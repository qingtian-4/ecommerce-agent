"""No-network finite-router ablation; fake usage is not real tokens."""

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
from acs.db_seed import build
from acs.routing import route_request
from acs.service_agent import CustomerServiceAgent

OUT = Path(__file__).parent
cases = json.loads(Path("data/eval/routing_v2.json").read_text())["cases"]
records = []
with (
    TemporaryDirectory() as directory,
    patch.object(socket.socket, "connect", side_effect=(AssertionError("network forbidden"))),
):
    directory = Path(directory)
    build(directory / "seed.db")
    for enabled in [False, True]:
        path = directory / f"{enabled}.db"
        shutil.copyfile(directory / "seed.db", path)
        settings = Settings(_env_file=None, db_path=path, workflow_router_enabled=enabled)
        llm = ScriptedLLM(
            [
                ScriptedResponse(
                    wants_tools=True,
                    tool_calls=[
                        ScriptedToolCall("irrelevant", "get_product", '{"product_id":"P001"}'),
                        ScriptedToolCall("order", "get_order", '{"order_id":"O10024"}'),
                    ],
                ),
                ScriptedResponse(text="只读查询结束"),
            ]
        )
        agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
        final = list(ChatService(settings).run_turn("查订单O10024的颜色", "USER001", agent=agent))[
            -1
        ]
        records.append(
            {
                "enabled": enabled,
                "model_calls": len(llm.calls),
                "first_tools_chars": len(json.dumps(llm.calls[0]["tools"], ensure_ascii=False)),
                "executed_tools": [e.label for e in final.result.trace if e.type == "tool_result"],
                "task": final.task_view["task"],
                "answer": final.result.answer,
            }
        )
    times = []
    labels = []
    for case in cases:
        for _ in range(100):
            started = perf_counter()
            result = route_request(case["text"], agent.registry.names, False)
            times.append((perf_counter() - started) * 1000)
        labels.append(
            {
                "text": case["text"],
                "expected": case["intent"],
                "actual": result["intent"],
                "confidence": result["confidence"],
            }
        )
result = {
    "labels": labels,
    "correct": sum(r["expected"] == r["actual"] for r in labels),
    "n": len(labels),
    "paired_fixed_decision": records,
    "route_ms": {
        "samples": len(times),
        "p50": statistics.median(times),
        "p95": sorted(times)[int(len(times) * 0.95) - 1],
        "max": max(times),
    },
    "paid_calls": 0,
    "fake_usage_not_real_tokens": True,
}
(OUT / "router_probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "labels"}, ensure_ascii=False))
