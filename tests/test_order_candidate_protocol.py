"""Invalid presentation data must leave a completed model reply untouched."""

import copy
import json
from pathlib import Path

import pytest

from acs.order_candidates import order_candidate_response

from .test_order_candidate_facts import sample_trace

FROZEN = json.loads((Path(__file__).resolve().parents[1]
                     / "data/eval/order_candidate_scope_v1.json").read_text())


def corrupt_orders(name):
    orders = copy.deepcopy(FROZEN["truth_payload"]["orders"])
    order, item = orders[0], orders[0]["items"][0]
    if name == "missing_order_id":
        del order["order_id"]
    elif name == "non_dict_order":
        orders[0] = None
    elif name in {"items_dict", "items_string", "item_null", "empty_items"}:
        order["items"] = {"items_dict": {"bad": item}, "items_string": "invalid",
                          "item_null": [None], "empty_items": []}[name]
    elif name == "missing_items":
        del order["items"]
    elif name in {"sku_list", "sku_string"}:
        item["sku"] = ["invalid"] if name == "sku_list" else "invalid"
    elif name == "quantity_bool":
        item["quantity"] = True
    elif name == "colour_list":
        item["sku"]["color"] = ["白色"]
    elif name == "item_id_text":
        item["id"] = "invalid"
    else:
        raise AssertionError(name)
    return orders


@pytest.mark.parametrize("name", FROZEN["malformed"])
def test_malformed_display_protocol_declines_without_raising(name):
    trace = sample_trace(corrupt_orders(name))
    before = copy.deepcopy(trace)
    assert order_candidate_response(trace, {"task": None}, "answered") is None
    assert trace == before


@pytest.mark.parametrize(("location", "key", "value"), [
    ("order", "order_id", ""), ("order", "order_id", 7),
    ("order", "status", []), ("order", "created_at", {}),
    ("item", "id", True), ("item", "id", 0),
    ("item", "quantity", -1), ("item", "product_name", []),
    ("item", "product_id", ""), ("item", "sku", {}),
    ("sku", "sku_id", []), ("sku", "color", {}),
])
def test_unrenderable_fields_decline_before_sorting(location, key, value):
    orders = copy.deepcopy(FROZEN["truth_payload"]["orders"])
    item = orders[0]["items"][0]
    target = {"order": orders[0], "item": item, "sku": item["sku"]}[location]
    target[key] = value
    assert order_candidate_response(sample_trace(orders), {"task": None}, "answered") is None


def test_valid_batch_preserves_every_item_and_none_sku():
    orders = copy.deepcopy(FROZEN["truth_payload"]["orders"])
    orders[0]["items"][0]["sku"] = None
    before = copy.deepcopy(orders)
    answer, detail = order_candidate_response(sample_trace(orders), {"task": None}, "answered")
    assert len(detail["rows"]) == 25 and "未知" in answer
    assert orders == before
