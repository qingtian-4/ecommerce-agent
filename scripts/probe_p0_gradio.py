"""Serve the real Gradio UI with a scripted model and an isolated synthetic database."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.config import Settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


def serve(output: Path, port: int):
    output.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(prefix="acs-p0-gradio-") as temporary:
        root = Path(temporary) / "scenario"
        build_scenario(root)
        settings = scenario_settings(Settings(
            _env_file=None, llm_api_key="", embedding_api_key="", knowledge_mode="full_context",
        ), root)
        assert settings.return_state_check_enabled, "Set ACS_RETURN_STATE_CHECK_ENABLED=true"
        store = SessionStore(settings.db_path)
        fingerprint, records, models = data_fingerprint(settings.db_path), [], []
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        blocked = []

        def connect(method, connection, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
                blocked.append(str(address))
                raise AssertionError("External network forbidden in Gradio acceptance")
            return method(connection, address)

        with patch.object(socket.socket, "connect", lambda s, a: connect(original_connect, s, a)), \
                patch.object(socket.socket, "connect_ex",
                             lambda s, a: connect(original_connect_ex, s, a)):
            import app

            def agent_factory(*, settings, current_user_id):
                llm = ScriptedLLM([
                    ScriptedResponse(wants_tools=True, tool_calls=[ScriptedToolCall(
                        "order", "get_order", '{"order_id":"S20017"}',
                    )]),
                    ScriptedResponse(text="请确认要退订单中的哪件商品，目前尚未提交申请。"),
                    ScriptedResponse(text="好的，等待您选择商品；目前尚未提交申请。"),
                ])
                models.append(llm)
                return CustomerServiceAgent(settings=settings, llm=llm,
                                            registry=ToolRegistry(settings),
                                            current_user_id=current_user_id)

            original_send = app.on_send

            def observed_send(message, history, state, user_key):
                for frame in original_send(message, history, state, user_key):
                    if "data-reply-state='done'" in frame[-1] and state.get("session_id"):
                        uid, sid = app.user_id_from_key(user_key), state["session_id"]
                        snapshot = store.load(sid, uid)
                        records.append({"query": message, "session_id": sid,
                                        "history": snapshot["history"],
                                        "last_result": snapshot["last_result"],
                                        "task": store.get_return_task(sid, uid)["task"],
                                        "ui_card": frame[5], "ui_trace": frame[2]})
                        evidence = {"flag_enabled": settings.return_state_check_enabled,
                                    "model": "ScriptedLLM (not a real-model evaluation)",
                                    "paid_api_calls": 0, "external_network_blocked": blocked,
                                    "model_calls": sum(len(m.calls) for m in models),
                                    "business_fingerprint_unchanged":
                                        data_fingerprint(settings.db_path) == fingerprint,
                                    "turns": records}
                        (output / "server_evidence.json").write_text(
                            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                        )
                    yield frame

            app.SETTINGS, app._SESSION_STORE = settings, store
            app.CustomerServiceAgent, app.on_send = agent_factory, observed_send
            (output / "server.json").write_text(json.dumps({
                "url": f"http://127.0.0.1:{port}", "flag_enabled": True,
                "database": "isolated temporary synthetic scenario",
            }, indent=2) + "\n")
            demo = app.build_app()
            demo.queue().launch(
                server_name="127.0.0.1", server_port=port, show_error=True, css=app.CSS,
                theme=app.gr.themes.Soft(primary_hue="orange", neutral_hue="slate"),
                footer_links=[],
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7872)
    args = parser.parse_args()
    serve(args.out, args.port)
