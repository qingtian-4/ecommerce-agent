"""Hard exits and concurrent SQL receipts, separate from scripted model quality."""

import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import LocalReturnGateway, TurnContext, fixture_settings, init_fixture
from acs.sessions import SessionStore

from .test_langgraph_poc import ScriptedModel
from .test_local_return_poc import counts, draft


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError("No network in receipt recovery contracts")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_preview_hard_exit_batch_recovery_once(tmp_path, point):
    root = tmp_path / "preview-crash"
    init_fixture(root)
    store = SessionStore(fixture_settings(root).db_path)
    sid = store.create("USER001")
    pid = os.fork()
    if pid == 0:
        hits = 0

        def crash(where):
            nonlocal hits
            if where == point:
                hits += 1
                if hits == 2:  # First draft transaction completes; crash preview transaction.
                    os._exit(73)

        model = ScriptedModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "draft", "name": "update_return_task", "args": draft()},
                        {"id": "preview", "name": "preview_return", "args": {}},
                    ],
                ),
                AIMessage(content="must exit first"),
            ]
        )
        with LocalReturnPoc(root, "USER001", model=model, fault=crash) as poc:
            list(poc.stream(sid, "退货并预览"))
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    model = ScriptedModel(responses=[AIMessage(content="预览恢复，不是申请。")])
    with LocalReturnPoc(root, "USER001", model=model) as poc:
        done = list(poc.stream(sid))[-1]
        assert done.detail["business_state"]["preview"]["ready"]
        assert counts(poc.gateway)["return_previews"] == 1
        assert counts(poc.gateway)["local_receipts"] == 2
        assert done.detail["business_state"]["task"]["task"]["revision"] == 1
        delivered = [
            json.loads(m.content)
            for m in model.seen[0]
            if isinstance(m, ToolMessage) and m.tool_call_id == "preview"
        ]
        assert len(delivered) == 1
        assert delivered[0]["replayed"] == (point == "after_commit")


def test_two_sessions_concurrent_receipts_and_owner(tmp_path):
    root = tmp_path / "concurrent"
    init_fixture(root)
    settings = fixture_settings(root)
    store = SessionStore(settings.db_path)
    contexts = []
    for n in range(2):
        sid = store.create("USER001")
        tid = store.begin_turn(sid, "USER001", "我要退货")
        with store.transaction() as conn:
            conn.execute("INSERT INTO local_threads VALUES(?,?,?,0)", (sid, f"t{n}", tid))
        contexts.append(TurnContext(sid, "USER001", tid))
    gateway = LocalReturnGateway(settings)
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(gateway.execute, "update_return_task", draft(), "same-call", c)
            for c in contexts
        ]
        results = [f.result(timeout=30) for f in futures]
    assert all(r["result"]["task"]["revision"] == 1 for r in results)
    assert counts(gateway)["local_receipts"] == 2
    for context in contexts:
        with pytest.raises(PermissionError):
            gateway.execute(
                "update_return_task",
                draft(),
                "same-call",
                TurnContext(context.session_id, "USER002", context.turn_id),
            )
