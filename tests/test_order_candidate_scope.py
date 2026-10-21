"""Request scope is independent of model wording and returned order volume."""

import copy
import json
from pathlib import Path

import pytest
from scripts.review_order_candidate_scope import audit

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.order_candidates import order_candidate_response

from .conftest import ScriptedResponse, ScriptedToolCall
from .test_order_candidate_facts import (
    local as local,
)
from .test_order_candidate_facts import (
    no_network as no_network,
)
from .test_order_candidate_facts import (
    p1_scenario as p1_scenario,
)
from .test_order_candidate_facts import replay, sample_trace

ROOT = Path(__file__).resolve().parents[1]
FROZEN = json.loads((ROOT / "data/eval/order_candidate_scope_v1.json").read_text())
GUARDS = json.loads((ROOT / "data/eval/order_candidate_boundary_guards_v1.json").read_text())
CATEGORIES = {r["product_id"]: r["category"] for r in FROZEN["truth_rows"]}
FULL_QUERY = "请列出我的全部订单和全部商品明细。"


@pytest.mark.parametrize("case", FROZEN["cases"], ids=lambda c: c["id"])
def test_frozen_tasks_finish_without_unrelated_orders(local, case):
    final, _ = replay(local, True, query=case["query"], responses=[
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("orders", "list_orders", "{}"),
        ]), ScriptedResponse(text=case["model_answer"]),
    ])
    measured = audit(final.result.answer, case, FROZEN["truth_rows"])
    assert measured["fact_precision"] == 1
    assert measured["task_complete"], measured
    assert not measured["irrelevant_orders"]
    if case["id"] not in {"list-all", "short-full-control", "return-all",
                          "return-headphones", "legacy-orders01"}:
        assert final.result.answer == case["model_answer"]
        assert not any(e.type == "fact_render" for e in final.result.trace)
    if case["id"] == "legacy-orders01":
        assert "尚未限定时间范围" in final.result.answer


@pytest.mark.parametrize("guard", GUARDS["guards"], ids=lambda c: c["id"])
def test_full_user_utterance_controls_takeover(guard):
    trace = sample_trace(copy.deepcopy(FROZEN["truth_payload"]["orders"]))
    trace[1].detail["result"]["product_categories"] = dict(CATEGORIES)
    before = copy.deepcopy(trace)
    result = order_candidate_response(trace, {"task": None}, "answered",
                                      user_text=guard["query"])
    assert (result is not None) == guard["takeover"]
    assert trace == before


@pytest.mark.parametrize("mutation", ["missing_categories", "unknown_category",
                                     "non_dict_categories"])
def test_category_protocol_fails_closed(mutation):
    trace = sample_trace(copy.deepcopy(FROZEN["truth_payload"]["orders"]))
    payload = trace[1].detail["result"]
    payload["product_categories"] = dict(CATEGORIES)
    if mutation == "missing_categories":
        del payload["product_categories"]
    elif mutation == "unknown_category":
        payload["product_categories"][next(iter(CATEGORIES))] = "未核实类别"
    else:
        payload["product_categories"] = []
    assert order_candidate_response(trace, {"task": None}, "answered",
                                    user_text="我想退最近买的耳机。") is None


def test_missing_user_utterance_never_authorizes_presentation():
    assert order_candidate_response(sample_trace(), {"task": None}, "answered") is None


def test_tool_categories_use_sql_not_product_names_and_off_payload_is_unchanged(local):
    settings, _ = local
    off = ToolRegistry(settings)._list_orders(current_user_id="USER001")
    assert off == FROZEN["truth_payload"]
    on_settings = settings.model_copy(update={"order_candidate_render_enabled": True})
    on = ToolRegistry(on_settings)._list_orders(current_user_id="USER001")
    assert on.pop("product_categories") == CATEGORIES
    assert on == off


def test_accessory_name_cannot_make_it_a_headphone(local):
    settings, store = local
    with store._connect() as conn:
        conn.execute("UPDATE products SET name='演示 耳机清洁配件' WHERE product_id='V209'")
        conn.commit()
    final, _ = replay((settings, store), True)
    event = next(e for e in final.result.trace if e.type == "fact_render")
    assert len(event.detail["rows"]) == 18
    assert not any(r["product_id"] == "V209" for r in event.detail["rows"])


def test_previous_scope_is_not_reset_by_candidate_presentation(local):
    context = json.loads(
        (ROOT / "results/p1_scope_boundary_followup/context_guard.json").read_text(),
    )
    settings, store = local
    first, agent = replay(local, True, query=context["initial_user"], responses=[
        ScriptedResponse(text="好的，后续按您限定的颜色和类别查询。"),
    ])
    rows = next(c["model_answer"] for c in FROZEN["cases"] if c["id"] == "category-headphones")
    answer = "\n".join(rows.splitlines()[:2]
                       + [line for line in rows.splitlines()[2:] if "| 白色 |" in line])
    answer += "\n请确认订单号及明细编号。"
    agent.llm._responses.extend([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("orders", "list_orders", "{}"),
        ]), ScriptedResponse(text=answer),
    ])
    final = list(ChatService(agent.settings, store).run_turn(
        context["next_user"], "USER001", first.session_id, agent=agent,
    ))[-1]
    assert final.result.answer == answer
    assert not any(e.type == "fact_render" for e in final.result.trace)
