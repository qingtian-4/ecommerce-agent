"""Paired development probe: identical long history/task, two character budgets.

The relaxed arm approximates the old unbounded context policy on this fixture;
it is not an old-commit quality benchmark or a production performance claim.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.chat_service import ChatService  # noqa: E402
from acs.config import get_settings  # noqa: E402
from acs.database import BusinessDB  # noqa: E402
from acs.scenarios import scenario_settings, sha256  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def fixture(settings) -> tuple[str, str, dict]:
    store = SessionStore(settings.db_path)
    sid, uid = store.create("USER001"), "USER001"
    order = BusinessDB(settings).get_order("S20011", uid)
    item = next(i for i in order["items"] if i["quantity"] == 2)
    task = {"order_id": order["order_id"], "item_id": item["id"], "quantity": 2,
            "reason": "no_reason", "condition": "sealed"}
    text = "[合成长上下文场景] 记录本人订单中的2件商品退货草稿，未提交。"
    tid = store.begin_turn(sid, uid, text)
    store.update_return_task(sid, uid, tid, task)
    answer = "合成草稿已记录，未判断资格或提交。"
    store.finish_turn(sid, uid, tid, [{"role": "user", "content": text},
                                    {"role": "assistant", "content": answer}], {"answer": answer})
    for n in range(18):
        text = f"历史轮次{n}。" + (
            "这是合成的历史长度测试文本，不提供新的业务事实，也不修改已有草稿。" * 60
        )
        tid = store.begin_turn(sid, uid, text)
        store.finish_turn(sid, uid, tid, [{"role": "user", "content": text},
                                        {"role": "assistant", "content": "已收到，不改变草稿。"}],
                          {"answer": "已收到，不改变草稿。"})
    return sid, uid, task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("data/scenarios/v2"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    settings = scenario_settings(get_settings(), args.scenario)
    query = "继续我的退货草稿，数量改成1，其他不变。只记录，不查询资格，不提交。"
    with TemporaryDirectory(prefix="acs-context-") as temp:
        source = Path(temp) / "source.db"
        with (closing(sqlite3.connect(settings.db_path)) as src,
              closing(sqlite3.connect(source)) as dst):
            src.backup(dst)
        sid, uid, expected = fixture(settings.model_copy(update={"db_path": source}))
        manifest = {"query": query, "expected_task": expected | {"quantity": 1},
                    "history_turns": 19, "arms": {"relaxed": 100000, "bounded": 24000},
                    "model": settings.llm_model, "temperature": settings.llm_temperature,
                    "thinking": settings.llm_thinking,
                    "fixture_sha256": sha256(source),
                    "implementation_sha256": {str(p): sha256(p) for p in sorted(
                        Path("src/acs").glob("*.py")
                    )}, "script_sha256": sha256(Path(__file__))}
        (args.out / "frozen_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        records = []
        for label, limit in manifest["arms"].items():
            path = Path(temp) / (label + ".db")
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(path)) as dst:
                src.backup(dst)
            service = ChatService(settings.model_copy(update={"db_path": path,
                                                              "context_max_chars": limit}))
            updates = list(service.run_turn(query, uid, sid))
            final = updates[-1]
            task = service.store.get_return_task(sid, uid)["task"]
            result = asdict(final.result)
            record = {"arm": label, "result": result, "elapsed_ms": final.elapsed_ms,
                      "saved_messages": len(service.store.load(sid, uid)["history"]),
                      "task_check": bool(task and all(task[k] == v for k, v in (
                          expected | {"quantity": 1}
                      ).items()) and task["status"] == "draft"), "task": task}
            (args.out / (label + ".json")).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            )
            records.append(record)
            print(json.dumps({k: record[k] for k in ("arm", "task_check", "elapsed_ms")}),
                  flush=True)
        summary = {"arms": [{"arm": r["arm"], "task_check": r["task_check"],
                             "stop_reason": r["result"]["stop_reason"],
                             "context": r["result"]["totals"].get("context"),
                             "prompt_tokens": r["result"]["totals"].get("prompt_tokens"),
                             "total_tokens": r["result"]["totals"].get("total_tokens"),
                             "api_requests": r["result"]["totals"].get("calls"),
                             "saved_messages": r["saved_messages"]} for r in records],
                   "boundary": "单个合成长历史开发探针，每方案1次，不代表泛化或耗时分位数。"}
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        )


if __name__ == "__main__":
    main()
