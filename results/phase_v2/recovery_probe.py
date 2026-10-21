"""V1/V2 fixed-fault comparison without provider traffic."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall
from tests.test_recovery_v2 import FaultModel

from acs.config import Settings
from acs.cs_tools import ToolRegistry
from acs.db_seed import build
from acs.runtime import RunBudget
from acs.service_agent import CustomerServiceAgent

records = []
with TemporaryDirectory() as temp, patch("acs.service_agent.sleep", lambda seconds: None):
    db = Path(temp) / "business.db"
    build(db)
    for case in ["transient", "partial", "exhausted", "no_progress", "different_orders"]:
        for enabled in [False, True]:
            settings = Settings(_env_file=None, db_path=db, runtime_recovery_enabled=enabled)
            if case in {"no_progress", "different_orders"}:
                responses = [
                    ScriptedResponse(
                        wants_tools=True,
                        tool_calls=[
                            ScriptedToolCall(
                                str(i),
                                "get_order",
                                json.dumps(
                                    {
                                        "order_id": "O10024"
                                        if (case == "no_progress" or i % 2)
                                        else "O10023"
                                    }
                                ),
                            )
                        ],
                    )
                    for i in range(6)
                ]
                model = ScriptedLLM(responses)
            else:
                model = FaultModel(5 if case == "exhausted" else 1, partial=case == "partial")
            agent = CustomerServiceAgent(
                settings=settings, llm=model, registry=ToolRegistry(settings)
            )
            final = list(agent.chat_stream("核查", budget=RunBudget(10, 8)))[-1]
            records.append(
                {
                    "case": case,
                    "enabled": enabled,
                    "stop": final.stop_reason,
                    "attempts": getattr(model, "attempts", len(model.calls)),
                    "tool_executions": sum(e.type == "tool_result" for e in final.trace),
                    "retries": sum(e.type == "retry_scheduled" for e in final.trace),
                    "paid_calls": 0,
                    "fake_usage_not_real_tokens": True,
                }
            )
Path(__file__).with_suffix(".json").write_text(json.dumps(records, indent=2) + "\n")
print(json.dumps(records))
