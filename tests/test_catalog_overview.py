"""The user's mixed query needs complete enumeration, not every SKU specification."""
import json

import pytest

from acs.answer_grounding import Evidence, review_answer
from acs.cs_tools import ToolRegistry, catalog_progress
from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings
from acs.shop_demo_data import materialize


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "shop"
    materialize(root)
    return ToolRegistry(fixture_settings(root, kind=FORMAL_FIXTURE_KIND))


def test_complete_overview_contains_every_identity_and_current_price(registry):
    result, _, error = registry.run("list_products", '{"view":"overview"}',
                                    current_user_id="USER001")
    assert error is None
    assert len(result["products"]) == result["total_count"] == 48
    assert result["complete_catalog"] and not result["has_more"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 16000
    for p in result["products"]:
        authoritative = registry.db.get_product(p["product_id"])
        assert p == {k: authoritative[k] for k in (
            "product_id", "name", "category", "current_price", "currency")}
    assert result["detail_tool"] == "get_product"
    assert result["specifications_complete"] is False


@pytest.mark.parametrize("query,expected", [("燃气", 2), ("不存在的商品", 0)])
def test_overview_filter_is_complete_and_does_not_relax_conditions(registry, query, expected):
    value, _, error = registry.run("list_products", json.dumps({
        "view": "overview", "name_contains": query}), current_user_id="USER001")
    assert error is None and len(value["products"]) == expected
    assert value["query_complete"] and not value["has_more"]
    assert all(query in p["name"] for p in value["products"])


def test_partial_catalog_preserves_independent_authoritative_order_result():
    evidence = Evidence(catalog_queries=[dict(complete=False, seen_count=8, total_count=48)],
                        completed_reads=[dict(tool="list_orders", count=30)])
    result = review_answer("我已完成所有任务。", evidence)
    assert "订单" in result.text and "30" in result.text
    assert "查询尚未完成" in result.text and "8/48" in result.text
    assert "我已完成所有任务" not in result.text


def test_overview_is_not_specification_evidence(registry):
    from acs.commerce_complex_eval import evidence_present

    value, _, _ = registry.run("list_products", '{"view":"overview"}',
                               current_user_id="USER001")
    receipt = dict(name="list_products", arguments='{"view":"overview"}',
                   result=json.dumps(value), error=None)
    goal = dict(evidence=dict(kind="product", product_id="B101", attribute="主动降噪"))
    assert not evidence_present(goal, {"state": {"receipts": [receipt]}}, [receipt], {})
    product = registry.db.get_product("B101")
    detail = dict(name="get_product", result=json.dumps(product), error=None)
    assert evidence_present(goal, {"state": {"receipts": [detail]}}, [detail], {})


def test_oversized_overview_fails_explicitly_without_partial_catalog(registry, monkeypatch):
    get_product = registry.db.get_product

    def large(pid):
        return get_product(pid) | {"name": "长商品名称" * 200}

    monkeypatch.setattr(registry.db, "get_product", large)
    value, _, error = registry.run("list_products", '{"view":"overview"}',
                                   current_user_id="USER001")
    assert error is None and value["error_type"] == "overview_too_large"
    assert value["products"] == [] and value["total_count"] == 48
    assert value["has_more"] and not value["query_complete"]
    assert value["next_arguments"]["view"] == "details"


def test_complete_details_can_finish_an_oversized_overview_query(registry):
    value, _, _ = registry.run("list_products", '{"view":"overview"}',
                               current_user_id="USER001")
    failed = value | dict(products=[], count=0, has_more=True, query_complete=False,
                           complete_catalog=False, error_type="overview_too_large")
    receipts = [dict(name="list_products", arguments='{"view":"overview"}',
                     result=json.dumps(failed), error=None)]
    args = {}
    while True:
        page, _, error = registry.run("list_products", json.dumps(args),
                                      current_user_id="USER001")
        assert error is None
        receipts.append(dict(name="list_products", arguments=json.dumps(args),
                             result=json.dumps(page), error=None))
        if not page["has_more"]:
            break
        args = page["next_arguments"]
    progress = catalog_progress(receipts)
    assert len(progress) == 1 and progress[0]["complete"]
    assert progress[0]["seen_count"] == 48
