"""Offline P0 ablation through real UI handlers, Loop and SQLite; never a live eval."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import socket
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

FROZEN = ROOT / "results/iteration_v2_recovery/local_after/raw.jsonl"


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    attempts = []
    def denied(*args, **kwargs):
        attempts.append("blocked")
        raise AssertionError("Network forbidden in offline P0 probe")
    with patch.object(socket.socket, "connect", denied), patch.object(
        socket.socket, "connect_ex", denied,
    ), TemporaryDirectory(prefix="acs-p0-probe-") as temporary:
        import app

        source = Path(temporary) / "source"
        build_scenario(source)
        with FROZEN.open() as handle:
            original = next(r for r in map(json.loads, handle)
                            if r["id"] == "orders-02" and r["trial"] == 1)
        cases = [
            ("orders-02", "USER001", [e["query"] for e in original["events"]], "S20017", False),
            ("query-only", "USER001", ["只查询S20017，不办理退货。"], "S20017", False),
            ("negation", "USER001", ["我不想退S20017。"], "S20017", False),
            ("hypothesis", "USER001", ["如果我要退S20017"], "S20017", False),
            ("quoted", "USER001", ["别人说‘我想退S20017’"], "S20017", False),
            ("wrong-order-read", "USER001", ["我想退S20017。"], "S20001", False),
            ("foreign-order", "USER002", ["我想退S20017。"], "S20017", False),
            ("model-saved", "USER001", ["我想退S20017。"], "S20017", True),
        ]
        records, ui = [], {}
        for case_id, uid, queries, read_id, model_saved in cases:
            for enabled in (False, True):
                root = Path(temporary) / f"{case_id}-{enabled}"
                shutil.copytree(source, root)
                settings = scenario_settings(Settings(
                    _env_file=None, llm_api_key="", embedding_api_key="",
                    return_state_check_enabled=enabled,
                ), root)
                store = SessionStore(settings.db_path)
                before = data_fingerprint(settings.db_path)
                with closing(store._connect()) as conn:
                    business_before = {t: conn.execute(f"SELECT * FROM {t}").fetchall()
                                       for t in ("return_requests", "return_confirmations",
                                                 "return_audit")}
                calls = [ScriptedToolCall("order", "get_order", json.dumps({"order_id": read_id}))]
                if model_saved:
                    calls.append(ScriptedToolCall("save", "update_return_task",
                                                  '{"order_id":"S20017"}'))
                answers = ([e["result"]["answer"] for e in original["events"]]
                           if case_id == "orders-02" else ["请您选择商品。"])
                llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=calls)] + [
                    ScriptedResponse(text=a) for a in answers
                ])
                agent = CustomerServiceAgent(settings=settings, llm=llm,
                                             registry=ToolRegistry(settings))
                state, turns = {"agent": agent, "user_id": uid}, []
                with patch.object(app, "SETTINGS", settings), patch.object(
                    app, "_SESSION_STORE", store,
                ):
                    for index, query in enumerate(queries):
                        frame = list(app.on_send(query, [], state, uid))[-1]
                        snapshot = store.load(state["session_id"], uid)
                        assert snapshot["last_result"]["stop_reason"] == "answered"
                        assert frame[0][-1]["content"] == snapshot["messages"][-1]["content"]
                        turns.append(snapshot["last_result"])
                        if case_id == "orders-02" and index == 0:
                            ui[str(enabled)] = frame
                task = store.get_return_task(state["session_id"], uid)["task"]
                expected = model_saved or (enabled and case_id == "orders-02")
                assert bool(task) == expected
                if task:
                    assert task["order_id"] == "S20017" and task["item_id"] is None
                    assert task["revision"] == 1
                assert data_fingerprint(settings.db_path) == before
                with closing(store._connect()) as conn:
                    business_after = {t: conn.execute(f"SELECT * FROM {t}").fetchall()
                                      for t in business_before}
                assert business_after == business_before
                business = {t: len(rows) for t, rows in business_after.items()}
                records.append({"case_id": case_id, "enabled": enabled,
                                "final_task": task, "synthetic_model_calls": len(llm.calls),
                                "business_rows": business, "business_unchanged": True,
                                "turns": turns})
        assert not attempts
        for case_id, *_ in cases:
            pair = [r for r in records if r["case_id"] == case_id]
            assert pair[0]["synthetic_model_calls"] == pair[1]["synthetic_model_calls"]
        page = ("<!doctype html><html lang='zh'><meta charset='utf-8'>"
                "<title>P0 状态检查离线 UI 验证</title><style>" + app.CSS +
                "body{padding:20px;background:white}main{display:grid;"
                "grid-template-columns:1fr 1fr;"
                "gap:20px}.trace-panel{max-height:none}</style><body><main>")
        for enabled, frame in ui.items():
            page += (f"<section><h2>检查开关：{enabled}</h2>" + frame[5] + frame[2] +
                     "<pre style='white-space:pre-wrap'>" +
                     html.escape(frame[0][-1]["content"]) + "</pre></section>")
        page += ("</main><script>document.body.dataset.probeResult=JSON.stringify({"
                 "stateChecks:document.querySelectorAll('.trace-step.state-check').length,"
                 "finals:[...document.querySelectorAll('.trace-head')].filter(e=>"
                 "e.textContent.includes('最终回答')).length,"
                 "orderVisible:document.querySelectorAll('#return-task')[1].textContent"
                 ".includes('S20017')});</script></body></html>")
        (output / "ui.html").write_text(page)
        with (output / "raw.jsonl").open("x") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        durations = sorted(e["duration_ms"] for r in records for t in r["turns"]
                           for e in t["trace"] if e["type"] == "state_check")
        summary = {"pairs": len(cases), "conversations": len(records),
                   "eligible_omissions": 1, "off_repaired": 0, "on_repaired": 1,
                   "negative_pairs": len(cases) - 2, "false_drafts": 0,
                   "extra_revisions": 0, "new_applications": 0, "new_audits": 0,
                   "extra_synthetic_model_calls": 0, "paid_api_calls": 0,
                   "network_attempts": len(attempts), "check_duration_ms": durations,
                   "source_sha256": hashlib.sha256(FROZEN.read_bytes()).hexdigest(),
                   "limitations": "Fixed scripted decisions; not live quality or Token usage."}
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        )
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().out), ensure_ascii=False, indent=2))
