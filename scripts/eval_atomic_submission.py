"""M6 fixed-model E2E; every application action runs in a fresh CLI process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "return_tasks",
    "return_previews",
    "return_requests",
    "return_confirmations",
    "return_audit",
    "return_reviews",
    "local_confirmation_waits",
    "local_submission_receipts",
)


def snapshot(root):
    with closing(sqlite3.connect(root / "business.sqlite")) as conn, conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
            if t in tables
            else []
            for t in TABLES
        }


def run(out):
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    commands = []

    def cli(root, *args):
        start = time.perf_counter()
        command = [
            sys.executable,
            str(ROOT / "scripts/local_formal_submission.py"),
            "--state-dir",
            str(root),
            *args,
        ]
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180)
        index = len(commands)
        (private / f"command-{index}.stdout").write_text(process.stdout)
        (private / f"command-{index}.stderr").write_text(process.stderr)
        commands.append(
            {
                "command": list(args),
                "returncode": process.returncode,
                "latency_ms": (time.perf_counter() - start) * 1000,
            }
        )
        if process.returncode:
            raise RuntimeError(f"CLI action {args[0]} failed; preserved evidence {index}")
        return json.loads(process.stdout)

    files = [
        ROOT / "scripts/local_formal_submission.py",
        Path(__file__),
        *sorted((ROOT / "src/acs").glob("*.py")),
        ROOT / "uv.lock",
    ]
    fingerprints = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    records = []
    try:
        for scenario in ("accept", "reject", "cancel", "expired"):
            root = private / scenario
            cli(root, "init")
            sid = cli(root, "new")["session_id"]
            before = snapshot(root)
            with (
                closing(sqlite3.connect(root / "business.sqlite")) as src,
                closing(sqlite3.connect(private / f"{scenario}-initial.sqlite")) as dst,
            ):
                src.backup(dst)
            stages = []
            for index in range(1, 9) if scenario == "accept" else [8]:
                reply = cli(root, "stage", sid, str(index))
                assert reply["type"] == "completed"
                business = reply["result"]["business_state"]
                task = business["task"]["task"]
                if index in (2, 8):
                    assert task["item_id"] == 4 and task["quantity"] == 1
                if index == 4:
                    assert task["item_id"] == 5 and business["preview"]["ready"]
                if index == 7:
                    assert task["status"] == "cancelled" and business["preview"] is None
                stages.append(
                    {
                        "stage": index,
                        "passed": True,
                        "revision": task["revision"],
                        "status": task["status"],
                        "preview": business["preview"],
                    }
                )
            waiting = cli(root, "open", sid)
            assert waiting["status"] == "waiting"
            wid = waiting["wait_id"]
            pre_submit = snapshot(root)
            assert pre_submit["return_requests"] == pre_submit["return_confirmations"] == []
            if scenario == "expired":
                with closing(sqlite3.connect(root / "business.sqlite")) as conn, conn:
                    conn.execute(
                        "UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'"
                    )
            result = cli(root, "decide", sid, wid, "accept" if scenario == "expired" else scenario)
            after = snapshot(root)
            with (
                closing(sqlite3.connect(root / "business.sqlite")) as src,
                closing(sqlite3.connect(private / f"{scenario}-final.sqlite")) as dst,
            ):
                src.backup(dst)
            expected = int(scenario == "accept")
            assert [
                len(after[t])
                for t in (
                    "return_requests",
                    "return_confirmations",
                    "return_audit",
                    "local_submission_receipts",
                )
            ] == [expected] * 4
            assert after["return_reviews"] == []
            if expected:
                assert result["request"]["status"] == "pending_review"
                assert cli(root, "lookup", sid, wid) == result
                assert cli(root, "inspect", sid)["requests"][0] == result["request"]
                duplicate = cli(root, "decide", sid, wid, "accept")
                assert duplicate["request"] == result["request"]
                assert snapshot(root) == after
            else:
                assert (
                    result["status"]
                    == {"reject": "rejected", "cancel": "cancelled", "expired": "invalidated"}[
                        scenario
                    ]
                )
            records.append(
                {
                    "scenario": scenario,
                    "passed": True,
                    "stages": stages,
                    "before": before,
                    "pre_submit": pre_submit,
                    "after": after,
                    "result": result,
                }
            )
    finally:
        score = {
            "kind": "offline-fixed-model-synthetic-not-model-quality",
            "source_base_sha": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_fingerprints": fingerprints,
            "source_unchanged": fingerprints
            == {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
            },
            "paid_requests": 0,
            "cost_cny": 0,
            "scenarios": records,
            "commands": commands,
            "completed_all": len(records) == 4 and all(r["passed"] for r in records),
            "private": "Fresh synthetic SQLite fixtures and CLI logs in ignored .private-evidence/",
        }
        (out / "scorecard.json").write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n")
    assert score["source_unchanged"], "Source changed during E2E; preserve this run as diagnostic"
    print(
        json.dumps(
            {
                "completed_all": score["completed_all"],
                "scenarios": len(records),
                "paid_requests": 0,
                "commands": len(commands),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
