"""Order attributes stay bound to the actual list_orders rows; no paid models."""

import json
import shutil
import socket
from contextlib import closing
from pathlib import Path

import pytest

from acs.chat_service import ChatService
from acs.config import Settings
from acs.cs_tools import ToolRegistry, TraceEvent
from acs.order_candidates import order_candidate_response
from acs.scenarios import build_scenario, scenario_settings
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

ROOT = Path(__file__).resolve().parents[1]
BADCASE = json.loads((ROOT / "results/p0_order_draft_integrity/acceptance_20261020/"
                     "next_badcase.json").read_text())
FULL_QUERY = "请列出我的全部订单和全部商品明细。"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden in P1 tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture(scope="session")
def p1_scenario(tmp_path_factory):
    root = tmp_path_factory.mktemp("p1-source") / "scenario"
    build_scenario(root)
    return root


@pytest.fixture
def local(tmp_path, p1_scenario):
    root = tmp_path / "scenario"
    shutil.copytree(p1_scenario, root)
    settings = scenario_settings(
        Settings(_env_file=None, llm_api_key="", embedding_api_key=""), root,
    )
    return settings, SessionStore(settings.db_path)


def replay(local, enabled, *, query=None, uid="USER001", sid=None, responses=None):
    settings, store = local
    settings = settings.model_copy(update={"order_candidate_render_enabled": enabled})
    llm = ScriptedLLM(responses or [ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("orders", "list_orders", "{}"),
    ]), ScriptedResponse(text=BADCASE["answer"])])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    final = list(ChatService(settings, store).run_turn(
        query or BADCASE["user_text"], uid, sid, agent=agent,
    ))[-1]
    return final, agent


@pytest.mark.parametrize("enabled", [False, True])
def test_original_orders01_colour_binding_switch_pair(local, enabled):
    final, agent = replay(local, enabled)
    facts = next(e.detail["result"] for e in final.result.trace if e.type == "tool_result")
    orders = {o["order_id"]: o for o in facts["orders"]}
    assert orders["S20008"]["items"][0]["sku"]["color"] == "白色"
    assert orders["S20003"]["items"][0]["sku"]["color"] == "黑色"
    if enabled:
        rows = final.result.answer.splitlines()
        white = next((line for line in rows if "| S20008 |" in line), "")
        black = next((line for line in rows if "| S20003 |" in line), "")
        assert "| V201-2 | 白色 |" in white, "S20008 colour is not bound to its own item"
        assert "| V201-1 | 黑色 |" in black
        assert "S20003" not in white and "S20008" not in black
        assert "S20008、S20003" not in final.result.answer
    else:
        assert final.result.answer == BADCASE["answer"]
        assert "S20008、S20003" in final.result.answer
    assert final.task_view["task"] is None
    assert len(agent.llm.calls) == 2


def test_rendered_rows_preserve_every_order_item_sku_and_ordering(local):
    final, agent = replay(local, True, query=FULL_QUERY)
    rendered = next(e for e in final.result.trace if e.type == "fact_render")
    source = final.result.trace[rendered.detail["tool_result_trace_index"]].detail["result"]
    expected = {(o["order_id"], i["id"], i["sku"]["sku_id"], i["sku"]["color"])
                for o in source["orders"] for i in o["items"]}
    rows = [line.split(" | ") for line in final.result.answer.splitlines()
            if line.startswith("| ")][2:]
    actual = {(r[1], int(r[2]), r[4], r[5]) for r in rows}
    assert actual == expected and len(rows) == len(expected) == 25
    bound = rendered.detail["rows"]
    assert [r["created_at"] for r in bound] == sorted(
        [r["created_at"] for r in bound], reverse=True,
    )
    assert rendered.detail["original_answer"] == BADCASE["answer"]
    assert rendered.detail["user_id"] == "USER001"
    assert agent.messages[-1]["content"] == final.result.answer
    snapshot = local[1].load(final.session_id, "USER001")
    assert snapshot["messages"][-1]["content"] == final.result.answer
    assert snapshot["last_result"]["answer"] == final.result.answer
    assert final.result.trace[-1].detail["text"] == final.result.answer


def sample_trace(orders=None):
    return [TraceEvent("tool_call", "list_orders", {"arguments": "{}"}),
            TraceEvent("tool_result", "list_orders", {"result": {"orders": orders or []}})]


@pytest.mark.parametrize("state", ["draft", "cancelled", "submitted", "unknown"])
def test_existing_or_unreadable_task_never_enters_candidate_mode(state):
    view = {"state_unavailable": True} if state == "unknown" else {"task": {"status": state}}
    assert order_candidate_response(sample_trace(), view, "answered", user_text=FULL_QUERY) is None


@pytest.mark.parametrize("stop", ["running", "llm_error", "max_steps", "budget_exceeded"])
def test_non_answered_turn_does_not_replace_error_with_facts(stop):
    assert order_candidate_response(sample_trace(), {"task": None}, stop,
                                    user_text=FULL_QUERY) is None


@pytest.mark.parametrize("mutation", ["missing_call", "wrong_order", "mixed", "error",
                                     "repeated", "missing_result", "invalid_payload"])
def test_non_candidate_tool_protocol_is_not_rewritten(mutation):
    trace = sample_trace()
    if mutation == "missing_call":
        trace.pop(0)
    elif mutation == "wrong_order":
        trace.reverse()
    elif mutation == "mixed":
        trace.insert(0, TraceEvent("tool_call", "get_order", {"arguments": "{}"}))
    elif mutation == "error":
        trace.append(TraceEvent("error", "list_orders", {}))
    elif mutation == "repeated":
        trace += sample_trace()
    elif mutation == "missing_result":
        trace.pop()
    else:
        trace[1].detail["result"] = {"error": "unavailable"}
    assert order_candidate_response(trace, {"task": None}, "answered", user_text=FULL_QUERY) is None


