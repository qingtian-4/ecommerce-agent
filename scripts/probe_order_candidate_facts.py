"""Fixed-model comparison of free text and bound candidate rendering, without a network."""

from __future__ import annotations

import argparse
import html
import json
import shutil
import socket
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.conftest import ScriptedResponse, ScriptedToolCall  # noqa: E402
from tests.test_order_candidate_facts import replay  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    def denied(*args, **kwargs):
        raise AssertionError("No network in P1 paired probe")
    records = []
    with TemporaryDirectory(prefix="acs-p1-paired-") as temp, patch.object(
        socket.socket, "connect", denied,
    ), patch.object(socket.socket, "connect_ex", denied):
        source = Path(temp) / "source"
        build_scenario(source)
        for case in ("original", "changed-colour", "other-owner", "correct-model"):
            for enabled in (False, True):
                root = Path(temp) / f"{case}-{enabled}"
                shutil.copytree(source, root)
                settings = scenario_settings(Settings(
                    _env_file=None, llm_api_key="", embedding_api_key="",
                ), root)
                store = SessionStore(settings.db_path)
                with closing(store._connect()) as conn, conn:
                    if case == "changed-colour":
                        conn.execute("UPDATE product_skus SET color='蓝色' WHERE sku_id='V201-2'")
                    counts_before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                                     for t in ("return_requests", "return_confirmations",
                                               "return_audit", "return_tasks")}
                fingerprint = data_fingerprint(settings.db_path)
                uid = "USER002" if case == "other-owner" else "USER001"
                responses = None
                if case == "correct-model":
                    responses = [
                        ScriptedResponse(wants_tools=True, tool_calls=[
                            ScriptedToolCall("orders", "list_orders", "{}"),
                        ]), ScriptedResponse(text="S20008 白色；S20003 黑色。"),
                    ]
                final, agent = replay((settings, store), enabled, uid=uid, responses=responses)
                first_answer = final.result.answer
                input_chars = final.result.totals["model_input_chars"]
                event = next((e for e in final.result.trace if e.type == "fact_render"), None)
                if enabled:
                    assert event is not None
                    rows = event.detail["rows"]
                    assert len(rows) == 25
                    if uid == "USER001":
                        row = next(r for r in rows if r["order_id"] == "S20008")
                        assert row["color"] == ("蓝色" if case == "changed-colour" else "白色")
                    else:
                        assert not any(r["order_id"] in {"S20008", "S20003"} for r in rows)
                else:
                    rows = None
                    assert event is None
                # Keep model decisions fixed for the subsequent input-size comparison too.
                agent.llm._responses.append(ScriptedResponse(text="请查看订单候选中的明细编号。"))
                next_turn = list(ChatService(agent.settings, store).run_turn(
                    "明细编号在哪里查看？", uid, final.session_id, agent=agent,
                ))[-1]
                with closing(store._connect()) as conn:
                    counts_after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                                    for t in counts_before}
                assert counts_before == counts_after
                assert data_fingerprint(settings.db_path) == fingerprint
                assert store.get_return_task(final.session_id, uid)["task"] is None
                records.append({"case": case, "enabled": enabled, "user_id": uid,
                                "answer": first_answer, "rows": rows,
                                "calls": len(agent.llm.calls), "first_input_chars": input_chars,
                                "followup_input_chars":
                                    next_turn.result.totals["model_input_chars"],
                                "first_turn_elapsed_ms": final.elapsed_ms,
                                "business_counts_unchanged": True, "final_task": None})
    pairs = []
    for case in dict.fromkeys(r["case"] for r in records):
        off, on = [r for r in records if r["case"] == case]
        assert off["calls"] == on["calls"] == 3
        assert off["first_input_chars"] == on["first_input_chars"]
        pairs.append({"case": case, "extra_model_calls": 0,
                      "first_input_chars_delta": [b - a for a, b in zip(
                          off["first_input_chars"], on["first_input_chars"], strict=True,
                      )], "followup_input_chars_delta": on["followup_input_chars"][0]
                      - off["followup_input_chars"][0]})
    (output / "raw.jsonl").write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in records
    ))
    summary = {"pairs": pairs, "conversations": len(records), "new_tasks": 0,
               "new_applications": 0, "paid_api_calls": 0, "real_tokens": None,
               "scope": "fixed development probes; no model success-rate estimate"}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    page = "<!doctype html><meta charset='utf-8'><title>订单候选事实对照</title>"
    page += "<style>body{font:16px sans-serif;padding:24px}main{display:grid;"
    page += "grid-template-columns:1fr 2fr;gap:24px}td,th{padding:5px;border:1px solid #ddd}"
    page += "table{border-collapse:collapse}pre{white-space:pre-wrap}</style><main>"
    for r in records[:2]:
        page += f"<section><h2>{'确定性事实' if r['enabled'] else '原始自由文本'}</h2>"
        if r["enabled"]:
            page += ("<table><tr><th>订单</th><th>明细</th><th>商品</th>"
                     "<th>SKU</th><th>颜色</th></tr>")
            for row in r["rows"]:
                page += "<tr>" + "".join("<td>" + html.escape(str(row[k])) + "</td>"
                                         for k in ("order_id", "item_id", "product_name",
                                                   "sku_id", "color")) + "</tr>"
            page += "</table>"
        else:
            page += "<pre>" + html.escape(r["answer"]) + "</pre>"
        page += "</section>"
    (output / "comparison.html").write_text(page + "</main>")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
