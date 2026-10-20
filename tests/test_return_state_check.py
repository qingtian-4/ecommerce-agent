"""Replay actual model omissions through the real runtime without any network."""

import json
import shutil
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from acs.chat_service import ChatService
from acs.config import Settings
from acs.cs_tools import ToolRegistry
from acs.database import BusinessDB
from acs.return_state_check import check_order_draft
from acs.returns import ReturnService
from acs.runtime import BudgetExceeded, RunBudget
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings
from acs.service_agent import CustomerServiceAgent
from acs.sessions import SessionStore, TaskValidationError

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in P0 tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture(scope="session")
def scenario(tmp_path_factory):
    root = tmp_path_factory.mktemp("p0-source") / "scenario"
    build_scenario(root)
    return root


@pytest.fixture
def local(tmp_path, scenario):
    root = tmp_path / "scenario"
    shutil.copytree(scenario, root)
    settings = scenario_settings(
        Settings(_env_file=None, llm_api_key="", embedding_api_key=""), root,
    )
    return settings, SessionStore(settings.db_path)


def order_response(order_id="S20017"):
    return ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("order", "get_order", json.dumps({"order_id": order_id})),
    ])


def make_agent(settings, responses):
    return CustomerServiceAgent(
        settings=settings, llm=ScriptedLLM(responses), registry=ToolRegistry(settings),
    )


def run_request(local, message="我想退S20017。", *, sid=None, uid="USER001", responses=None):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    agent = make_agent(settings, responses or [
        order_response(), ScriptedResponse(text="请您选择商品。"),
    ])
    update = list(ChatService(settings, store).run_turn(message, uid, sid, agent=agent))[-1]
    return update, agent


def check_event(update):
    return next(e for e in update.result.trace if e.type == "state_check")


def business_counts(settings):
    with closing(sqlite3.connect(settings.db_path)) as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (
            "return_requests", "return_confirmations", "return_audit", "return_previews",
        ))


def save_task(store, patch, sid=None):
    sid = sid or store.create("USER001")
    text = json.dumps(patch, ensure_ascii=False)
    tid = store.begin_turn(sid, "USER001", text)
    store.update_return_task(sid, "USER001", tid, patch)
    store.finish_turn(sid, "USER001", tid, [
        {"role": "user", "content": text}, {"role": "assistant", "content": "已存草稿"},
    ], {"answer": "已存草稿"})
    return sid


@pytest.mark.parametrize("enabled", [False, True])
def test_orders02_replays_frozen_omission_with_switch_pair(local, enabled):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": enabled})
    with (ROOT / "results/iteration_v2_recovery/local_after/raw.jsonl").open() as handle:
        row = next(r for r in map(json.loads, handle)
                   if r["id"] == "orders-02" and r["trial"] == 1)
    agent = make_agent(settings, [order_response()] + [
        ScriptedResponse(text=e["result"]["answer"]) for e in row["events"]
    ])
    service, sid = ChatService(settings, store), None
    for event in row["events"]:
        update = list(service.run_turn(event["query"], "USER001", sid, agent=agent))[-1]
        sid = update.session_id
        assert update.result.stop_reason == "answered"
    assert BusinessDB(settings).get_order("S20017", "USER001")["found"]
    view = SessionStore(settings.db_path).get_return_task(sid, "USER001")
    if enabled:
        assert view["task"] is not None, "Required partial draft is absent"
        assert view["task"]["order_id"] == "S20017" and view["task"]["item_id"] is None
        assert view["task"]["revision"] == 1
    else:
        assert view["task"] is None
    assert update.task_view == view or update.task_view.get("task") == view["task"]
    assert len(agent.llm.calls) == 5


