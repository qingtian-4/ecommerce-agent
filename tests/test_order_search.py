"""Owned order candidates must keep the literal product constraint through the UI."""
import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from acs.cs_tools import ToolRegistry
from acs.local_submission import init_formal_fixture
from acs.shop_frontend import create_shop_app

from .test_langgraph_poc import ScriptedModel, call
from .test_web_service import ORIGIN, browser, done, post


@pytest.mark.parametrize("query", ["Life P3", "ｌｉｆｅ ｐ３", "L i f e P 3"])
def test_literal_name_query_keeps_owned_matches_and_whole_order(settings, query):
    registry = ToolRegistry(settings)
    result, _, error = registry.run("list_orders", json.dumps({"name_contains": query}),
                                    current_user_id="USER001")
    assert error is None
    expected = [r["order_id"] for r in registry.db.list_orders("USER001")
                if any(i["product_id"] == "P001"
                       for i in registry.db.get_order(r["order_id"], "USER001")["items"])]
    assert expected and [r["order_id"] for r in result["orders"]] == expected
    assert len(next(r for r in result["orders"] if r["order_id"] == "O10024")["items"]) == 2
    assert "O10029" not in json.dumps(result)


def test_no_match_and_literal_sql_metacharacters_do_not_drop_constraint(settings):
    registry = ToolRegistry(settings)
    for query in ["不存在的商品", "%", "' OR 1=1 --"]:
        value, _, error = registry.run("list_orders", json.dumps({"name_contains": query}),
                                       current_user_id="USER001")
        assert error is None and value["orders"] == [] and value["count"] == 0


def test_cards_and_candidate_pagination_use_the_answer_receipt_subset(tmp_path):
    root = tmp_path / "shop"
    init_formal_fixture(root)

    @contextmanager
    def factory():
        yield ScriptedModel(responses=[
            call("list_orders", {}, uuid4().hex),
            AIMessage(content="回答中要查看的两笔订单：O10024、O10027。请明确选择。"),
        ])

    with TestClient(create_shop_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="查询耳机订单")
        done(client, sid)
        message = client.get(f"/api/sessions/{sid}/messages").json()["messages"][-1]
        assert [o["order_id"] for o in message["cards"]["orders"]] == ["O10024", "O10027"]
        assert message["cards"]["orders_scope"] == "answer_references"
        path = f"/api/sessions/{sid}/orders?name_contains=Life%20P3&page_size=1"
        first, second = client.get(path).json(), client.get(path + "&page=2").json()
        assert first["total"] < 10 and first["total"] > 1
        assert first["orders"][0]["order_id"] != second["orders"][0]["order_id"]
        for order in first["orders"] + second["orders"]:
            assert any("Life P3" in i["product_name"] for i in order["items"])
        assert client.get(f"/api/sessions/{sid}/orders?name_contains=无匹配").json()["total"] == 0
