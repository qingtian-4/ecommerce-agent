"""The fixed Demo must present actual tool facts and disclose its limits."""

import sqlite3

from fastapi.testclient import TestClient
from scripts.v3_web_demo import offline_factory

from acs.local_return_tools import fixture_settings
from acs.local_submission import KIND, init_formal_fixture
from acs.web_service import create_app

from .test_web_service import ORIGIN, browser, counts, done, post


def test_product_and_order_answers_render_different_authoritative_facts(tmp_path):
    root = tmp_path / "demo"
    init_formal_fixture(root)
    with TestClient(create_app(root, offline_factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="查询P003充电器接口")
        product_answer = done(client, sid)["history"][-1]["content"]
        settings = fixture_settings(root, kind=KIND)
        product = client.app.state.engine.gateway.registry.db.get_product("P003")
        assert product["name"] in product_answer
        assert all(a in product_answer for a in product["attributes"])
        post(client, sid, headers, "messages", text="查询我的订单")
        order_answer = done(client, sid)["history"][-1]["content"]
        with sqlite3.connect(settings.db_path) as conn:
            orders = conn.execute("SELECT order_id FROM orders WHERE user_id='USER001'").fetchall()
        assert orders
        assert all(row[0] in order_answer for row in orders)
        assert product_answer != order_answer
        assert counts(root) == (0, 0, 0)


def test_fixed_runtime_capabilities_are_server_declared_and_honest(tmp_path):
    root = tmp_path / "demo"
    init_formal_fixture(root)
    with TestClient(
        create_app(root, offline_factory, runtime_mode="fixed_script"), base_url=ORIGIN
    ) as client:
        browser(client)
        runtime = client.get("/api/bootstrap").json()["runtime"]
        assert runtime["mode"] == "fixed_script"
        assert runtime["answer_streaming"] is False
        assert runtime["progress_streaming"] is True
        assert "固定脚本" in runtime["label"]
        assert "自由对话" in runtime["notice"]
        assert "逐字" in runtime["notice"]


def test_unsupported_free_question_does_not_masquerade_as_a_model_answer(tmp_path):
    root = tmp_path / "demo"
    init_formal_fixture(root)
    with TestClient(create_app(root, offline_factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="请推荐适合我的所有商品")
        answer = done(client, sid)["history"][-1]["content"]
        assert "固定脚本" in answer and "自由对话" in answer
        assert counts(root) == (0, 0, 0)
