"""Replay frozen paid decisions offline after the final cancellation-scope regression fix."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.scenarios import build_scenario, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def run(source, cases, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = {c["id"]: c for c in json.loads(cases.read_text())["cases"]}
    records = []

    def denied(*args, **kwargs):
        raise AssertionError("No API or network in recorded-decision replay")

    with (
        TemporaryDirectory(prefix="acs-recorded-") as temp,
        patch.object(socket.socket, "connect", denied),
        patch.object(socket.socket, "connect_ex", denied),
    ):
        for number, line in enumerate(source.read_text().splitlines()):
            row = json.loads(line)
            path = Path(temp) / str(number)
            build_scenario(path)
            settings = scenario_settings(Settings(_env_file=None, llm_api_key=""), path).model_copy(
                update={
                    "return_state_check_enabled": True,
                    "order_candidate_render_enabled": True,
                    "order_constraint_enabled": row["enabled"],
                    "order_aggregation_enabled": row["enabled"],
                }
            )
            store = SessionStore(settings.db_path)
            sid = store.create("USER001")
            if cases[row["case"]].get("preload_draft"):
                raw = "我想退S20017。"
                tid = store.begin_turn(sid, "USER001", raw)
                store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"})
                store.finish_turn(
                    sid,
                    "USER001",
                    tid,
                    [
                        {"role": "user", "content": raw},
                        {"role": "assistant", "content": "草稿已记录，商品待确认。"},
                    ],
                    {"answer": "草稿已记录，商品待确认。"},
                )
            equal = []
            for turn in row["turns"]:
                trace = turn["result"]["trace"]
                calls = [
                    ScriptedToolCall("replay-" + str(i), e["label"], e["detail"]["arguments"])
                    for i, e in enumerate(trace)
                    if e["type"] == "tool_call"
                ]
                gates = [e for e in trace if e["type"] == "output_gate"]
                text = gates[-1]["detail"]["original_answer"] if gates else turn["result"]["answer"]
                responses = []
                if calls:
                    responses.append(ScriptedResponse(wants_tools=True, tool_calls=calls))
                responses.append(ScriptedResponse(text=text))
                agent = CustomerServiceAgent(
                    settings=settings, llm=ScriptedLLM(responses), registry=ToolRegistry(settings)
                )
                final = list(
                    ChatService(settings, store).run_turn(
                        turn["query"], "USER001", sid, agent=agent
                    )
                )[-1]
                equal.append(final.result.answer == turn["result"]["answer"])
            records.append({"case": row["case"], "enabled": row["enabled"], "answers_equal": equal})
    (output / "summary.json").write_text(
        json.dumps(
            {
                "records": records,
                "conversations": len(records),
                "all_answers_identical": all(all(r["answers_equal"]) for r in records),
                "paid_calls": 0,
                "limits": "Recorded model decisions and terminal text only; not network timing.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    assert all(all(r["answers_equal"]) for r in records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.cases, args.out)
