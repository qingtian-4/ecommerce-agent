"""Offline character accounting; run from repository root, never a Token estimate."""

import copy
import json
import shutil
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.context import input_chars  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.scenarios import build_scenario, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


class Capture(ScriptedLLM):
    def chat(self, *args, **kwargs):
        result = super().chat(*args, **kwargs)
        self.calls[-1] = copy.deepcopy(self.calls[-1])
        return result


def denied(*args, **kwargs):
    raise AssertionError("No network in character accounting")


with patch.object(socket.socket, "connect", denied), patch.object(
    socket.socket, "connect_ex", denied,
), TemporaryDirectory(prefix="acs-p0-context-") as temporary:
    frozen = ROOT / "results/iteration_v2_recovery/local_after/raw.jsonl"
    original = next(r for r in map(json.loads, frozen.read_text().splitlines())
                    if r["id"] == "orders-02" and r["trial"] == 1)
    source = Path(temporary) / "source"
    build_scenario(source)
    runs = []
    for enabled in (False, True):
        location = Path(temporary) / str(enabled)
        shutil.copytree(source, location)
        settings = scenario_settings(Settings(
            _env_file=None, llm_api_key="", embedding_api_key="",
            return_state_check_enabled=enabled,
        ), location)
        llm = Capture([ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("order", "get_order", '{"order_id":"S20017"}'),
        ])] + [ScriptedResponse(text=e["result"]["answer"]) for e in original["events"]])
        service = ChatService(settings, SessionStore(settings.db_path))
        agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
        sid, turns = None, []
        for event in original["events"]:
            result = list(service.run_turn(event["query"], "USER001", sid, agent=agent))[-1]
            sid = result.session_id
            turns.append(result.result.totals["model_input_chars"])
        calls = []
        for call in llm.calls:
            messages, tools = call["messages"], call["tools"]
            retained = [m for m in messages if not (m["role"] == "system"
                        and m["content"].startswith("当前会话的退货草稿如下"))]
            size = input_chars(messages, tools)
            calls.append({"input_chars": size,
                          "draft_system_chars": size - input_chars(retained, tools)})
        runs.append({"enabled": enabled, "calls": calls, "turn_input_chars": turns,
                     "fake_usage": llm.totals()})
    deltas = [{"call": i + 1, "total_chars": on["input_chars"] - off["input_chars"],
               "draft_system_chars": on["draft_system_chars"],
               "history_chars": on["input_chars"] - off["input_chars"]
               - on["draft_system_chars"]}
              for i, (off, on) in enumerate(zip(runs[0]["calls"], runs[1]["calls"], strict=True))]
    output = {"runs": runs, "deltas": deltas, "extra_model_calls": 0,
              "real_tokens": None, "real_cost": None, "paid_api_calls": 0,
              "unit": "serialized input characters; fake usage is a placeholder"}
    assert [d["total_chars"] for d in deltas] == [0, 0, 1207, 1207, 1207]
    Path(__file__).with_name("context_analysis.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