@pytest.mark.parametrize("text,expected", [
    ("我想退S20017。", True), (" 我要退 S20017！ ", True), ("我想退 S20017", True),
    ("只查询S20017，不办理退货。", False), ("请查S20017", False),
    ("不是要退S20017", False), ("我不想退S20017。", False),
    ("先不要退S20017", False), ("如果我要退S20017", False),
    ("别人说‘我想退S20017’", False), ("‘我想退S20017。’", False),
    ("我想退S20017吗？", False), ("我想退S20017还是S20001", False),
    ("我想退S20017，但先别保存", False), ("我想退S20017\n先别保存", False),
    ("这是我的订单S20017", False), ("我想退S20017并查询S20001", False),
])
def test_full_utterance_positive_negative_pairs(local, text, expected):
    settings, store = local
    before, counts = data_fingerprint(settings.db_path), business_counts(settings)
    update, agent = run_request(local, text)
    task = store.get_return_task(update.session_id, "USER001")["task"]
    assert bool(task) is expected
    assert len(agent.llm.calls) == 2
    assert data_fingerprint(settings.db_path) == before
    assert business_counts(settings) == counts
    if expected:
        assert task["order_id"] == "S20017" and task["item_id"] is None
        assert task["quantity"] is task["reason"] is None
        assert check_event(update).detail["status"] == "repaired"
    else:
        assert not any(e.type == "state_check" for e in update.result.trace)


@pytest.mark.parametrize("query,uid,responses", [
    ("我想退S20017。", "USER001", [ScriptedResponse(text="已查到订单")]),
    ("我想退S20017。", "USER001", [order_response("S20001"), ScriptedResponse(text="查到了")]),
    ("我想退S99999。", "USER001", [order_response("S99999"), ScriptedResponse(text="查到了")]),
    ("我想退S20017。", "USER002", [order_response(), ScriptedResponse(text="查到了")]),
])
def test_missing_failed_wrong_or_foreign_order_read_is_blocked(local, query, uid, responses):
    _, store = local
    update, _ = run_request(local, query, uid=uid, responses=responses)
    assert update.task_view["task"] is None
    assert store.get_return_task(update.session_id, uid)["task"] is None
    assert check_event(update).detail["reason"] == "missing_owned_order_read"
    assert "当前没有退货草稿" in update.result.answer


def test_previous_turn_order_read_and_current_candidate_list_cannot_authorize_repair(local):
    _, store = local
    first, _ = run_request(local, "只查询S20017，不办理退货。")
    second, agent = run_request(local, sid=first.session_id,
                                responses=[ScriptedResponse(text="之前查过该订单")])
    assert any(m.get("tool_call_id") == "order" for m in agent.llm.calls[0]["messages"])
    assert second.task_view["task"] is None
    assert check_event(second).detail["reason"] == "missing_owned_order_read"
    listed, _ = run_request(local, responses=[ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("list", "list_orders", "{}"),
    ]), ScriptedResponse(text="候选列表包含S20017")])
    assert store.get_return_task(listed.session_id, "USER001")["task"] is None


@pytest.mark.parametrize("changed", [
    {"user_id": "USER002"}, {"order_id": "S20001"}, {"found": "true"},
])
def test_actual_result_must_match_call_object_and_user(local, monkeypatch, changed):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="已查询")])
    original = agent.registry.run
    def tampered(*args, **kwargs):
        payload, events, error = original(*args, **kwargs)
        payload = payload | changed
        events[-1].detail["result"] = payload
        return payload, events, error
    monkeypatch.setattr(agent.registry, "run", tampered)
    update = list(ChatService(settings, store).run_turn(
        "我想退S20017。", "USER001", agent=agent,
    ))[-1]
    assert update.task_view["task"] is None
    assert check_event(update).detail["reason"] == "missing_owned_order_read"


def test_inline_query_does_not_move_draft_target(local):
    _, store = local
    first, _ = run_request(local)
    before = first.task_view["task"]
    query, _ = run_request(local, "只查询S20001，不办理退货。", sid=first.session_id,
                           responses=[order_response("S20001"),
                                      ScriptedResponse(text="已查询另一单")])
    assert query.task_view["task"] == before
    assert store.get_return_task(first.session_id, "USER001")["task"] == before


