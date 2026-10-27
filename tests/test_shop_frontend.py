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
from .test_local_return_poc import draft
from .test_web_service import ORIGIN, browser, counts, done, events, post


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


def test_product_cards_retain_all_pages_in_the_same_turn(app_data):
    root, _ = app_data

    @contextmanager
    def factory():
        yield ScriptedModel(responses=[
            call("list_products", {"page_size": 2}, uuid4().hex),
            call("list_products", {"page_size": 2, "after_product_id": "P002"}, uuid4().hex),
            AIMessage(content="已查询全部四件商品。"),
        ])

    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        message = completed(client, sid, headers)
        ids = [p["product_id"] for p in message["cards"]["products"]]
        assert ids == ["P001", "P002", "P003", "P004"]


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
        with sqlite3.connect(root / "business.sqlite") as conn:
            call_ids = [r[0] for r in conn.execute(
                "SELECT call_id FROM local_receipts WHERE turn_id=? ORDER BY rowid",
                (message["turn_id"],),
            )]
        public = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
        assert call_ids and public[0]["tool_call_ids"] == call_ids
        assert public[0]["return_activity"] is False
        assert message["return_activity"] is False
        human = client.get(f"/api/sessions/{sid}/messages").json()["human_service"]
        assert human["available"] is False and "尚未接入" in human["message"]
        assert counts(root) == (0, 0, 0)


def test_return_activity_stays_on_business_turn_after_product_consultation(app_data):
    root, _ = app_data
    outputs = iter([
        [call("update_return_task", draft(), uuid4().hex),
         AIMessage(content="已保存退货草稿，尚未提交申请。")],
        [call("get_return_task", {}, uuid4().hex),
         call("list_products", {}, uuid4().hex), AIMessage(content="商品查询已返回。")],
    ])

    @contextmanager
    def factory():
        yield ScriptedModel(responses=next(outputs))

    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        for text in [
            "我要退货，订单O10024，明细4，数量1，无理由，未拆封，满足全部二次销售条件。",
            "介绍商品，保留退货草稿。",
        ]:
            assert post(client, sid, headers, "messages", text=text).status_code == 202
            assert done(client, sid)["jobs"][-1]["status"] == "completed"
        public = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
        assert [m["return_activity"] for m in public] == [True, True, False, False]
        assert public[0]["turn_id"] == public[1]["turn_id"]
        assert public[0]["turn_id"] != public[2]["turn_id"]
        assert counts(root) == (0, 0, 0)


def test_public_receipt_ids_use_same_redaction_as_sse(app_data):
    root, _ = app_data

    @contextmanager
    def factory():
        yield ScriptedModel(responses=[
            call("list_orders", {}, "call-13800000000"),  # Synthetic phone-shaped identifier.
            AIMessage(content="本人订单查询完成。"),
        ])

    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        message = completed(client, sid, headers)
        assert message["tool_call_ids"] == ["call-[已隐藏]"]
        public_call_ids = [
            e["data"]["call_id"] for e in events(client, sid) if e["type"] == "tool_requested"
        ]
        assert message["tool_call_ids"] == public_call_ids


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
