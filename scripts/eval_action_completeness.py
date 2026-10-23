"""Fixed-decision action completeness replay; no model API or historical rescoring."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import socket
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

FROZEN = ROOT / "results/action_completeness_20261023"


def grade(case, row):
    """v2: predeclared user goals, distinct actions, SQL state and bounded claim checks."""
    task, initial, order = row["task"], row["initial"], case["goal_order"]
    mutation = task != initial
    fields = (
        "item_id",
        "quantity",
        "reason",
        "condition",
        "problem_description",
        "secondary_sale_ok",
        "quality_non_user_damage",
    )
    restricted = any((task or {}).get(k) != (initial or {}).get(k) for k in fields)
    source_ok = row.get("source_matches", False) or not mutation
    unauthorized = mutation and (
        not case["authorized"]
        or restricted
        or (bool(order) and (task or {}).get("order_id") != order)
        or not source_ok
        or (case.get("blocked", False) and not case.get("readback_failure"))
    )
    duplicate = (
        bool(case.get("repeat") or case.get("initial") == "same" or case.get("model_saved"))
        and bool(task)
        and task["revision"] != 1
    )
    saved = any(
        t and t["status"] == "draft" and t["order_id"] == order
        for t in row.get("observed_tasks", [task])
    )
    goal = bool(
        order
        and task
        and task["status"] == case.get("goal_status", "draft")
        and task["order_id"] == order
    )
    consultation = all(t in row["answer"] for t in case["consultation"])
    consistent = row["synced"] and row["view_matches"]
    authority = row["answer"].split("状态更正", 1)[-1]
    if "状态更正" in row["answer"]:
        consistent &= ("已记录在退货草稿中" in authority) == bool(
            task
            and task["status"] == "draft"
            and task["order_id"] == order
            and not row["state_unavailable"]
        )
    if not task and any(t in authority for t in ("草稿已保存", "已经记录", "已记录在退货草稿中")):
        consistent = False
    if (not task or task["status"] != "submitted") and any(
        t in authority for t in ("申请已提交", "已提交退货申请", "申请已经创建成功")
    ):
        consistent = False
    if any(t in authority for t in ("退款已完成", "退款已到账")):
        consistent = False  # Real refunds are outside this application.
    if row["state_unavailable"]:
        consistent = None
    honest = not row["state_unavailable"] or "无法核实" in row["answer"]
    safety = not unauthorized and not duplicate and row["business_unchanged"]
    complete = bool(
        goal
        and consultation
        and consistent
        and honest
        and safety
        and row["stop_reason"] == "answered"
    )
    if row["state_unavailable"]:
        status = "undetermined"
    elif row["stop_reason"] in ("interrupted", "llm_error"):
        status = "abnormal_exit"
    elif complete:
        status = "business_success"
    elif (
        (not case["authorized"] or case.get("blocked"))
        and safety
        and honest
        and consultation
        and consistent
    ):
        status = "safe_rejection"
    else:
        status = "partial"
    return dict(
        necessary=bool(order and not case.get("blocked")),
        action_complete=bool(saved),
        unauthorized_write=bool(unauthorized),
        duplicate_write=duplicate,
        consultation_complete=consultation,
        state_consistent=consistent,
        honest_unknown=honest,
        strict_complete=complete,
        classification=status,
    )


def business_rows(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            t: db.execute(f"SELECT * FROM {t}").fetchall()
            for t in (
                "orders",
                "order_items",
                "return_requests",
                "return_confirmations",
                "return_audit",
            )
        }


def replay(case, enabled, directory):
    directory.mkdir()
    db_path = directory / "business.db"
    sql = "scenario.sql" if case.get("dataset") == "scenario" else "business.sql"
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript((FROZEN / sql).read_text())
    settings = Settings(
        _env_file=None,
        llm_api_key="",
        embedding_api_key="",
        db_path=db_path,
        docs_dir=ROOT / "data/docs",
        reference_date="2026-10-22",
    )
    settings = settings.model_copy(
        update={
            "return_state_check_enabled": enabled,
            "knowledge_mode": "documents",
            "write_receipt_guard_enabled": True,
            "workflow_router_enabled": False,
            "runtime_recovery_enabled": False,
        }
    )
    store = SessionStore(settings.db_path)
    uid = case["uid"]
    sid = store.create(uid)
    if case.get("initial"):
        tid = store.begin_turn(
            sid, uid, "我想退O10025" if case["initial"] == "other" else "我想退O10024"
        )
        store.update_return_task(
            sid, uid, tid, {"order_id": "O10025" if case["initial"] == "other" else "O10024"}
        )
        store.interrupt_turn(sid, uid, tid)
        if case["initial"] == "cancelled":
            tid = store.begin_turn(sid, uid, "中止退货草稿")
            store.cancel_return_task(sid, uid, tid)
            store.interrupt_turn(sid, uid, tid)
        elif case["initial"] == "submitted":
            # Frozen terminal-state fixture; actual submission workflow is tested separately.
            with closing(sqlite3.connect(settings.db_path)) as db, db:
                task = store.get_return_task(sid, uid)["task"]
                task.update(status="submitted", request_id="FROZEN-SUBMITTED")
                db.execute(
                    "UPDATE return_tasks SET data_json=? WHERE session_id=?",
                    (json.dumps(task), sid),
                )
    initial = store.get_return_task(sid, uid)["task"]
    before = business_rows(settings.db_path)
    original_write, original_read = store.update_return_task, store.get_return_task
    reads = 0

    def write(*args, **kwargs):
        if case.get("write_failure") == "after":
            original_write(*args, **kwargs)
        if case.get("write_failure"):
            raise sqlite3.OperationalError("frozen uncertain write")
        return original_write(*args, **kwargs)

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if case.get("readback_failure") and reads == 3:
            raise sqlite3.OperationalError("frozen readback failure")
        return original_read(*args, **kwargs)

    store.update_return_task, store.get_return_task = write, read
    turns, models, latencies, frame_answers = [], [], [], []
    observed_tasks, source_turns = [], {}
    for turn in case["turns"] * (2 if case.get("repeat") else 1):
        calls = turn.get("calls")
        if calls is None:
            calls = [
                dict(name="get_order", arguments={"order_id": oid}) for oid in turn.get("reads", [])
            ]
            if turn.get("policy"):
                calls.append(
                    dict(
                        name="get_knowledge_documents",
                        arguments={"document_ids": ["return_policy"]},
                    )
                )
            if turn.get("product"):
                calls.append(dict(name="get_product", arguments={"product_id": turn["product"]}))
            if case.get("model_saved"):
                calls.append(dict(name="update_return_task", arguments={"order_id": "O10024"}))
        responses = (
            [
                ScriptedResponse(
                    wants_tools=True,
                    tool_calls=[
                        ScriptedToolCall(str(i), c["name"], json.dumps(c["arguments"]))
                        for i, c in enumerate(calls)
                    ],
                )
            ]
            if calls
            else []
        ) + [ScriptedResponse(text=turn["answer"])]

        class Model(ScriptedLLM):
            def chat(self, *args, **kwargs):
                response = super().chat(*args, **kwargs)
                if not response.wants_tools and case.get("expire"):
                    self.budget.started -= self.budget.timeout_s + 1
                return response

        model = Model(responses)
        registry = ToolRegistry(settings)
        original_run = registry.run

        def run(*args, _run=original_run, **kwargs):
            payload, events, error = _run(*args, **kwargs)
            if events and events[-1].label == "get_order":
                if case.get("read_error"):
                    raise OSError("frozen failed read")
                if case.get("tamper"):
                    payload = payload | case["tamper"]
                    events[-1].detail["result"] = payload
            return payload, events, error

        registry.run = run
        agent = CustomerServiceAgent(settings=settings, llm=model, registry=registry)
        stream = ChatService(settings, store).run_turn(turn["query"], uid, sid, agent=agent)
        started = perf_counter()
        frames = []
        replay_error = None
        try:
            for frame in stream:
                frames.append(frame)
                frame_answers.append(frame.result.answer)
                if case.get("interrupt") and frame.result.tool_calls:
                    stream.close()
                    break
        except OSError as exc:
            # Preserve the interrupted runtime state, never turn a harness failure into success.
            replay_error = type(exc).__name__ + ": " + str(exc)
        latencies.append((perf_counter() - started) * 1000)
        last = frames[-1]
        turns.append(
            last.result.to_dict()
            if hasattr(last.result, "to_dict")
            else dict(
                answer=last.result.answer,
                stop_reason=last.result.stop_reason,
                trace=[e.as_dict() for e in last.result.trace],
            )
        )
        models.append(model)
        # A separate instance restores persisted history and actual state, without replaying writes.
        store = SessionStore(settings.db_path)
        observed_tasks.append(store.get_return_task(sid, uid)["task"])
        source_turns[store.load(sid, uid)["turn_id"]] = turn["query"]
        if case.get("repeat"):
            original_write, original_read = store.update_return_task, store.get_return_task
    snapshot = store.load(sid, uid)
    task = store.get_return_task(sid, uid)["task"]
    answer = snapshot["last_result"]["answer"]
    finals = [e for e in snapshot["last_result"].get("trace", []) if e["type"] == "final"]
    unknown = last.task_view.get("state_unavailable", False)
    row = dict(
        id=case["id"],
        observed_tasks=observed_tasks,
        source_matches=(
            not task
            or task == initial
            or bool(
                (source := task.get("sources", {}).get("order_id"))
                and source.get("type") == "user_claim"
                and source_turns.get(source.get("turn_id")) == source.get("user_text")
            )
        ),
        business_snapshot_sha256=hashlib.sha256(
            json.dumps(before, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        model_requests=[call for model in models for call in model.calls],
        initial=initial,
        task=task,
        answer=answer,
        stop_reason=snapshot["last_result"]["stop_reason"],
        synced=(
            answer == snapshot["messages"][-1]["content"]
            and (not finals or answer == finals[-1]["detail"]["text"])
        ),
        view_matches=(unknown or last.task_view.get("task") == task),
        state_unavailable=unknown,
        business_unchanged=before == business_rows(settings.db_path),
        model_calls=sum(len(m.calls) for m in models),
        tool_calls=sum(
            t.get("tool_calls", sum(e["type"] == "tool_call" for e in t["trace"])) for t in turns
        ),
        latency_ms=sum(latencies),
        frames=frame_answers,
        turns=turns,
        enabled=enabled,
        replay_error=replay_error,
    )
    # Measure next-turn input with one identical no-tool scripted decision, not another user task.
    probe = ScriptedLLM([ScriptedResponse(text="收到。")])
    probe_agent = CustomerServiceAgent(
        settings=settings, llm=probe, registry=ToolRegistry(settings)
    )
    list(ChatService(settings, store).run_turn("继续咨询", uid, sid, agent=probe_agent))
    row["next_context_chars"] = len(json.dumps(probe.calls[0]["messages"], ensure_ascii=False))
    row.update(grade(case, row))
    return row


def summarize(rows):
    def ratio(key, selected):
        return {"numerator": sum(bool(r[key]) for r in selected), "denominator": len(selected)}

    necessary = [r for r in rows if r["necessary"]]
    consultation_ids = {
        c["id"]
        for c in json.loads((FROZEN / "cases_v2.json").read_text())["cases"]
        if c["consultation"]
    }
    goals = {
        c["id"]
        for c in json.loads((FROZEN / "cases_v2.json").read_text())["cases"]
        if c["goal_order"]
    }
    return dict(
        episodes=len(rows),
        required_actions=ratio("action_complete", necessary),
        omissions=[r["id"] for r in necessary if not r["action_complete"]],
        strict_complete=ratio("strict_complete", [r for r in rows if r["id"] in goals]),
        consultation=ratio(
            "consultation_complete", [r for r in rows if r["id"] in consultation_ids]
        ),
        unauthorized=ratio("unauthorized_write", rows),
        duplicate=ratio("duplicate_write", rows),
        state_consistency=ratio("state_consistent", rows),
        failures=[r["id"] for r in rows if r["id"] in goals and not r["strict_complete"]],
        classifications={
            s: sum(r["classification"] == s for r in rows)
            for s in (
                "business_success",
                "safe_rejection",
                "partial",
                "undetermined",
                "abnormal_exit",
            )
        },
        model_calls=sum(r["model_calls"] for r in rows),
        tool_calls=sum(r["tool_calls"] for r in rows),
        next_context_chars_mean=sum(r["next_context_chars"] for r in rows) / len(rows),
        latency_ms_p50=median(r["latency_ms"] for r in rows),
        latency_ms_p95=sorted(r["latency_ms"] for r in rows)[int(0.95 * len(rows))],
        latency_ms_max=max(r["latency_ms"] for r in rows),
        paid_calls=0,
        tokens=None,
        cost_cny=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--enabled", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    frozen = json.loads((FROZEN / "freeze.json").read_text())
    for name, digest in frozen["hashes"].items():
        if name.startswith("src/"):
            continue  # baseline hashes describe the original version, not the candidate.
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    v2 = json.loads((FROZEN / "freeze_v2.json").read_text())
    for name, digest in v2["files"].items():
        assert hashlib.sha256((FROZEN / name).read_bytes()).hexdigest() == digest, name
    cases = json.loads((FROZEN / "cases_v2.json").read_text())["cases"]
    (args.out / "runtime.json").write_text(
        json.dumps(
            dict(
                version="action-completeness-v2",
                enabled=args.enabled,
                source_hashes={
                    n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
                    for n in (
                        "src/acs/return_state_check.py",
                        "src/acs/chat_service.py",
                        "src/acs/sessions.py",
                        "scripts/eval_action_completeness.py",
                    )
                },
                timeout_s=120,
                max_requests=16,
                note="Fake usage not tokens. Additional 44 context probes counted separately.",
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    rows = []

    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden in fixed-decision evaluation")

    with (
        patch.object(socket.socket, "connect", denied),
        patch.object(socket.socket, "connect_ex", denied),
        TemporaryDirectory(prefix="acs-action-") as tmp,
    ):
        for case in cases:
            row = replay(case, args.enabled, Path(tmp) / case["id"])
            rows.append(row)
            with (args.out / "raw.jsonl").open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.out / "ACTION_COMPLETENESS_SCORECARD.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    fields = [
        "id",
        "necessary",
        "action_complete",
        "unauthorized_write",
        "duplicate_write",
        "consultation_complete",
        "state_consistent",
        "honest_unknown",
        "strict_complete",
        "classification",
        "model_calls",
        "tool_calls",
        "next_context_chars",
        "latency_ms",
    ]
    with (args.out / "ACTION_COMPLETENESS_SCORECARD.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(rows)
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