def test_model_update_is_not_repeated_and_noop_keeps_preview_valid(local):
    settings, store = local
    sid = save_task(store, {"order_id": "S20017", "item_id": 21, "quantity": 1,
                           "reason": "no_reason", "secondary_sale_ok": True})
    returns = ReturnService(settings, store)
    preview = returns.preview(sid, "USER001")
    assert preview["ready"]
    before = store.get_return_task(sid, "USER001")["task"]
    counts = business_counts(settings)
    update, _ = run_request(local, sid=sid)
    assert check_event(update).detail["status"] == "satisfied"
    assert update.task_view["task"] == before
    assert returns.latest_preview(sid, "USER001")["preview_id"] == preview["preview_id"]
    assert business_counts(settings) == counts
    submitted = returns.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert submitted["status"] == "pending_review"


def test_normal_model_write_in_same_turn_is_satisfied_without_extra_revision(local):
    update, agent = run_request(local, responses=[
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("order", "get_order", '{"order_id":"S20017"}'),
            ScriptedToolCall("save", "update_return_task", '{"order_id":"S20017"}'),
        ]), ScriptedResponse(text="已记录订单"),
    ])
    assert update.task_view["task"]["revision"] == 1
    assert check_event(update).detail["status"] == "satisfied"
    assert update.result.tool_calls == 2 and len(agent.llm.calls) == 2


@pytest.mark.parametrize("state", ["other_order", "cancelled", "submitted", "empty_draft"])
def test_existing_tasks_are_never_overwritten_or_revived(local, state):
    settings, store = local
    patch = {"order_id": "S20001" if state == "other_order" else "S20017"}
    if state == "submitted":
        patch |= {"item_id": 21, "quantity": 1, "reason": "no_reason", "secondary_sale_ok": True}
    if state == "empty_draft":
        patch = {"reason": "no_reason"}
    sid = save_task(store, patch)
    if state == "cancelled":
        tid = store.begin_turn(sid, "USER001", "中止退货草稿")
        store.cancel_return_task(sid, "USER001", tid)
        store.interrupt_turn(sid, "USER001", tid)
    elif state == "submitted":
        returns = ReturnService(settings, store)
        preview = returns.preview(sid, "USER001")
        returns.confirm_and_submit(sid, "USER001", preview["preview_id"])
    before = store.get_return_task(sid, "USER001")["task"]
    counts = business_counts(settings)
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    conditional = store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"},
                                          only_if_absent=True)
    assert conditional["state_write"] == "conflict" and conditional["task"] == before
    store.interrupt_turn(sid, "USER001", tid)
    update, _ = run_request(local, sid=sid)
    assert update.task_view["task"] == before
    assert check_event(update).detail["status"] == "conflict"
    assert business_counts(settings) == counts
    if state == "submitted":
        assert "本轮没有新建申请" in update.result.answer


def test_conditional_store_write_is_atomic_under_same_and_different_order_races(local):
    _, store = local
    for ids in (["S20017"] * 6, ["S20017", "S20001"] * 3):
        sid = store.create("USER001")
        tid = store.begin_turn(sid, "USER001", "我想退S20017。")
        def write(oid, sid=sid, tid=tid):
            return store.update_return_task(sid, "USER001", tid, {"order_id": oid},
                                            only_if_absent=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            views = list(pool.map(write, ids))
        assert sum(v["state_write"] == "created" for v in views) == 1
        assert set(v["state_write"] for v in views) <= {"created", "satisfied", "conflict"}
        final = store.get_return_task(sid, "USER001")["task"]
        assert final["revision"] == 1
        assert all(v["task"] == final for v in views)


def test_conditional_store_enforces_owner_turn_patch_and_backend_order(local):
    _, store = local
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    for uid, turn in [("USER002", tid), ("USER001", "old-turn")]:
        with pytest.raises((PermissionError, TaskValidationError)):
            store.update_return_task(sid, uid, turn, {"order_id": "S20017"}, only_if_absent=True)
    for patch in [{"item_id": 21}, {"order_id": "S20017", "item_id": 21},
                  {"order_id": None}, {"order_id": "unknown"}]:
        with pytest.raises(TaskValidationError):
            store.update_return_task(sid, "USER001", tid, patch, only_if_absent=True)
    with pytest.raises(TaskValidationError):
        store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"},
                                 only_if_absent=True, cancel=True)
    assert store.get_return_task(sid, "USER001")["task"] is None


