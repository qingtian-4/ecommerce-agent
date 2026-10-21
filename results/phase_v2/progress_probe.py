"""Paired synthetic V1/V2 chain timing, schema chars and semantic checkpoint counts."""

import json
import shutil
import socket
import statistics
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

from acs.chat_service import ChatService
from acs.config import Settings
from acs.cs_tools import ToolRegistry
from acs.scenarios import build_scenario, scenario_settings
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

rows = []
with (
    TemporaryDirectory() as temp,
    patch.object(socket.socket, "connect", side_effect=(AssertionError("no provider network"))),
):
    base = Path(temp) / "base"
    build_scenario(base)
    for trial in range(8):
        for enabled in [False, True] if trial % 2 else [True, False]:
            local = Path(temp) / f"{trial}-{enabled}"
            shutil.copytree(base, local)
            settings = scenario_settings(Settings(_env_file=None), local).model_copy(
                update={
                    "workflow_router_enabled": enabled,
                    "runtime_recovery_enabled": enabled,
                    "execution_progress_enabled": enabled,
                }
            )
            model = ScriptedLLM(
                [
                    ScriptedResponse(
                        wants_tools=True,
                        tool_calls=[
                            ScriptedToolCall("order", "get_order", '{"order_id":"S20003"}'),
                            ScriptedToolCall("logistics", "get_logistics", '{"order_id":"S20003"}'),
                        ],
                    ),
                    ScriptedResponse(text="物流查询结束"),
                ]
            )
            store = SessionStore(settings.db_path)
            agent = CustomerServiceAgent(
                settings=settings, llm=model, registry=ToolRegistry(settings)
            )
            with patch.object(store, "save_progress", wraps=store.save_progress) as checkpoint:
                started = perf_counter()
                updates = list(
                    ChatService(settings, store).run_turn("查S20003物流", "USER001", agent=agent)
                )
                elapsed = (perf_counter() - started) * 1000
                rows.append(
                    {
                        "trial": trial,
                        "enabled": enabled,
                        "elapsed_ms": elapsed,
                        "checkpoint_writes": checkpoint.call_count,
                        "model_calls": len(model.calls),
                        "tool_calls": updates[-1].result.tool_calls,
                        "model_input_chars": updates[-1].result.totals["model_input_chars"],
                        "answer": updates[-1].result.answer,
                        "frame_count": len(updates),
                    }
                )
summary = {}
for enabled in [False, True]:
    selected = [r for r in rows if r["enabled"] == enabled]
    times = sorted(r["elapsed_ms"] for r in selected)
    summary[str(enabled)] = {
        "n": len(times),
        "p50_ms": statistics.median(times),
        "p95_ms": times[-1],
        "max_ms": times[-1],
        "checkpoint_writes": [r["checkpoint_writes"] for r in selected],
        "model_calls": [r["model_calls"] for r in selected],
    }
Path(__file__).with_suffix(".json").write_text(
    json.dumps(
        {
            "rows": rows,
            "summary": summary,
            "paid_calls": 0,
            "tokens": "fake usage, no real tokens",
            "timing": "Session reservation, reads, fake model loop, progress and persistence; "
            "8 alternating pairs, not a production SLA or provider-latency benchmark.",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(json.dumps(summary))
