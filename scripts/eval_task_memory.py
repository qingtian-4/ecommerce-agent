"""Run the frozen task conversations through the shared streaming service; preserve every turn."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.chat_service import ChatService  # noqa: E402
from acs.config import get_settings  # noqa: E402


def state_check(case_id: str, turns: list[dict]) -> bool:
    last = turns[-1]
    task = last["task_view"]["task"]
    if any(t["result"]["stop_reason"] != "answered" for t in turns):
        return False
    if case_id == "ambiguous-order":
        return task is None and any(e["label"] == "list_orders" and e["type"] == "tool_result"
                                        for e in last["result"]["trace"])
    if case_id == "ambiguous-item":
        return bool(task and task["order_id"] == "O10024" and task["item_id"] is None)
    if not task:
        return False
    if case_id == "correct-order":
        old = turns[0]["task_view"]["task"]
        return bool(old and task["revision"] > old["revision"] and task["order_id"] == "O10027"
                    and all(task[f] is None for f in (
                        "item_id", "quantity", "reason", "condition",
                    )))
    if case_id == "cancel-draft":
        return task["status"] == "cancelled" and task["order_id"] is None
    items = (last["task_view"]["order"] or {}).get("items", [])
    item = next((i for i in items if i["product_id"] == "P001"), {})
    expected = (task["order_id"] == "O10024" and task["item_id"] == item.get("id")
                and task["quantity"] == 1)
    if case_id == "remember-after-digression":
        first = turns[0]["task_view"]["task"]
        middle = turns[1]["task_view"]["task"]
        return bool(expected and task["reason"] == "no_reason" and task["condition"] == "sealed"
                    and first == middle)
    if case_id == "invalid-quantity":
        return expected and any("数量" in e for e in last["result"]["errors"])
    raise ValueError(case_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("results/task_memory/online"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    raw_path = args.out / "per_case.jsonl"
    cases_path = Path("data/eval/task_memory_cases.json")
    cases = json.loads(cases_path.read_text())["online_cases"]
    # Exclusive creation keeps failures and earlier attempts instead of overwriting them.
    with raw_path.open("x") as raw, TemporaryDirectory(prefix="acs-task-eval-") as temp:
        db_path = Path(temp) / "business.db"
        with closing(sqlite3.connect(get_settings().db_path)) as src, closing(
            sqlite3.connect(db_path)
        ) as dst:
            src.backup(dst)
        original = get_settings()
        service = ChatService(original.model_copy(update={"db_path": db_path}))
        results = []
        for case in cases:
            sid, turns = None, []
            for text in case["turns"]:
                for update in service.run_turn(text, case["user_id"], sid):
                    sid = update.session_id
                store = service.store
                snapshot = store.load(sid, case["user_id"])
                result = snapshot["last_result"]
                turns.append({"query": text, "result": result,
                              "task_view": store.get_return_task(sid,
                                                                 case["user_id"])})
            record = {"id": case["id"], "user_id": case["user_id"], "turns": turns,
                      "state_check_passed": state_check(case["id"], turns),
                      "manual_answer_review": "pending", "criterion": case["check"]}
            raw.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw.flush()
            results.append(record)
            print(f"{case['id']}: state_check={record['state_check_passed']}", flush=True)
        summary = {"cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                   "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                   "model": original.llm_model, "temperature": original.llm_temperature,
                   "thinking": original.llm_thinking, "retrieval_mode": original.retrieval_mode,
                   "reranker_enabled": original.reranker_enabled,
                   "evidence_backfill": original.evidence_backfill,
                   "cases": len(results), "turns": sum(len(r["turns"]) for r in results),
                   "state_checks_passed": sum(r["state_check_passed"] for r in results),
                   "implementation_sha256": {
                       f: hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in (
                           "src/acs/chat_service.py", "src/acs/context.py", "src/acs/runtime.py",
                       "src/acs/async_transport.py", "src/acs/llm.py",
                       "src/acs/service_agent.py", "src/acs/cs_tools.py",
                           "src/acs/sessions.py", "scripts/eval_task_memory.py",
                       )
                   },
                   "boundary": "单次真实模型草稿对话，回答需人工复核；不是提交或端到端成功率"}
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        )


if __name__ == "__main__":
    main()