def test_guard_reentry_does_not_write_twice(local):
    settings, store = local
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="请选商品")])
    result = agent.chat("我想退S20017。")
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    def check():
        return check_order_draft(store, sid, "USER001", tid, "我想退S20017。",
                                 result.trace, "answered", RunBudget(120, 16))
    assert check().detail["status"] == "repaired"
    first = store.get_return_task(sid, "USER001")["task"]
    assert check().detail["status"] == "satisfied"
    assert store.get_return_task(sid, "USER001")["task"] == first


@pytest.mark.parametrize("committed", [False, True])
def test_write_failure_or_lost_write_receipt_reads_actual_state_and_retry_is_safe(
    local, monkeypatch, committed,
):
    settings, store = local
    original = store.update_return_task
    def fail(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise sqlite3.OperationalError("injected write/response failure")
    monkeypatch.setattr(store, "update_return_task", fail)
    before = business_counts(settings)
    update, agent = run_request(local, responses=[
        order_response(), ScriptedResponse(text="申请已创建成功。"),
    ])
    task = store.get_return_task(update.session_id, "USER001")["task"]
    assert bool(task) is committed
    assert update.task_view["task"] == task
    assert update.result.stop_reason == "state_check_error"
    assert check_event(update).detail["status"] == "error"
    correction = update.result.answer.split("状态更正", 1)[1]
    assert ("已记录在退货草稿中" in correction) is committed
    assert "尚未提交申请" in correction if committed else "当前没有退货草稿" in correction
    assert agent.messages[-1]["content"] == update.result.answer
    snapshot = store.load(update.session_id, "USER001")
    assert snapshot["last_result"]["answer"] == update.result.answer
    final = next(e for e in update.result.trace if e.type == "final")
    assert final.detail["text"] == snapshot["messages"][-1]["content"] == update.result.answer
    monkeypatch.setattr(store, "update_return_task", original)
    retry, _ = run_request(local, sid=update.session_id)
    assert retry.task_view["task"]["revision"] == 1
    assert check_event(retry).detail["status"] == ("satisfied" if committed else "repaired")
    assert business_counts(settings) == before


def test_readback_failure_displays_unknown_instead_of_stale_empty_task(local, monkeypatch):
    _, store = local
    original, calls = store.get_return_task, 0
    def fail_third_read(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise sqlite3.OperationalError("readback unavailable")
        return original(*args)
    monkeypatch.setattr(store, "get_return_task", fail_third_read)
    update, _ = run_request(local)
    assert calls == 3
    assert update.task_view == {"state_unavailable": True}
    assert update.result.stop_reason == "state_check_error"
    assert check_event(update).detail["reason"] == "state_read_failed"
    assert "不能确认是否保存" in update.result.answer
    assert original(update.session_id, "USER001")["task"]["order_id"] == "S20017"


def test_readback_cannot_report_repaired_when_postcondition_disappeared(local, monkeypatch):
    _, store = local
    original = store.update_return_task
    def delete_after_save(*args, **kwargs):
        view = original(*args, **kwargs)
        with closing(sqlite3.connect(store.db_path)) as conn, conn:
            conn.execute("DELETE FROM return_tasks WHERE session_id=?", (args[0],))
        return view
    monkeypatch.setattr(store, "update_return_task", delete_after_save)
    update, _ = run_request(local)
    assert update.task_view["task"] is None
    assert check_event(update).detail["reason"] == "postcondition_failed"
    assert check_event(update).detail["status"] == "error"
    assert "当前没有退货草稿" in update.result.answer


def test_sql_error_rolls_back_without_business_side_effects(local):
    settings, store = local
    before = business_counts(settings)
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("CREATE TRIGGER fail_p0 BEFORE INSERT ON return_tasks "
                     "BEGIN SELECT RAISE(ABORT,'injected'); END")
    update, _ = run_request(local)
    assert check_event(update).detail["status"] == "error"
    assert update.task_view["task"] is None
    assert business_counts(settings) == before


def test_budget_expired_after_final_model_reply_does_not_write(local):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    class ExpiringLLM(ScriptedLLM):
        def chat(self, *args, **kwargs):
            response = super().chat(*args, **kwargs)
            if len(self.calls) == 2:
                self.budget.started -= self.budget.timeout_s + 1
            return response
    agent = make_agent(settings, [])
    agent.llm = ExpiringLLM([order_response(), ScriptedResponse(text="已保存草稿")])
    update = list(ChatService(settings, store).run_turn(
        "我想退S20017。", "USER001", agent=agent,
    ))[-1]
    assert update.result.stop_reason == "budget_exceeded"
    assert check_event(update).detail["reason"] == "budget_expired"
    assert update.task_view["task"] is None
    assert "当前没有退货草稿" in update.result.answer
    assert store.load(update.session_id, "USER001")["running"] is False


@pytest.mark.parametrize("expire_on", [1, 2])
def test_budget_is_checked_inside_transaction_before_any_write(local, monkeypatch, expire_on):
    _, store = local
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    budget = RunBudget(120, 16)
    original_connect, transactions, checks = store._connect, [], 0
    def connect():
        conn = original_connect()
        conn.set_trace_callback(transactions.append)
        return conn
    def expired():
        nonlocal checks
        assert "BEGIN IMMEDIATE" in transactions
        checks += 1
        if checks == expire_on:
            raise BudgetExceeded("expired while waiting for transaction or validating")
        return 1
    monkeypatch.setattr(store, "_connect", connect)
    monkeypatch.setattr(budget, "remaining", expired)
    with pytest.raises(BudgetExceeded):
        store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"},
                                 only_if_absent=True, budget=budget)
    assert not any(sql.startswith("INSERT INTO return_tasks") for sql in transactions)
    assert store.get_return_task(sid, "USER001")["task"] is None


def test_non_answered_turns_never_repair(local):
    settings, store = local
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="请选商品")])
    result = agent.chat("我想退S20017。")
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    for reason in ["running", "interrupted", "llm_error", "max_steps", "budget_exceeded"]:
        event = check_order_draft(store, sid, "USER001", tid, "我想退S20017。",
                                  result.trace, reason, RunBudget(120, 16))
        assert event.detail["reason"] == "turn_not_answered"
        assert store.get_return_task(sid, "USER001")["task"] is None


