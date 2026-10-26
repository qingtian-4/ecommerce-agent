"""HTTP + native Graph + synthetic SQLite, no paid network or client authority."""

from __future__ import annotations

import socket
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from acs.local_submission import init_formal_fixture
from acs.shop_frontend import create_shop_app
from acs.web_service import COOKIE

from .test_langgraph_poc import ScriptedModel, call
from .test_web_service import ORIGIN, browser, counts, done, post


@pytest.fixture
def app_data(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No model network allowed")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    root = tmp_path / "shop"
    init_formal_fixture(root)

    @contextmanager
    def factory():
        yield ScriptedModel(
            responses=[
                call("list_orders", {}, uuid4().hex),
                AIMessage(content="已查询本人订单，请从卡片选择。"),
            ]
        )

    return root, factory


def completed(client, sid, headers):
    assert post(client, sid, headers, "messages", text="查询我的订单").status_code == 202
    done(client, sid)
    value = client.get(f"/api/sessions/{sid}/messages").json()
    return next(m for m in value["messages"] if m.get("complete"))


def test_new_shell_keeps_old_shell_and_cookie_security(app_data):
    root, factory = app_data
    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        r = client.get("/shop")
        assert r.status_code == 200 and "chat-scroll" in r.text
        assert "HttpOnly" in r.headers["set-cookie"]
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert client.get("/").status_code == 200
        assert client.get("/shop/assets/app.js").status_code == 200
        assert client.get("/shop/assets/../../.env").status_code != 200
        assert client.get("/api/bootstrap").json()["runtime"]["mode"] == "custom"


def test_orders_are_paged_ordered_owned_and_whitelisted(app_data):
    root, factory = app_data
    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, _ = browser(client)
        path = f"/api/sessions/{sid}/orders"
        first = client.get(path).json()
        second = client.get(path + "?page=2").json()
        assert first["total"] == 10 and len(first["orders"]) == 3
        ids = [o["order_id"] for o in first["orders"] + second["orders"]]
        assert len(set(ids)) == 6
        with sqlite3.connect(root / "business.sqlite") as conn:
            actual = [
                r[0]
                for r in conn.execute(
                    "SELECT order_id FROM orders WHERE user_id='USER001' "
                    "ORDER BY created_at DESC,order_id LIMIT 6"
                )
            ]
            foreign = conn.execute(
                "SELECT order_id FROM orders WHERE user_id!='USER001'"
            ).fetchone()[0]
        assert ids == actual
        assert all("user_name" not in o and "address" not in o for o in first["orders"])
        assert client.get(path + "?page=0").status_code == 422
        assert client.get(path + "?page_size=100").status_code == 422
        assert client.get(path + "/" + foreign).status_code == 404
        assert client.get(path + "/" + foreign + "/logistics").status_code == 404
        assert client.get(path + "/O10024/logistics").json()["found"]
        assert counts(root) == (0, 0, 0)


def test_message_cards_have_native_turn_binding_and_original_text(app_data):
    root, factory = app_data
    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        message = completed(client, sid, headers)
        assert message["content"] == "已查询本人订单，请从卡片选择。"
        assert len(message["cards"]["orders"]) == 10
        assert message["message_id"] == message["turn_id"] + ":assistant"
        assert message["review_status"] == "reviewed"
        human = client.get(f"/api/sessions/{sid}/messages").json()["human_service"]
        assert human["available"] is False and "尚未接入" in human["message"]
        assert counts(root) == (0, 0, 0)


@pytest.mark.parametrize(
    "vote,reason",
    [
        ("helpful", None),
        ("unhelpful", "incorrect"),
        ("unhelpful", "incomplete"),
        ("unhelpful", "unclear"),
        ("unhelpful", "other"),
    ],
)
def test_feedback_real_persistence_idempotency_and_restart(app_data, vote, reason):
    root, factory = app_data
    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        message = completed(client, sid, headers)
        path = f"/api/sessions/{sid}/feedback"
        body = dict(turn_id=message["turn_id"], vote=vote, reason=reason, request_key=uuid4().hex)
        assert client.post(path, json=body, headers=headers).json()["persisted"] is True
        assert client.post(path, json=body, headers=headers).status_code == 200
        assert (
            client.post(path, json=body | {"turn_id": "different"}, headers=headers).status_code
            == 404
        )
        changed = body | (
            {"vote": "unhelpful", "reason": "incorrect"}
            if vote == "helpful"
            else {"vote": "helpful", "reason": None}
        )
        assert client.post(path, json=changed, headers=headers).status_code == 409
        cookie = client.cookies.get(COOKIE)
        with sqlite3.connect(root / "web.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM ui_feedback").fetchone()[0] == 1
        assert counts(root) == (0, 0, 0)
    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, cookie)
        data = client.get(f"/api/sessions/{sid}/messages").json()["messages"][-1]
        assert data["feedback"] == dict(vote=vote, reason=reason)
        assert data["content"] == message["content"]


@pytest.mark.parametrize(
    "body",
    [
        {"vote": "unhelpful", "reason": None},
        {"vote": "helpful", "reason": "other"},
        {"vote": "unhelpful", "reason": "unsafe"},
        {"vote": "helpful", "reason": None, "user_id": "USER002"},
        {"vote": "helpful", "reason": None, "db_path": "/tmp/other.sqlite"},
    ],
)
def test_feedback_rejects_invalid_and_identity_injection(app_data, body):
    root, factory = app_data
    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        message = completed(client, sid, headers)
        data = dict(turn_id=message["turn_id"], request_key=uuid4().hex) | body
        assert (
            client.post(f"/api/sessions/{sid}/feedback", json=data, headers=headers).status_code
            == 422
        )
        with sqlite3.connect(root / "web.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM ui_feedback").fetchone()[0] == 0


def test_new_routes_enforce_same_origin_csrf_and_session_ownership(app_data):
    root, factory = app_data
    app = create_shop_app(root, factory)
    with TestClient(app, base_url=ORIGIN) as a:
        b = TestClient(app, base_url=ORIGIN)
        sid, headers = browser(a)
        message = completed(a, sid, headers)
        other, _ = browser(b)
        for suffix in ["/orders", "/messages", "/orders/O10024", "/orders/O10024/logistics"]:
            assert b.get(f"/api/sessions/{sid}" + suffix).status_code == 403
        body = dict(turn_id=message["turn_id"], vote="helpful", request_key=uuid4().hex)
        path = f"/api/sessions/{sid}/feedback"
        assert a.post(path, json=body).status_code == 403
        assert (
            a.post(
                path, json=body, headers=headers | {"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert b.post(path, json=body, headers=headers).status_code == 403
        assert a.get(f"/api/sessions/{other}/orders").status_code == 403
        b.close()
