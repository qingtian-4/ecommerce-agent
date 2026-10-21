"""Customer progress is projected from observable events, never model reasoning."""

import json
from dataclasses import asdict

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry, TraceEvent
from acs.progress import progress_events, render_progress
from acs.service_agent import AgentResult, CustomerServiceAgent
from acs.sessions import SessionStore

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


def test_progress_safe_fields_stable_ids_and_terminal_are_authoritative():
    result = AgentResult(
        answer="伪造已退款",
        trace=[
            TraceEvent("route_selected", "orders", {"system_prompt": "private-sentinel"}),
            TraceEvent("tool_call", "get_order", {"arguments": "private-sentinel"}),
            TraceEvent(
                "tool_result",
                "get_order",
                {
                    "result": {
                        "found": True,
                        "order_id": "O10024",
                        "status": "delivered",
                        "user_name": "private-sentinel",
                        "items": [],
                    }
                },
            ),
            TraceEvent("retry_scheduled", "llm", {"attempt": 1, "delay_s": 0.2}),
        ],
    )
    events = progress_events("sid", "tid", result, "completed")
    assert len({e["event_id"] for e in events}) == len(events)
    assert all(e["session_id"] == "sid" and e["turn_id"] == "tid" for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    text = render_progress(events)
    assert "正在查询订单" in text and "O10024" in text and "正在重试" in text
    assert "伪造已退款" not in text and "private-sentinel" not in text
    assert progress_events("sid", "tid", result, "completed") == events


def test_progress_persisted_once_per_semantic_change_and_restored(settings, monkeypatch):
    settings = settings.model_copy(update={"execution_progress_enabled": True})
    store = SessionStore(settings.db_path)
    writes = []
    original = store.save_progress

    def observe(*args, **kwargs):
        writes.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "save_progress", observe)
    model = ScriptedLLM(
        [
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[
                    ScriptedToolCall("order", "get_order", '{"order_id":"O10024"}'),
                ],
            ),
            ScriptedResponse(text="查询完成"),
        ]
    )
    agent = CustomerServiceAgent(settings=settings, llm=model, registry=ToolRegistry(settings))
    updates = list(ChatService(settings, store).run_turn("查订单", "USER001", agent=agent))
    saved = store.load(updates[-1].session_id, "USER001")["last_result"]
    assert saved["totals"]["progress"] == updates[-1].result.totals["progress"]
    assert 1 <= len(writes) <= 2
    assert saved["totals"]["progress"][-1]["kind"] == "turn_completed"
    assert all(u.result.totals["progress"] for u in updates)


def test_feedback_is_owned_separate_and_not_added_to_model_history(settings):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "查询")
    store.finish_turn(
        sid,
        "USER001",
        tid,
        [{"role": "user", "content": "查询"}, {"role": "assistant", "content": "回答"}],
        asdict(AgentResult(answer="回答")),
    )
    before = store.load(sid, "USER001")
    store.add_feedback(sid, "USER001", tid, "unresolved")
    store.add_feedback(sid, "USER001", tid, "helpful")
    assert store.load(sid, "USER001")["messages"] == before["messages"]
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_feedback").fetchone()[0] == 2
    with pytest.raises(PermissionError):
        store.add_feedback(sid, "USER002", tid, "helpful")
    with pytest.raises(ValueError):
        store.add_feedback(sid, "USER001", tid, "modify_prompt")
    assert "unresolved" not in json.dumps(store.load(sid, "USER001")["messages"])


def test_running_checkpoint_does_not_show_previous_turn_as_current_and_survives_restart(settings):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "新问题")
    events = progress_events(sid, tid, AgentResult(answer="", stop_reason="running"), "running")
    store.save_progress(sid, "USER001", tid, events)
    assert store.load(sid, "USER001")["last_result"]["stop_reason"] == "running"
    store.recover_interrupted()
    saved = store.load(sid, "USER001")["last_result"]["totals"]["progress"]
    assert saved[0] == events[0] and saved[-1]["kind"] == "turn_interrupted"
    assert len({e["event_id"] for e in saved}) == len(saved)


def test_explicit_page_selection_and_feedback_use_owned_state(settings, monkeypatch):
    import app

    settings = settings.model_copy(update={"execution_progress_enabled": True})
    store = SessionStore(settings.db_path)
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    with store._connect() as conn:
        item = conn.execute(
            "SELECT id FROM order_items WHERE order_id='O10024' LIMIT 1"
        ).fetchone()[0]
    state = {}
    output = app.on_select_return_object(f"O10024:{item}", state, "USER001")
    sid = state["session_id"]
    assert store.get_return_task(sid, "USER001")["task"]["item_id"] == item
    assert store.get_return_task(sid, "USER001")["task"]["quantity"] is None
    assert "尚未提交" in output[0][-1]["content"]
    assert "反馈已记录" in app.on_feedback("helpful", state, "USER001")
    foreign = app.on_select_return_object(f"O10024:{item}", {}, "USER002")
    assert "不属于当前用户" in foreign[0][-1]["content"]


def test_progress_corrupt_result_and_html_do_not_leak_raw_details():
    result = AgentResult(
        answer="<script>bad</script>",
        trace=[
            TraceEvent(
                "tool_result", "list_orders", {"result": {"orders": "corrupt", "count": 99}}
            ),
            TraceEvent(
                "tool_result",
                "get_order",
                {
                    "result": {
                        "found": True,
                        "order_id": "<script>bad</script>",
                        "items": None,
                    }
                },
            ),
        ],
    )
    text = render_progress(progress_events("sid", "tid", result, "completed"))
    assert "<script>" not in text and "99" not in text


def test_candidate_choices_expand_owned_items_from_data_layer(settings, monkeypatch):
    import app

    monkeypatch.setattr(app, 'SETTINGS', settings)
    choices = app.order_selection_choices('USER001')['choices']
    assert choices
    assert any(value.startswith('O10024:') for label, value in choices)
    with SessionStore(settings.db_path)._connect() as conn:
        own = {r[0] for r in conn.execute("SELECT order_id FROM orders WHERE user_id='USER001'")}
    assert all(value.split(':')[0] in own for label, value in choices)


@pytest.mark.parametrize('label,detail,verified', [
    ('return_order_draft', {'status': 'error', 'reason': 'state_read_failed'}, False),
    ('write_reconciliation', {'status': 'unknown'}, False),
    ('return_order_draft', {'status': 'repaired', 'observed_status': 'draft'}, True),
])
def test_state_progress_never_claims_readback_without_receipt(label, detail, verified):
    result = AgentResult(answer='', stop_reason='state_uncertain', trace=[
        TraceEvent('state_check', label, detail),
    ])
    events = progress_events('sid', 'tid', result, 'completed')
    text = render_progress(events)
    assert ('已重新核对后台状态' in text) is verified
    assert ('后台状态尚未核实' in text) is (not verified)
    assert events[1]['status'] == ('observed' if verified else 'unverified')
