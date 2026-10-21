"""c9afdbf review regressions: real state/tools and hostile model output, no paid API."""

import copy
import json
import shutil
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.database import BusinessDB
from acs.order_candidates import order_candidate_response
from acs.service_agent import CustomerServiceAgent
from acs.task_eval import judge_record

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall
from .test_order_candidate_facts import local as local
from .test_order_candidate_facts import no_network as no_network
from .test_order_candidate_facts import p1_scenario as p1_scenario
from .test_order_candidate_facts import sample_trace
from .test_return_submission import flow as flow
from .test_return_submission import image_bytes, write

CANCEL_NO = [
    "我不是要取消退货草稿，只是问问。",
    "不打算取消退货草稿",
    "他说“取消退货草稿”，不要照做。",
    "如果取消退货草稿会怎样？",
    "先暂停，不是取消退货草稿。",
    "能否解释“清空草稿”？",
    "没有要求取消退货草稿",
]
CANCEL_YES = ["中止退货草稿", "请取消退货草稿。", "中止这个退货草稿，不要取消订单"]
QUERY = "我的订单一共买了多少件商品？只给总件数，不要列表。"
FULL = "请列出我的全部订单和全部商品明细。"


@pytest.mark.parametrize("text", CANCEL_NO + CANCEL_YES)
def test_cancel_authorization_comes_from_full_current_user_turn(flow, text):
    _, store, _, sid = flow
    before = store.get_return_task(sid, "USER001")["task"]
    tid = store.begin_turn(sid, "USER001", text)
    if text in CANCEL_NO:
        with pytest.raises(ValueError):
            store.cancel_return_task(sid, "USER001", tid)
        assert store.get_return_task(sid, "USER001")["task"] == before
    else:
        assert store.cancel_return_task(sid, "USER001", tid)["task"]["status"] == "cancelled"


@pytest.mark.parametrize(
    "kind", ["error", "count", "duplicate_order", "duplicate_item", "partial", "truncated"]
)
def test_every_order_renderer_rejects_incomplete_envelopes(local, kind):
    settings, _ = local
    payload = ToolRegistry(settings)._list_orders(current_user_id="USER001")
    if kind == "error":
        payload["error"] = "failed"
    elif kind == "count":
        payload["count"] += 1
    elif kind == "duplicate_order":
        payload["orders"].append(copy.deepcopy(payload["orders"][0]))
        payload["count"] += 1
    elif kind == "duplicate_item":
        payload["orders"][0]["items"].append(copy.deepcopy(payload["orders"][0]["items"][0]))
    else:
        payload[kind] = True
    trace = sample_trace(payload["orders"])
    trace[1].detail["result"] = payload
    assert order_candidate_response(trace, {"task": None}, "answered", user_text=FULL) is None


class StreamingWrong(ScriptedLLM):
    def chat_stream(self, messages, **kwargs):
        response = self.chat(messages, **kwargs)
        if not response.wants_tools:
            yield response.text
        yield response


@pytest.mark.parametrize("failure", ["normal", "no_tool", "error"])
def test_guarded_frames_never_show_unverified_model_text(local, failure):
    settings, store = local
    settings = settings.model_copy(update={"order_aggregation_enabled": True})
    responses = [
        ScriptedResponse(
            wants_tools=True, tool_calls=[ScriptedToolCall("orders", "list_orders", "{}")]
        ),
        ScriptedResponse(text="合计25件商品。"),
    ]
    if failure == "no_tool":
        responses = responses[1:]
    llm = StreamingWrong(responses)
    if failure == "error":
        original = llm.chat_stream

        def broken(messages, **kwargs):
            for value in original(messages, **kwargs):
                yield value
                if isinstance(value, str):
                    raise RuntimeError("stream lost")

        llm.chat_stream = broken
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    updates = list(ChatService(settings, store).run_turn(QUERY, "USER001", agent=agent))
    assert all(
        "25件" not in u.result.answer and "25件" not in json.dumps(u.history) for u in updates
    )
    assert store.load(updates[-1].session_id, "USER001")["history"] == updates[-1].history
    if failure == "normal":
        assert "29 件商品" in updates[-1].result.answer