@pytest.mark.parametrize("fail_stream", [False, True])
def test_streamed_claim_is_explicitly_corrected_at_final_authoritative_state(local, fail_stream):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    class StreamingLLM(ScriptedLLM):
        def chat_stream(self, *args, **kwargs):
            response = self.chat(*args, **kwargs)
            if not response.wants_tools:
                yield "申请已经创建成功。"
                if fail_stream:
                    raise OSError("injected stream disconnect")
            yield response
    agent = make_agent(settings, [])
    agent.llm = StreamingLLM([order_response(), ScriptedResponse(text="申请已经创建成功。")])
    updates = list(ChatService(settings, store).run_turn("我想退S20017。", "USER001", agent=agent))
    assert any(u.result.stop_reason == "running" and "申请已经创建成功" in u.result.answer
               for u in updates)
    final = updates[-1]
    assert "状态更正" in final.result.answer
    assert bool(final.task_view["task"]) is not fail_stream
    assert "当前没有退货草稿" in final.result.answer if fail_stream else (
        "尚未提交申请" in final.result.answer
    )
    assert final.result.stop_reason == ("llm_error" if fail_stream else "answered")
    assert agent.messages[-1]["content"] == final.result.answer


def test_closed_stream_before_terminal_does_not_repair_and_restart_can_retry(local):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="请选商品")])
    stream = ChatService(settings, store).run_turn("我想退S20017。", "USER001", agent=agent)
    with closing(stream):
        for update in stream:
            if update.result.tool_calls == 1:
                break
    sid = update.session_id
    assert store.get_return_task(sid, "USER001")["task"] is None
    fresh = SessionStore(settings.db_path)
    assert fresh.load(sid, "USER001")["last_result"]["stop_reason"] == "interrupted"
    retry, _ = run_request((settings, fresh), sid=sid)
    assert retry.task_view["task"]["revision"] == 1


