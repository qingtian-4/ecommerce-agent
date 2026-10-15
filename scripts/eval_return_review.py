"""Frozen review/query development scenarios on copied facts and the real chat handler."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402

from acs.returns import ReturnService  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = Path("data/eval/return_review_cases.json")
    manifest = json.loads(Path("results/return_review/frozen_manifest.json").read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == manifest["cases_sha256"]
    cases = json.loads(source.read_text())
    original = app.SETTINGS
    with closing(sqlite3.connect(original.db_path)) as conn:
        for table, expected in manifest["business_fingerprints"].items():
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            assert hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()
                                  ).hexdigest() == expected
    for name, expected in manifest["knowledge_fingerprints"].items():
        assert hashlib.sha256((original.docs_dir / name).read_bytes()).hexdigest() == expected
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path("app.py"), Path("admin_app.py"), *sorted(Path("src/acs").glob("*.py")),
        Path("scripts/eval_return_review.py"),
    )}
    records = []
    raw_path = args.out / "per_case.jsonl"
    with raw_path.open("x") as raw, TemporaryDirectory(prefix="acs-review-eval-") as temp:
        try:
            for case in cases["dialogues"]:
                path = Path(temp) / (case["id"] + ".db")
                with (closing(sqlite3.connect(original.db_path)) as src,
                      closing(sqlite3.connect(path)) as dst):
                    src.backup(dst)
                app.SETTINGS = original.model_copy(update={
                    "db_path": path, "reference_date": date.fromisoformat(cases["clock"]),
                    "admin_username": "evaluation-reviewer",
                    "admin_password": SecretStr(secrets.token_urlsafe(32)),
                })
                service = ReturnService(app.SETTINGS, SessionStore(path))
                pending = service.list_for_review(reviewer="evaluation-reviewer")
                if not pending:
                    raise RuntimeError("评测需要冻结事实中的合成待审核申请，不能重置演示库制造数据")
                rid = pending[0]["request_id"]
                if case["decision"]:
                    service.review(rid, case["decision"], case["review_reason"],
                                   reviewer="evaluation-reviewer")
                before = service.get_for_review(rid, reviewer="evaluation-reviewer")
                state, started = {}, perf_counter()
                uid = case.get("user_id", "USER001")
                for _ in app.on_send(case["query"], [], state, uid):
                    pass
                wall = (perf_counter() - started) * 1000
                result = app.session_store().load(state["session_id"], uid)["last_result"]
                after = service.get_for_review(rid, reviewer="evaluation-reviewer")
                checks = {"answered": result["stop_reason"] == "answered",
                          "model_did_not_change_review_or_application": before == after}
                record = {"id": case["id"], "query": case["query"], "criteria": case["criteria"],
                          "expected_status": after["status"], "review": after["review"],
                          "checks": checks, "result": result, "handler_wall_ms": wall,
                          "manual_review": "pending"}
                records.append(record)
                raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                raw.flush()
                print(case["id"], checks, flush=True)
        finally:
            app.SETTINGS = original
    summary = {"cases_sha256": manifest["cases_sha256"],
               "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
               "implementation_sha256": hashes, "clock": cases["clock"],
               "model": original.llm_model, "temperature": original.llm_temperature,
               "thinking": original.llm_thinking, "knowledge_mode": original.knowledge_mode,
               "retrieval_mode": original.retrieval_mode,
               "reranker_enabled": original.reranker_enabled,
               "evidence_backfill": original.evidence_backfill,
               "dialogues": len(records),
               "state_checks_passed": sum(all(r["checks"].values()) for r in records),
               "boundary": "审核由可信参与方在临时副本执行；状态正确不等于回答正确；"
                           "单遍开发证据，须逐条复核"}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
