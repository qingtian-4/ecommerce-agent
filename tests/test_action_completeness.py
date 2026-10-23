# ruff: noqa: F811
"""New grading contract and mixed authorization regressions; no network or live scores."""

import json

import pytest
from scripts.eval_action_completeness import grade

from .conftest import ScriptedResponse
from .test_return_state_check import (  # noqa: F401
    business_counts,
    check_event,
    local,
    make_agent,
    order_response,
    run_request,
    save_task,
    scenario,
)


@pytest.mark.parametrize(
    "query",
    [
        "记录S20017的退货草稿，同时介绍V201的参数",
        "我想退S20017，同时说明退货期限",
        "我要退S20017，同时说明退货政策，同时介绍V201的参数",
    ],
)
def test_mixed_explicit_order_request_repairs_only_order_and_preserves_answer(local, query):
    settings, store = local
    before = business_counts(settings)
    final, agent = run_request(local, query)
    task = store.get_return_task(final.session_id, "USER001")["task"]
    assert task and task["order_id"] == "S20017"
    assert all(task[k] is None for k in ("item_id", "quantity", "reason", "condition"))
    assert task["revision"] == 1
    assert task["sources"]["order_id"]["user_text"] == query
    assert final.result.answer.startswith("请您选择商品。")
    assert check_event(final).detail["status"] == "repaired"
    assert final.task_view["task"] == task
    snap = store.load(final.session_id, "USER001")
    assert final.result.answer == agent.messages[-1]["content"] == snap["messages"][-1]["content"]
    assert final.result.answer == next(
        e.detail["text"] for e in final.result.trace if e.type == "final"
    )
    assert business_counts(settings) == before
    assert len(agent.llm.calls) == 2


@pytest.mark.parametrize(
    "query",
    [
        "我不想退S20017，同时说明退货政策",
        "先不要记录S20017的退货草稿",
        "如果我要退S20017，同时说明退货期限",
        "别人说“我想退S20017”，同时说明退货政策",
        "我想退S20017，同时查询S20001",
        "我想退S20017，同时说明退货政策可以吗？",
        "我想退S20017，同时不要介绍V201的参数",
        "我想退S20017，同时介绍V201参数并取消草稿",
        "我想退S20017，同时说明退货政策，然后取消",
        "记录S20017的退货草稿\n先别保存",
    ],
)
def test_unsupported_qualified_or_negated_whole_message_never_authorizes(local, query):
    final, _ = run_request(local, query)
    assert final.task_view["task"] is None
    assert not any(e.type == "state_check" for e in final.result.trace)


@pytest.mark.parametrize("initial", ["other", "cancelled", "same"])
def test_mixed_request_never_overwrites_resurrects_or_revises_existing(local, initial):
    _, store = local
    sid = save_task(store, {"order_id": "S20001" if initial == "other" else "S20017"})
    if initial == "cancelled":
        tid = store.begin_turn(sid, "USER001", "中止退货草稿")
        store.cancel_return_task(sid, "USER001", tid)
        store.interrupt_turn(sid, "USER001", tid)
    before = store.get_return_task(sid, "USER001")["task"]
    final, _ = run_request(local, "我想退S20017，同时说明退货期限", sid=sid)
    assert final.task_view["task"] == before
    assert check_event(final).detail["status"] == ("satisfied" if initial == "same" else "conflict")


def calibrated_row():
    task = dict(
        order_id="O10024", status="draft", item_id=None, quantity=None, reason=None, revision=1
    )
    return dict(
        task=task,
        initial=None,
        answer="P001 参数529元；7天。",
        synced=True,
        view_matches=True,
        state_unavailable=False,
        business_unchanged=True,
        source_matches=True,
        stop_reason="answered",
    )


def test_grading_contract_does_not_confuse_safe_empty_and_full_user_success():
    case = dict(goal_order="O10024", authorized=True, consultation=["P001", "529", "7"])
    row = calibrated_row()
    assert grade(case, row)["strict_complete"]
    for patch in [
        dict(task=None),
        dict(answer="7天。"),
        dict(synced=False),
        dict(task=row["task"] | {"item_id": 4}),
        dict(state_unavailable=True, answer="已保存。"),
    ]:
        assert not grade(case, row | patch)["strict_complete"]
    assert (
        grade(case | {"blocked": True}, row | {"task": None})["classification"] == "safe_rejection"
    )
    repeated = grade(case | {"repeat": True}, row | {"task": row["task"] | {"revision": 2}})
    assert repeated["duplicate_write"] and not repeated["strict_complete"]


