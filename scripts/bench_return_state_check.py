"""Offline P0 latency attribution; production SQLite durability settings are unchanged."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

STARTED = perf_counter()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs import return_state_check as checker  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.cs_tools import TraceEvent  # noqa: E402
from acs.database import BusinessDB  # noqa: E402
from acs.runtime import RunBudget  # noqa: E402
from acs.scenarios import build_scenario  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

CASES = ("not_applicable", "blocked", "satisfied", "repaired", "repaired_readback", "contention")
LOCAL = threading.local()


def ms(start: float) -> float:
    return (perf_counter() - start) * 1000


def match(*args, **kwargs):
    start = perf_counter()
    result = re.fullmatch(*args, **kwargs)
    LOCAL.metrics["intent_ms"] += ms(start)
    return result


def instrument(store, metrics, barrier, stack):
    """Observe C SQLite calls/context exit, without changing the SQL or commit policy."""
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "BEGIN IMMEDIATE":
                if barrier is not None:
                    start = perf_counter()
                    barrier.wait(timeout=20)
                    metrics["coordination_ms"] += ms(start)
                start = perf_counter()
                try:
                    return super().execute(sql, *args, **kwargs)
                finally:
                    metrics["write_lock_ms"] += ms(start)
            return super().execute(sql, *args, **kwargs)

        def __exit__(self, exc_type, exc_value, traceback):
            start = perf_counter()
            try:
                return super().__exit__(exc_type, exc_value, traceback)
            finally:
                metrics["commit_ms" if exc_type is None else "rollback_ms"] += ms(start)

    def connect():
        start = perf_counter()
        conn = sqlite3.connect(store.db_path, factory=Connection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if metrics["phase"] == "write":
            metrics["write_connect_ms"] += ms(start)
        return conn

    original_read, original_write = store.get_return_task, store.update_return_task
    def read(*args, **kwargs):
        start = perf_counter()
        try:
            return original_read(*args, **kwargs)
        finally:
            metrics["readback_ms" if metrics["phase"] == "readback" else
                    "initial_read_ms"] += ms(start)
    def write(*args, **kwargs):
        metrics["phase"] = "write"
        start = perf_counter()
        try:
            return original_write(*args, **kwargs)
        finally:
            metrics["write_total_ms"] += ms(start)
            metrics["phase"] = "guard"
    stack.enter_context(patch.object(store, "_connect", connect))
    stack.enter_context(patch.object(store, "get_return_task", read))
    stack.enter_context(patch.object(store, "update_return_task", write))


def measure(store, sid, tid, case, trace, barrier=None, *, timed=True):
    metrics = defaultdict(float, phase="guard")
    LOCAL.metrics = metrics
    message = "只查询S20017，不办理退货。" if case == "not_applicable" else "我想退S20017。"
    with ExitStack() as stack:
        if timed:
            instrument(store, metrics, barrier, stack)
        start = perf_counter()
        event = checker.check_order_draft(store, sid, "USER001", tid, message,
                                          [] if case == "blocked" else trace,
                                          "answered", RunBudget(120, 16))
        metrics["guard_ms"] = ms(start)
        if case in {"repaired_readback", "contention"}:
            metrics["phase"] = "readback"
            view = store.get_return_task(sid, "USER001")
            assert view["task"]["order_id"] == "S20017"
        metrics["total_ms"] = ms(start)
    task = store.get_return_task(sid, "USER001")["task"]
    status = event.detail["status"] if event else "not_applicable"
    expected = {"not_applicable": "not_applicable", "blocked": "blocked",
                "satisfied": "satisfied", "repaired": "repaired",
                "repaired_readback": "repaired"}
    assert status in {"repaired", "satisfied"} if case == "contention" else status == expected[case]
    assert (task is None) == (case in {"not_applicable", "blocked"})
    if task:
        assert task["revision"] == 1 and task["item_id"] is None
    metrics["conditional_write_ms"] = max(0, metrics["write_total_ms"] - sum(
        metrics[k] for k in ("write_lock_ms", "write_connect_ms", "commit_ms", "coordination_ms")
    ))
    return {"status": status, **{k: round(v, 6) for k, v in metrics.items() if k != "phase"}}


def one(store, case, trace):
    start = perf_counter()
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    if case == "satisfied":
        store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"})
    setup_ms = ms(start)
    if case == "contention":
        stores = [SessionStore(store.db_path) for _ in range(4)]
        barrier = threading.Barrier(4)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda s: measure(s, sid, tid, case, trace, barrier), stores))
        assert sum(r["status"] == "repaired" for r in results) == 1
    else:
        results = [measure(store, sid, tid, case, trace)]
    for result in results:
        result["session_setup_ms"] = setup_ms
    return results


def database(path):
    store = SessionStore(path)
    settings = Settings(_env_file=None, db_path=path, llm_api_key="", embedding_api_key="",
                        reference_date=date(2026, 10, 15))
    order = BusinessDB(settings).get_order("S20017", "USER001")
    trace = [TraceEvent("tool_call", "get_order", {"arguments": '{"order_id":"S20017"}'}),
             TraceEvent("tool_result", "get_order", {"result": order})]
    return store, trace


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "p50": values[math.ceil(.5 * len(values)) - 1],
            "p95": values[math.ceil(.95 * len(values)) - 1], "max": values[-1]}


def run(output, cold, warm, warmup):
    output.mkdir(parents=True, exist_ok=False)
    rows, failures = [], []
    # Seal sample counts before any measurement and preserve all failures.
    plan = {"cold": cold, "warm": warm, "warmup": warmup, "cases": CASES,
            "percentile": "nearest-rank", "threads": 4, "sqlite_modified": False,
            "cold_definition": "fresh process/DB copy; OS cache not flushed; setup separate",
            "warm_definition": "same process/DB after warmups; SQLite connections still fresh"}
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    with TemporaryDirectory(prefix="acs-p0-bench-") as temp:
        root = Path(temp)
        build_scenario(root / "source")
        source = root / "source/business.db"
        with closing(SessionStore(source)._connect()) as conn:
            sqlite = {name: conn.execute("PRAGMA " + name).fetchone()[0] for name in (
                "journal_mode", "synchronous", "busy_timeout", "page_size", "foreign_keys",
            )}
        for case in CASES:
            for index in range(cold):
                start = perf_counter()
                try:
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                            "--worker", case, "--source", str(source)],
                                           capture_output=True, text=True, timeout=60)
                    wall = ms(start)
                    assert child.returncode == 0, child.stderr
                    records = json.loads(child.stdout)
                    for row in records:
                        rows.append(row | {"case": case, "regime": "cold", "sample": index,
                                           "process_wall_ms": wall})
                except Exception as exc:
                    failures.append({"case": case, "regime": "cold", "sample": index,
                                     "error": str(exc)})
            path = root / (case + ".db")
            shutil.copyfile(source, path)
            store, trace = database(path)
            for index in range(warmup + warm):
                regime = "warmup" if index < warmup else "warm"
                try:
                    for row in one(store, case, trace):
                        rows.append(row | {"case": case, "regime": regime, "sample": index})
                except Exception as exc:
                    failures.append({"case": case, "regime": regime, "sample": index,
                                     "error": str(exc)})
    (output / "raw.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = {"hardware": platform.platform(), "cpu_count": os.cpu_count(),
               "cpu_model": next(line.split(":", 1)[1].strip() for line in
                                 Path('/proc/cpuinfo').read_text().splitlines()
                                 if line.startswith('model name')),
               "python": platform.python_version(),
               "sqlite_version": sqlite3.sqlite_version, "sqlite": sqlite,
               "failures": failures, "groups": []}
    for case in CASES:
        for regime in ("cold", "warm"):
            group = [r for r in rows if r["case"] == case and r["regime"] == regime]
            keys = sorted({k for r in group for k in r if k.endswith("_ms")})
            summary["groups"].append({"case": case, "regime": regime,
                                      "trials": len({r["sample"] for r in group}),
                                      "operations": len(group),
                                      "statuses": dict(Counter(r["status"] for r in group)),
                                      "metrics": {k: distribution([r.get(k, 0) for r in group])
                                                  for k in keys}})
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert not failures, "Benchmark failures preserved in summary.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cold", type=int, default=10)
    parser.add_argument("--warm", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--worker", choices=CASES)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in P0 microbenchmarks")
    with patch.object(socket.socket, "connect", denied), patch.object(
        socket.socket, "connect_ex", denied,
    ), patch.object(checker, "re", SimpleNamespace(fullmatch=match)):
        if args.worker:
            with TemporaryDirectory(prefix="acs-p0-cold-") as temporary:
                db = Path(temporary) / "business.db"
                shutil.copyfile(args.source, db)
                store, trace = database(db)
                start_ms = ms(STARTED)
                records = one(store, args.worker, trace)
                for record in records:
                    record["startup_before_session_ms"] = start_ms
                print(json.dumps(records))
        else:
            if args.out is None or min(args.cold, args.warm, args.warmup) < 1:
                parser.error("--out and positive sample counts are required")
            run(args.out, args.cold, args.warm, args.warmup)