def test_interruption_after_commit_before_finish_recovers_without_duplicate_write(
    local, monkeypatch,
):
    settings, store = local
    def interrupted(*args):
        raise OSError("injected finish loss")
    monkeypatch.setattr(store, "finish_turn", interrupted)
    with pytest.raises(OSError, match="finish loss"):
        run_request(local)
    sid = store.list("USER001")[0]["session_id"]
    fresh = SessionStore(settings.db_path)
    assert fresh.load(sid, "USER001")["last_result"]["stop_reason"] == "interrupted"
    assert fresh.get_return_task(sid, "USER001")["task"]["revision"] == 1
    retry, _ = run_request((settings, fresh), sid=sid)
    assert check_event(retry).detail["status"] == "satisfied"
    assert retry.task_view["task"]["revision"] == 1


def test_ui_task_trace_history_and_restore_show_the_same_authoritative_state(local, monkeypatch):
    import app

    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="请选择商品")])
    state = {"user_id": "USER001", "agent": agent}
    frames = list(app.on_send("我想退S20017。", [], state, app.USER_CHOICES[0]))
    final = frames[-1]
    assert "S20017" in final[5] and "尚未提交" in final[5]
    assert "服务端状态检查" in final[2]
    assert "trace-step state-check" in final[2]
    assert final[2].count("· 最终回答") == 1
    assert "tool calls: 1" in final[2]
    sid = state["session_id"]
    restored = app.on_restore(sid, {}, app.USER_CHOICES[0])
    assert restored[0] == final[0]
    assert restored[2] == final[2]
    assert restored[5] == final[5]
    snapshot = store.load(sid, "USER001")
    assert final[0][-1]["content"] == snapshot["last_result"]["answer"]
    assert snapshot["messages"][-1]["content"] == final[0][-1]["content"]
    assert store.get_return_task(sid, "USER001")["task"]["item_id"] is None
    assert len(agent.llm.calls) == 2


def test_ui_unknown_state_is_not_rendered_as_empty_or_saved_draft():
    import app

    rendered = app.render_return_task({"state_unavailable": True})
    assert "状态无法读取" in rendered
    assert "尚无退货草稿" not in rendered and "尚未提交" not in rendered


def test_switch_defaults_off_and_can_be_enabled_from_environment(monkeypatch):
    monkeypatch.delenv("ACS_RETURN_STATE_CHECK_ENABLED", raising=False)
    assert Settings(_env_file=None).return_state_check_enabled is False
    monkeypatch.setenv("ACS_RETURN_STATE_CHECK_ENABLED", "true")
    assert Settings(_env_file=None).return_state_check_enabled is True