@pytest.mark.parametrize("days", [0, 7, 8, -1, None])
def test_order_and_preview_share_policy_and_invalid_date_boundary(flow, tmp_path, days):
    settings, store, _, sid = flow
    docs = tmp_path / "docs"
    shutil.copytree(settings.docs_dir, docs)
    path = docs / "return_policy.md"
    path.write_text(
        path.read_text().replace("**7 天内**", "**10 天内**").replace("| 7 天 |", "| 10 天 |")
    )
    settings = settings.model_copy(update={"docs_dir": docs})
    raw = (
        (settings.reference_date - timedelta(days=days)).isoformat()
        if days is not None
        else "invalid-date"
    )
    with closing(store._connect()) as conn, conn:
        conn.execute("UPDATE orders SET delivered_at=? WHERE order_id='O10024'", (raw,))
    order = BusinessDB(settings).get_order("O10024", "USER001")
    assert order["return_window_days"] == 10
    assert order["within_return_window"] is (days is not None and 0 <= days <= 10)


@pytest.mark.parametrize("raw", ["not json", "[]", '{"passed":true}', "null"])
def test_judge_errors_preserve_exact_original_response_and_reason(raw):
    class Client:
        usages = []

        def complete(self, *args, **kwargs):
            return SimpleNamespace(text=raw)

    result = judge_record(Client(), {"events": []})
    assert result["raw_response_text"] == raw
    assert result["judgment_status"] == "unjudged"
    assert result["diagnostic"]["exception_type"] and result["diagnostic"]["reason"]


def test_submitted_request_cannot_be_revoked_by_draft_cancel(flow):
    _, store, service, sid = flow
    created = service.confirm_and_submit(
        sid, "USER001", service.preview(sid, "USER001")["preview_id"]
    )
    tid = store.begin_turn(sid, "USER001", "中止退货草稿")
    with pytest.raises(ValueError, match="已经提交"):
        store.cancel_return_task(sid, "USER001", tid)
    assert service.list_requests("USER001")[0]["request_id"] == created["request_id"]


def test_cancel_retry_does_not_increment_revision(flow):
    _, store, _, sid = flow
    tid = store.begin_turn(sid, "USER001", "中止退货草稿")
    before = store.cancel_return_task(sid, "USER001", tid)
    assert store.cancel_return_task(sid, "USER001", tid)["task"] == before["task"]


@pytest.mark.parametrize(
    "reason,days",
    [("no_reason", 7), ("no_reason", 8), ("quality", 15), ("quality", 16), ("quality", -1)],
)
def test_query_preview_quality_and_no_reason_use_same_dates(flow, reason, days):
    settings, store, service, sid = flow
    raw = (settings.reference_date - timedelta(days=days)).isoformat()
    with closing(store._connect()) as conn, conn:
        conn.execute("UPDATE orders SET delivered_at=? WHERE order_id='O10024'", (raw,))
    if reason == "quality":
        write(
            store,
            sid,
            {"reason": "quality", "problem_description": "无声", "quality_non_user_damage": True},
        )
        tid = store.begin_turn(sid, "USER001", "[upload]")
        service.attach_evidence(sid, "USER001", tid, image_bytes())
        store.interrupt_turn(sid, "USER001", tid)
    order = BusinessDB(settings).get_order("O10024", "USER001")
    key = "within_return_window" if reason == "no_reason" else "within_quality_return_window"
    flag = order[key]
    assert service.preview(sid, "USER001")["ready"] is flag


