"""Frozen real-model conversations plus application-controlled return actions."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402

from acs.returns import ReturnService  # noqa: E402


def table_counts(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (
            "return_requests", "return_confirmations", "return_audit",
        )}


def snapshot(state: dict, uid: str) -> dict:
    store = app.session_store()
    return {"result": store.load(state["session_id"], uid)["last_result"],
            "task_view": store.get_return_task(state["session_id"], uid)}


def run_case(case: dict) -> dict:
    state, turns, actions = {}, [], []
    uid = case["user_id"]
    for query in case["turns"]:
        for _update in app.on_send(query, [], state, uid):
            pass
        turns.append({"query": query} | snapshot(state, uid))
    initial = table_counts(app.SETTINGS.db_path)
    declarations = case.get("ui_declarations", {})
    secondary = declarations.get("secondary_sale_ok", False)
    non_user_damage = declarations.get("quality_non_user_damage", False)
    task = app.session_store().get_return_task(state["session_id"], uid)["task"]
    if task and declarations:
        app.on_return_declaration(state, uid, secondary, non_user_damage)
        actions.append({"action": "declarations"} | snapshot(state, uid))
    if case.get("synthetic_upload"):
        output = io.BytesIO()
        Image.new("RGB", (4, 4), "white").save(output, format="PNG")
        app.on_return_upload(output.getvalue(), state, uid)
        actions.append({"action": "synthetic_format_test_upload"} | snapshot(state, uid))
    if task:
        app.on_return_preview(state, uid, secondary, non_user_damage)
        actions.append({"action": "preview"} | snapshot(state, uid))
    service = ReturnService(app.SETTINGS, app.session_store())
    preview = service.latest_preview(state["session_id"], uid)
    before = table_counts(app.SETTINGS.db_path)
    if case["confirm"] and preview:
        for action in ("explicit_confirm", "replay_same_confirm"):
            app.on_return_confirm(preview["preview_id"], state, uid)
            actions.append({"action": action} | snapshot(state, uid))
    after = table_counts(app.SETTINGS.db_path)
    requests = service.list_requests(uid)
    task = app.session_store().get_return_task(state["session_id"], uid)["task"]
    no_side_effect = all(v == 0 for v in initial.values()) and all(v == 0 for v in before.values())
    if case["confirm"]:
        expected_order = "O10024" if case["id"] == "no-reason-confirm" else "O10027"
        expected_item = app.BusinessDB(app.SETTINGS).get_order(expected_order, uid)["items"]
        item = next(i for i in expected_item if i["product_id"] == "P001")
        passed = bool(no_side_effect and preview and all(v == 1 for v in after.values())
                      and len(requests) == 1 and requests[0]["status"] == "pending_review"
                      and requests[0]["item_id"] == item["id"] and requests[0]["quantity"] == 1)
    else:
        passed = no_side_effect and all(v == 0 for v in after.values())
        if case["id"] == "quality-missing-proof":
            passed = bool(passed and task and task["reason"] == "quality" and preview is None
                          and "quality_evidence" in actions[-1]["task_view"]["missing_fields"])
    passed = passed and all(t["result"]["stop_reason"] == "answered" for t in turns)
    return {"id": case["id"], "criterion": case["expected"], "turns": turns,
            "actions": actions, "initial_counts": initial, "before_confirm_counts": before,
            "after_counts": after, "preview": preview, "requests": requests,
            "state_check_passed": passed, "manual_answer_review": "pending"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("results/return_submission/online"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases_path = Path("data/eval/return_submission_cases.json")
    cases = json.loads(cases_path.read_text())
    frozen = json.loads(Path("results/return_submission/frozen_manifest.json").read_text())
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == frozen["cases_sha256"]
    assert hashlib.sha256(Path("data/docs/return_policy.md").read_bytes()).hexdigest() == frozen[
        "policy_sha256"
    ]
    original = app.SETTINGS
    raw_path = args.out / "per_case.jsonl"
    results = []
    with raw_path.open("x") as raw, TemporaryDirectory(prefix="acs-return-eval-") as temp:
        try:
            for case in cases["online_cases"]:
                path = Path(temp) / (case["id"] + ".db")
                with closing(sqlite3.connect(original.db_path)) as src, closing(
                    sqlite3.connect(path)
                ) as dst:
                    src.backup(dst)
                    for table, expected in frozen["business_fingerprints"].items():
                        facts = dst.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                        digest = hashlib.sha256(json.dumps(
                            facts, ensure_ascii=False, sort_keys=True,
                        ).encode()).hexdigest()
                        assert digest == expected
                    # Only the temporary benchmark copy is reset; business facts stay untouched.
                    for table in ("return_reviews", "return_audit", "return_requests",
                                  "return_confirmations",
                                  "return_previews", "return_evidence", "return_tasks",
                                  "conversation_turns", "conversations"):
                        if dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                       (table,)).fetchone():
                            dst.execute(f"DELETE FROM {table}")
                    dst.commit()
                app.SETTINGS = original.model_copy(update={
                    "db_path": path, "reference_date": date.fromisoformat(cases["clock"]),
                })
                record = run_case(case)
                raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                raw.flush()
                results.append(record)
                print(f"{case['id']}: state_check={record['state_check_passed']}", flush=True)
        finally:
            app.SETTINGS = original
    summary = {"cases_sha256": frozen["cases_sha256"], "policy_sha256": frozen["policy_sha256"],
               "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
               "model": original.llm_model, "temperature": original.llm_temperature,
               "thinking": original.llm_thinking, "retrieval_mode": original.retrieval_mode,
               "reranker_enabled": original.reranker_enabled,
               "evidence_backfill": original.evidence_backfill, "top_k": original.top_k,
               "clock": cases["clock"], "cases": len(results),
               "turns": sum(len(r["turns"]) for r in results),
               "state_checks_passed": sum(r["state_check_passed"] for r in results),
               "implementation_sha256": {f: hashlib.sha256(Path(f).read_bytes()).hexdigest()
                                         for f in ("app.py", "src/acs/returns.py",
                                                   "src/acs/sessions.py", "src/acs/cs_tools.py",
                                                   "src/acs/service_agent.py",
                                                   "src/acs/database.py",
                                                   "scripts/eval_return_submission.py")},
               "boundary": "单次模拟申请流程，回答需人工复核；不含管理员审核、退款或稳定性测量"}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