@pytest.mark.parametrize("readback_fails", [False, True])
def test_ui_finish_failure_rereads_committed_draft_or_shows_unknown(
    local, monkeypatch, readback_fails,
):
    import app

    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    original_read = store.get_return_task
    def fail_finish(*args):
        if readback_fails:
            def unavailable(*args):
                raise sqlite3.OperationalError("injected UI readback failure")
            monkeypatch.setattr(store, "get_return_task", unavailable)
        raise OSError("injected final response loss")
    monkeypatch.setattr(store, "finish_turn", fail_finish)
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="请您选择商品")])
    state = {"user_id": "USER001", "agent": agent}
    final = list(app.on_send("我想退S20017。", [], state, app.USER_CHOICES[0]))[-1]
    task = original_read(state["session_id"], "USER001")["task"]
    assert task["order_id"] == "S20017" and task["revision"] == 1
    assert "尚无退货草稿" not in final[5]
    assert "final response loss" in final[2]
    if readback_fails:
        assert "状态无法读取" in final[5]
        assert "状态无法核实" in final[0][-1]["content"]
    else:
        assert "订单：S20017" in final[5]
        assert final[0] == store.load(state["session_id"], "USER001")["history"]
        assert "状态更正" in final[0][-1]["content"]
        assert "本次回答中断" in final[0][-1]["content"]
    assert len(agent.llm.calls) == 2


def test_unbound_existing_draft_notice_does_not_print_none(local):
    _, store = local
    sid = save_task(store, {"reason": "no_reason"})
    update, _ = run_request(local, sid=sid)
    assert check_event(update).detail["status"] == "conflict"
    assert "尚未绑定订单" in update.result.answer
    assert "订单 None" not in update.result.answer
    assert update.task_view["task"]["order_id"] is None


def test_ui_repeated_query_before_turn_reservation_does_not_reuse_old_answer(local, monkeypatch):
    import app

    settings, store = local
    previous, _ = run_request(local)
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    def fail_begin(*args):
        raise OSError("injected reservation failure")
    monkeypatch.setattr(store, "begin_turn", fail_begin)
    agent = make_agent(settings, [ScriptedResponse(text="must not be called")])
    state = {"user_id": "USER001", "agent": agent, "session_id": previous.session_id}
    final = list(app.on_send("我想退S20017。", [], state, app.USER_CHOICES[0]))[-1]
    assert "系统出现异常" in final[0][-1]["content"]
    assert final[0] != store.load(previous.session_id, "USER001")["history"]
    assert not agent.llm.calls


def test_enabled_check_cannot_reuse_another_users_session_in_ui(local, monkeypatch):
    import app

    settings, store = local
    first, _ = run_request(local)
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="请选商品")])
    state = {"user_id": "USER001", "agent": agent, "session_id": first.session_id}
    rejected = list(app.on_send("我想退S20017。", [], state, "USER002"))[-1]
    assert state == {} and rejected[0] == []
    assert "S20017" not in rejected[5] and not agent.llm.calls
    assert store.get_return_task(first.session_id, "USER001")["task"]["revision"] == 1
    state.update(user_id="USER002", agent=agent)
    final = list(app.on_send("我想退S20017。", [], state, "USER002"))[-1]
    assert store.get_return_task(state["session_id"], "USER002")["task"] is None
    assert "订单：S20017" not in final[5]


def test_existing_store_accepts_order_only_claim_with_original_source(local):
    _, store = local
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "我想退S20017。")
    view = store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"})
    assert view["task"]["order_id"] == "S20017" and view["task"]["item_id"] is None
    assert view["task"]["sources"]["order_id"]["user_text"] == "我想退S20017。"


def test_reading_order_is_not_authorization(local):
    settings, store = local
    settings = settings.model_copy(update={"return_state_check_enabled": True})
    agent = make_agent(settings, [order_response(), ScriptedResponse(text="我想退S20017。")])
    update = list(ChatService(settings, store).run_turn(
        "只查询S20017，不办理退货。", "USER001", agent=agent,
    ))[-1]
    assert store.get_return_task(update.session_id, "USER001")["task"] is None