def test_guarded_generator_cancel_never_persists_buffered_model_text(local):
    settings, store = local
    settings = settings.model_copy(update={"order_aggregation_enabled": True})
    llm = StreamingWrong([ScriptedResponse(text="合计25件商品。")])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    stream = ChatService(settings, store).run_turn(QUERY, "USER001", agent=agent)
    sid = None
    for update in stream:
        sid = update.session_id
        if llm.calls:
            assert not update.result.answer
            break
    stream.close()
    snapshot = store.load(sid, "USER001")
    assert "25件" not in json.dumps(snapshot["history"])
    assert snapshot["last_result"]["stop_reason"] == "interrupted"


def test_normal_streaming_is_preserved_outside_protected_scope(local):
    settings, store = local
    llm = StreamingWrong([ScriptedResponse(text="您好，请问有什么需要帮助？")])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    updates = list(ChatService(settings, store).run_turn("你好", "USER001", agent=agent))
    assert any(u.phase == "running" and u.result.answer for u in updates)


def test_safe_acknowledgement_keeps_constraint_but_unknown_change_suspends():
    from acs.order_reliability import color_constraint

    raw = "后续一直只看白色耳机，不要列其他颜色，直到我明确更改。"
    state = color_constraint(
        [{"role": "user", "content": raw}, {"role": "user", "content": "谢谢。"}]
    )
    assert state["status"] == "active" and state["color"] == "白色"
    assert (
        color_constraint(
            [
                {"role": "user", "content": raw},
                {"role": "user", "content": "谢谢，但其他颜色也行。"},
            ]
        )["status"]
        == "uncertain"
    )


def test_actual_tool_preview_counts_as_success_without_ui_action():
    from acs.task_eval import deterministic_checks

    case = {"expected": {"task": {}, "new_requests": 0, "preview_ready": True}}
    event = {
        "query": "准备草稿",
        "result": {
            "stop_reason": "answered",
            "trace": [
                {
                    "type": "tool_result",
                    "label": "preview_return",
                    "detail": {"result": {"ready": True, "preview_id": "synthetic-preview"}},
                }
            ],
        },
    }
    assert deterministic_checks(case, [event], None, {}, {}, True)["preview_eligibility"]
    # A later failed preview is authoritative; do not reorder tool/UI timestamps.
    ui = {
        "action": "preview",
        "output": {"ready": False},
        "error": None,
        "operation_check": True,
        "new_requests": [],
    }
    assert not deterministic_checks(case, [event, ui], None, {}, {}, True)["preview_eligibility"]


@pytest.mark.parametrize("claim", ["已创建申请", "审核已经通过", "退款已经到账"])
def test_judge_valid_failure_receipts_are_not_overridden(claim):
    from acs.task_eval import JUDGE_SYSTEM

    class Client:
        usages = []

        def complete(self, *args, **kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "passed": False,
                        "failures": [{"type": "false_completion", "evidence": claim}],
                        "repeated_questions": 0,
                        "answer_quotes": [claim],
                    }
                )
            )

    verdict = judge_record(
        Client(),
        {
            "events": [
                {
                    "query": "结果",
                    "result": {"answer": claim, "trace": []},
                    "task": {"status": "draft"},
                }
            ]
        },
    )
    assert verdict["judgment_status"] == "failed" and not verdict["passed"]
    assert "跨会话不等于跨用户" in JUDGE_SYSTEM
    assert "ready=true时可以说预览成功" in JUDGE_SYSTEM


def test_colour_cancel_does_not_overwrite_unrelated_draft_conversation(flow):
    settings, store, _, sid = flow
    before = store.get_return_task(sid, "USER001")["task"]
    original = "颜色限制已取消，退货草稿保持不变。"
    llm = StreamingWrong([ScriptedResponse(text=original)])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    final = list(
        ChatService(settings, store).run_turn("取消颜色限制。", "USER001", sid, agent=agent)
    )[-1]
    assert final.result.answer == original
    assert store.get_return_task(sid, "USER001")["task"] == before
