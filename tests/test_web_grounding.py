"""Real HTTP/Graph/SQLite and private evidence for answer presentation checks."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from acs.local_submission import init_formal_fixture
from acs.web_service import create_app

from .test_langgraph_poc import ScriptedModel, call
from .test_local_return_poc import draft
from .test_web_service import ORIGIN, browser, counts, done, events, post

RAW = "已生成最新预览（此前耳机版本的预览已失效）。\n商品支持PD快充。\n预览有效期：12:52左右。"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def reject(*args, **kwargs):
        raise AssertionError("Grounding tests must never contact a provider")

    monkeypatch.setattr(socket.socket, "connect", reject)


def fixture(tmp_path, replies):
    root = tmp_path / "web"
    init_formal_fixture(root)

    @contextmanager
    def factory():
        yield ScriptedModel(responses=replies)

    return root, factory


def test_projection_keeps_raw_graph_tool_messages_and_survives_submission_restart(tmp_path):
    root, factory = fixture(
        tmp_path,
        [
            call("update_return_task", draft(), "draft"),
            call("preview_return", {}, "preview"),
            AIMessage(content=RAW),
        ],
    )
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(
            client,
            sid,
            headers,
            "messages",
            text="退O10024耳机1件，无理由，未拆封且满足全部二次销售条件，请生成预览",
        )
        s = done(client, sid)
        text = s["history"][-1]["content"]
        assert "此前耳机版本的预览已失效" not in text
        assert "商品支持PD快充" in text and "UTC+08:00" in text
        with sqlite3.connect(root / "business.sqlite") as conn:
            row = conn.execute("SELECT answer,messages_json FROM conversation_turns").fetchone()
            assert row[0] == RAW and json.loads(row[1])[-1]["content"] == RAW
            assert conn.execute("SELECT COUNT(*) FROM return_previews").fetchone()[0] == 1
        log = events(client, sid)
        assert any(
            e["type"] == "answer_checked" and e["data"]["status"] == "corrected" for e in log
        )
        assert log[-1]["data"]["text"] == text
        private = [json.loads(p.read_text()) for p in (root / ".private-evidence").glob("*.json")]
        assert any(p.get("kind") == "answer_grounding" and p["raw_answer"] == RAW for p in private)
        post(client, sid, headers, "confirmation")
        waiting = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=waiting["wait_id"], decision="accept")
        final = done(client, sid)
        assert counts(root) == (1, 1, 1)
        assert final["history"][-1]["content"] == text
        cookies = dict(client.cookies)
    with TestClient(create_app(root, factory), base_url=ORIGIN) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get(f"/api/sessions/{sid}").json()["history"][-1]["content"] == text
        assert counts(root) == (1, 1, 1)


def test_failed_evidence_write_hides_unreviewed_success_and_preserves_raw(tmp_path, monkeypatch):
    raw = "已提交退货申请。商品支持65W。"
    root, factory = fixture(tmp_path, [AIMessage(content=raw)])
    from acs.web_service import ResponseEvidence

    def fail(*args, **kwargs):
        raise OSError("injected private evidence failure")

    monkeypatch.setattr(ResponseEvidence, "save", fail)
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="查询商品")
        state = done(client, sid)
        assert state["jobs"][-1]["status"] == "failed"
        assert "已提交退货申请" not in state["history"][-1]["content"]
        assert "尚未完成核验" in state["history"][-1]["content"]
        assert not any(e["type"] == "answer_completed" for e in events(client, sid))
        assert counts(root) == (0, 0, 0)
        with sqlite3.connect(root / "business.sqlite") as conn:
            assert conn.execute("SELECT answer FROM conversation_turns").fetchone()[0] == raw


def test_read_only_application_flag_rejects_entire_write_batch(tmp_path):
    model = AIMessage(
        content="",
        tool_calls=[
            {"id": "read", "name": "get_product", "args": {"product_id": "P003"}},
            {"id": "write", "name": "update_return_task", "args": draft()},
        ],
    )
    root, factory = fixture(tmp_path, [model])
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(
            client, sid, headers, "messages", text="退O10024耳机1件，无理由，未拆封", read_only=True
        )
        state = done(client, sid)
        assert state["jobs"][-1]["status"] == "failed"
        assert state["draft"] == {}
        assert not any(e["type"] == "tool_completed" for e in events(client, sid))
        assert counts(root) == (0, 0, 0)


def test_presentation_tampering_is_detected_by_raw_hash(tmp_path):
    root, factory = fixture(tmp_path, [AIMessage(content="商品咨询已经回答。")])
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="查询商品")
        done(client, sid)
        with sqlite3.connect(root / "business.sqlite") as conn:
            conn.execute("UPDATE conversation_turns SET answer='已退款'")
        assert client.get(f"/api/sessions/{sid}").status_code == 503
        assert counts(root) == (0, 0, 0)


def test_exit_window_before_presentation_never_exposes_native_success_text(tmp_path):
    raw = "已提交退货申请。商品支持65W。"
    root, factory = fixture(tmp_path, [AIMessage(content=raw)])

    def fault(point):
        if point == "before_answer_review":
            raise RuntimeError("injected exit window before answer presentation")

    with TestClient(create_app(root, factory, fault=fault), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="查询商品")
        state = done(client, sid)
        assert state["jobs"][-1]["status"] == "failed"
        assert state["history"][-1]["review_status"] == "pending"
        assert "已提交退货申请" not in state["history"][-1]["content"]
        with sqlite3.connect(root / "web.sqlite") as conn:
            assert conn.execute("SELECT raw_sha256,text FROM answer_presentations").fetchone() == (
                None,
                None,
            )
        assert counts(root) == (0, 0, 0)


@pytest.mark.parametrize("write_batch", [False, True])
def test_unknown_server_scope_cannot_be_downgraded_by_client_false(tmp_path, write_batch):
    from uuid import uuid4

    root = tmp_path / "scope"
    init_formal_fixture(root)
    replies = [
        [
            call("update_return_task", draft(), "d"),
            call("preview_return", {}, "p"),
            AIMessage(content="隔离预览已生成，未提交。"),
        ],
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "q", "name": "get_product", "args": {"product_id": "P003"}},
                    *(
                        [{"id": "w", "name": "update_return_task", "args": draft()}]
                        if write_batch
                        else []
                    ),
                ],
            ),
            AIMessage(content="商品查询完成。"),
        ],
    ]

    @contextmanager
    def factory():
        yield ScriptedModel(responses=replies.pop(0))

    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(
            client,
            sid,
            headers,
            "messages",
            text="退O10024耳机1件，无理由，未拆封且满足全部二次销售条件，请预览",
        )
        done(client, sid)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        with sqlite3.connect(root / "business.sqlite") as conn:
            conn.execute(
                "UPDATE local_confirmation_waits SET status='issued',event_id=?,"
                "decision='accept' WHERE wait_id=?",
                (uuid4().hex, wait["wait_id"]),
            )
            before = conn.execute("SELECT COUNT(*) FROM local_receipts").fetchone()[0]
        assert client.get(f"/api/sessions/{sid}/return-card").json()["state"] == "UNKNOWN"
        response = post(client, sid, headers, "messages", text="查询P003商品", read_only=False)
        assert response.status_code == 202
        state = done(client, sid)
        assert state["jobs"][-1]["status"] == ("failed" if write_batch else "completed")
        with sqlite3.connect(root / "business.sqlite") as conn:
            after = conn.execute("SELECT COUNT(*) FROM local_receipts").fetchone()[0]
        assert after == before + (0 if write_batch else 1)
        assert counts(root) == (0, 0, 0)


def test_model_budget_failure_does_not_masquerade_as_unknown_submission(tmp_path):
    from acs.runtime import BudgetExceeded

    root = tmp_path / "budget"
    init_formal_fixture(root)

    @contextmanager
    def factory():
        raise BudgetExceeded("phase_cost_limit")
        yield

    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="查询商品")
        done(client, sid)
        last = events(client, sid)[-1]
        assert last["data"]["status"] == "stopped"
        assert last["data"]["reason"] == "budget_exhausted"
        assert client.get(f"/api/sessions/{sid}/return-card").json()["state"] != "UNKNOWN"
        assert counts(root) == (0, 0, 0)


def test_readonly_resume_of_pending_write_checkpoint_fails_closed(tmp_path):
    from acs.local_submission import FormalReturnPoc

    root = tmp_path / "resume"
    init_formal_fixture(root)

    def crash(point):
        if point == "before_commit":
            raise OSError("business transaction interrupted")

    model = ScriptedModel(responses=[call("update_return_task", draft(), "write")])
    with FormalReturnPoc(root, "USER001", model=model, fault=crash) as poc:
        sid = poc.store.create("USER001")
        with pytest.raises(OSError):
            list(poc.stream(sid, "退O10024耳机1件，无理由，未拆封且满足全部二次销售条件"))
    with FormalReturnPoc(
        root,
        "USER001",
        read_only=True,
        model=ScriptedModel(responses=[AIMessage(content="当前只允许查询。")]),
    ) as poc:
        with pytest.raises(PermissionError, match="Read-only recovery"):
            list(poc.stream(sid))
        assert not poc.store.get_return_task(sid, "USER001")["task"]
    assert counts(root) == (0, 0, 0)


@pytest.mark.parametrize(
    "code,reason",
    [
        ("usage_missing", "model_receipt_unverified"),
        ("invalid_usage", "model_receipt_unverified"),
        ("adapter_protocol_error", "model_response_rejected"),
    ],
)
def test_nonlimit_budget_stop_is_not_mislabeled_as_exhausted(tmp_path, code, reason):
    from acs.runtime import BudgetExceeded

    root = tmp_path / "receipt"
    init_formal_fixture(root)

    @contextmanager
    def factory():
        raise BudgetExceeded(code)
        yield

    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="商品咨询")
        done(client, sid)
        assert events(client, sid)[-1]["data"]["reason"] == reason
        assert counts(root) == (0, 0, 0)