def test_frozen_goals_include_both_consultation_subtasks_independent_of_tool_results():
    from scripts.eval_action_completeness import FROZEN

    cases = json.loads((FROZEN / "cases_v2.json").read_text())["cases"]
    case = next(c for c in cases if c["id"] == "mixed-both")
    assert set(case["consultation"]) == {"SoundCore", "529", "7", "15"}
    assert not grade(case, calibrated_row() | {"answer": "7天，15天。"})["consultation_complete"]


@pytest.mark.parametrize("error", [{"error_type": "timeout"}, {"error": "failed"}])
def test_success_shaped_order_receipt_with_error_is_not_authority(error):
    from acs.cs_tools import TraceEvent
    from acs.return_state_check import _order_read

    events = [
        TraceEvent("tool_call", "get_order", {"arguments": '{"order_id":"O10024"}'}),
        TraceEvent(
            "tool_result",
            "get_order",
            {"result": dict(found=True, order_id="O10024", user_id="USER001") | error},
        ),
    ]
    assert _order_read(events, "O10024", "USER001") is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("condition", "sealed"),
        ("problem_description", "broken"),
        ("secondary_sale_ok", True),
        ("quality_non_user_damage", True),
        ("item_id", 4),
        ("quantity", 1),
        ("reason", "quality"),
    ],
)
def test_grader_rejects_all_extra_order_fields(field, value):
    case = dict(goal_order="O10024", authorized=True, consultation=[])
    row = calibrated_row()
    result = grade(case, row | {"task": row["task"] | {field: value}})
    assert result["unauthorized_write"] and not result["strict_complete"]


def test_grader_keeps_initial_save_separate_from_final_cancel_and_checks_false_claims():
    case = dict(goal_order="O10024", goal_status="cancelled", authorized=True, consultation=[])
    row = calibrated_row()
    result = grade(case, row)
    assert result["action_complete"] and not result["strict_complete"]
    for text in ["申请已提交", "已提交退货申请", "退款已完成", "退款已到账"]:
        result = grade(case | {"goal_status": "draft"}, row | {"answer": text})
        assert not result["state_consistent"] and not result["strict_complete"]
    assert grade(case, row | {"source_matches": False})["unauthorized_write"]


def test_mixed_ui_handler_all_frames_and_restore_use_actual_task(local, monkeypatch):
    import app

    settings, store = local
    settings = settings.model_copy(
        update={"return_state_check_enabled": True, "write_receipt_guard_enabled": True}
    )
    monkeypatch.setattr(app, "SETTINGS", settings)
    monkeypatch.setattr(app, "_SESSION_STORE", store)
    agent = make_agent(
        settings, [order_response(), ScriptedResponse(text="请选择商品，尚未提交申请。")]
    )
    state = {"user_id": "USER001", "agent": agent}
    frames = list(app.on_send("我想退S20017，同时说明退货期限", [], state, app.USER_CHOICES[0]))
    for frame in frames:
        assert "申请已提交" not in str(frame[0]) and "退款已到账" not in str(frame[0])
    task = store.get_return_task(state["session_id"], "USER001")["task"]
    assert task["order_id"] == "S20017" and task["item_id"] is None
    assert "S20017" in frames[-1][5] and "state-check" in frames[-1][2]
    assert (
        frames[-1][0][-1]["content"]
        == store.load(state["session_id"], "USER001")["messages"][-1]["content"]
    )


def test_correct_short_read_answer_is_not_replaced_by_order_guard(local):
    from acs.database import BusinessDB

    from .conftest import ScriptedToolCall

    settings, _ = local
    actual = BusinessDB(settings).get_product("V201")
    assert actual["found"] is True
    answer = actual["name"] + "。"
    final, _ = run_request(
        local,
        "V201叫什么名字？",
        responses=[
            ScriptedResponse(
                wants_tools=True,
                tool_calls=[ScriptedToolCall("p", "get_product", '{"product_id":"V201"}')],
            ),
            ScriptedResponse(text=answer),
        ],
    )
    assert final.result.answer == answer
    assert final.task_view["task"] is None


def test_grader_wrong_order_is_an_unauthorized_write_even_with_matching_source_shape():
    row = calibrated_row()
    case = dict(goal_order="O10024", authorized=True, consultation=[])
    assert grade(case, row | {"task": row["task"] | {"order_id": "O10025"}})["unauthorized_write"]