def test_empty_orders_unknown_sku_and_hostile_cell_are_not_invented():
    empty, detail = order_candidate_response(sample_trace(), {"task": None}, "answered",
                                             user_text=FULL_QUERY)
    assert "未找到" in empty and detail["rows"] == []
    answer, detail = order_candidate_response(sample_trace([{
        "order_id": "TEST", "created_at": None, "status": "pending",
        "items": [{"id": 1, "product_id": "P", "product_name": "<img src=x> | ![x](https://x)",
                   "sku": None, "quantity": 1}],
    }]), {"task": None}, "answered", user_text=FULL_QUERY)
    assert "<img" not in answer and "&#124;" in answer and "\\![" not in answer
    assert "\\!\\[x\\]\\(https://x\\)" in answer
    assert detail["rows"][0]["sku_id"] is None and detail["rows"][0]["color"] is None
    assert "未知" in answer


def test_sql_colour_changes_are_reflected_without_hardcoding(local):
    with closing(local[1]._connect()) as conn, conn:
        conn.execute("UPDATE product_skus SET color='蓝色' WHERE sku_id='V201-2'")
    final, _ = replay(local, True)
    row = next(line for line in final.result.answer.splitlines() if "| S20008 |" in line)
    assert "| V201-2 | 蓝色 |" in row and "白色" not in row


def test_user_identity_limits_the_authoritative_candidate_rows(local):
    final, _ = replay(local, True, uid="USER002")
    rendered = next(e for e in final.result.trace if e.type == "fact_render")
    assert rendered.detail["user_id"] == "USER002" and rendered.detail["rows"]
    assert "S20008" not in final.result.answer and "S20003" not in final.result.answer


def test_existing_draft_is_not_rebound_by_candidate_lookup(local):
    store = local[1]
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    before = store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"})["task"]
    store.finish_turn(sid, "USER001", tid, [
        {"role": "user", "content": "我想退S20017。"},
        {"role": "assistant", "content": "等待选择商品"},
    ], {"answer": "等待选择商品"})
    final, _ = replay(local, True, sid=sid)
    assert not any(e.type == "fact_render" for e in final.result.trace)
    assert final.task_view["task"] == before


def test_p1_does_not_supply_p0_write_authorization(local):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    final, _ = replay((settings, store), True, query="我想退S20017。")
    assert final.task_view["task"] is None
    state = next(e for e in final.result.trace if e.type == "state_check")
    assert state.detail["status"] == "blocked"
    assert "当前没有退货草稿" in final.result.answer


def test_ui_history_restore_and_trace_keep_the_same_candidate_facts(local, monkeypatch):
    import app

    settings, store = local
    settings = settings.model_copy(update={"order_candidate_render_enabled": True})
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("orders", "list_orders", "{}"),
    ]), ScriptedResponse(text=BADCASE["answer"])])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    state = {"agent": agent, "user_id": "USER001"}
    final = list(app.on_send(BADCASE["user_text"], [], state, "USER001"))[-1]
    restored = app.on_restore(state["session_id"], {}, "USER001")
    assert final[0] == restored[0] and final[2] == restored[2]
    assert "订单候选事实" in final[2] and "trace-step fact-render" in final[2]
    assert final[2].count("· 最终回答") == 1 and "tool calls: 1" in final[2]
    assert "尚无退货草稿" in final[5]


def test_render_switch_defaults_off_and_has_independent_environment(monkeypatch):
    monkeypatch.delenv("ACS_ORDER_CANDIDATE_RENDER_ENABLED", raising=False)
    assert not Settings(_env_file=None).order_candidate_render_enabled
    monkeypatch.setenv("ACS_ORDER_CANDIDATE_RENDER_ENABLED", "true")
    assert Settings(_env_file=None).order_candidate_render_enabled


@pytest.mark.parametrize("name", json.loads(
    (ROOT / "data/eval/order_candidate_scope_v1.json").read_text(),
)["malformed"])
def test_malformed_result_preserves_completed_reply_and_business_state(local, monkeypatch, name):
    from .test_order_candidate_protocol import corrupt_orders

    settings, store = local
    tables = ("return_tasks", "return_evidence", "return_previews", "return_confirmations",
              "return_requests", "return_audit", "return_reviews")

    def snapshot():
        with closing(store._connect()) as conn:
            return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]
                    for t in tables}

    before = snapshot()
    monkeypatch.setattr(ToolRegistry, "_list_orders", lambda self, **kwargs: {
        "orders": corrupt_orders(name),
    })
    answer = "工具数据暂时不完整，请重新核对订单信息。"
    final, agent = replay((settings, store), True, query="请列出我的全部订单和全部商品明细。",
                          responses=[ScriptedResponse(wants_tools=True, tool_calls=[
                              ScriptedToolCall("orders", "list_orders", "{}"),
                          ]), ScriptedResponse(text=answer)])
    saved = store.load(final.session_id, "USER001")
    assert final.result.stop_reason == "answered"
    assert final.result.answer == agent.messages[-1]["content"] == answer
    assert saved["last_result"]["answer"] == saved["messages"][-1]["content"] == answer
    assert final.result.trace[-1].detail["text"] == answer
    assert not any(e.type == "fact_render" for e in final.result.trace)
    assert final.task_view["task"] is None and snapshot() == before
