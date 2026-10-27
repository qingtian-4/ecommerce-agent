"""M8 graders must detect bad SQL, false claims, lost work and gate paid HTTP."""

from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

from acs.multiturn_evaluation import (
    AnswerReference,
    Claim,
    Task,
    assess_answer,
    build_fixture,
    business_clock,
    run_task,
    score_tools,
    summarise,
)

from .conftest import load_current_regression_suite as load_suite

SUITE = Path(__file__).resolve().parents[1] / "data/eval/v3_multiturn_m8_v2"


@pytest.fixture(autouse=True)
def scripts_import_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SUITE.parents[2] / "scripts"))


def test_frozen_dataset_split_unique_and_not_historical_six_stage_copy():
    from eval_answer_stream import INPUTS

    tasks, contracts, _ = load_suite(SUITE)
    assert len({t.fixture_index for t in tasks}) == 30
    assert sum(t.cohort == "common" for t in tasks) == 25
    assert not any(s.text in INPUTS for t in tasks for s in t.steps if s.text)
    assert set(contracts) == {t.task_id for t in tasks}
    for t in tasks:
        assert len(contracts[t.task_id]) == len(t.steps)
        assert all(
            c.fact and c.span in a.text for s in t.steps for a in s.answers for c in a.claims
        )


def test_dataset_tamper_is_rejected(tmp_path):
    import shutil

    shutil.copytree(SUITE, tmp_path / "suite")
    (tmp_path / "suite/tasks.json").write_text("[]")
    with pytest.raises(ValueError, match="fingerprint"):
        load_suite(tmp_path / "suite")


def test_initial_pair_fixtures_reproducible_and_formal_barriers_intact(tmp_path):
    from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings, formal_barriers

    task = load_suite(SUITE)[0][0]
    with business_clock():
        a = build_fixture(tmp_path / "a", task)
        b = build_fixture(tmp_path / "b", task)
    assert a == b
    for name in ["a", "b"]:
        root = tmp_path / name
        fixture_settings(root, kind=FORMAL_FIXTURE_KIND)
        with sqlite3.connect(root / "business.sqlite") as conn:
            actual = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            assert all(actual[n] == s for n, s in formal_barriers().items())
            with pytest.raises(sqlite3.IntegrityError, match="trusted"):
                conn.execute("UPDATE orders SET status='shipped'")


@pytest.mark.parametrize("tid", ["D01", "D03", "D07", "D10"])
def test_development_actual_runtimes_share_execution_and_not_gold(tmp_path, tid):
    tasks, contracts, _ = load_suite(SUITE)
    task = next(t for t in tasks if t.task_id == tid)
    old = run_task(task, contracts[tid], tmp_path / "old", "old")
    new = run_task(task, contracts[tid], tmp_path / "v3", "v3")
    assert old["error"] is None and new["error"] is None
    assert old["sql_correct"] and new["sql_correct"]
    assert old["tool_valid"] and new["tool_valid"]
    pair = summarise([old, new])["pairs"][0]
    assert pair["same_initial_db"] and pair["same_tool_calls"]
    assert pair["same_normalized_model_inputs"]
    assert pair["same_final_business_state"]
    assert pair["old_success"] == pair["v3_success"]
    for root in [tmp_path / "old", tmp_path / "v3"]:
        model_input = json.loads((root / "model-inputs.json").read_text())
        encoded = json.dumps(model_input, ensure_ascii=False)
        assert "authored_complete_reference" not in encoded
        assert "submission_unverified" not in encoded and "fixture_index" not in encoded
        assert tid not in encoded
    if tid == "D10":
        assert not new["task_success"]  # SQL/tool success must not hide raw refund hallucination.
        assert new["steps"][0]["raw_grade"]["status"] == "unfaithful"
        assert new["steps"][0]["display_grade"]["status"] == "faithful"


def test_unmatched_natural_language_is_unresolved_not_faithful():
    r = assess_answer("都办理好了。", [], {})
    assert r["status"] == "unresolved" and r["unresolved"] == 1


def test_raw_and_display_are_independently_scored_from_sql():
    raw = "已提交退货申请。"
    refs = [
        AnswerReference(text=raw, claims=[Claim(span=raw, fact="submission", value=True)]),
        AnswerReference(
            text="尚未提交。", claims=[Claim(span="尚未提交", fact="submission", value=False)]
        ),
    ]
    assert assess_answer(raw, refs, {"requests": 0})["status"] == "unfaithful"
    assert assess_answer(raw, refs, {"requests": 1})["status"] == "faithful"
    assert assess_answer("尚未提交。", refs, {"requests": 0})["status"] == "faithful"
    assert assess_answer("尚未提交。", refs, {"requests": 1})["status"] == "unfaithful"


def test_failed_tool_is_not_counted_as_business_success():
    step = load_suite(SUITE)[0][0].steps[0]
    call = [{"name": "get_product"}]
    assert not score_tools(
        step, call, [{"name": "get_product", "error": "not_found", "call_id": "a", "result": "{}"}]
    )
    assert not score_tools(step, [{"name": "confirm_and_submit"}], [])
    assert score_tools(
        step, call, [{"name": "get_product", "error": None, "call_id": "a", "result": "{}"}]
    )


def test_changed_truthful_answer_can_be_a_false_correction():
    from copy import deepcopy

    base = {
        "task_id": "unit",
        "cohort": "common",
        "split": "development",
        "arm": "v3",
        "sql_correct": True,
        "tool_valid": True,
        "task_success": True,
        "model_calls": 1,
        "model_http_requests": 0,
        "input_tokens": None,
        "output_tokens": None,
        "latency_ms": 0,
        "steps": [
            {
                "raw": "truth",
                "display": "false",
                "raw_grade": {"status": "faithful", "unresolved": 0},
                "display_grade": {"status": "unfaithful", "unresolved": 0},
                "recovery": None,
            }
        ],
    }
    m = summarise([base])["metrics"]["common:v3"]
    assert m["false_corrections"] == 1 and m["false_correction_rate_among_changes"] == 1
    missed = deepcopy(base)
    missed["steps"][0]["raw_grade"]["status"] = "unfaithful"
    m = summarise([missed])["metrics"]["common:v3"]
    assert m["miss_rate"] == 1 and m["correction_rate"] == 0


def test_fresh_authorization_required_before_any_output_or_http(tmp_path):
    from eval_v3_multiturn import PILOT_IDS, execute

    args = Namespace(
        suite=SUITE,
        out=tmp_path / "out",
        ids=PILOT_IDS,
        mode="live",
        authorization=None,
        price_review=None,
        env_file=None,
    )
    with pytest.raises(ValueError, match="Fresh explicit"):
        execute(args)
    assert not args.out.exists()


def test_old_authorization_cannot_be_reused(tmp_path):
    from eval_v3_multiturn import authorization

    p = tmp_path / "old.json"
    p.write_text(
        json.dumps(
            {
                "authorized": True,
                "user_instruction_reference": "expired prior permission",
                "source_sha": "x",
                "task_ids": [],
                "arms": ["old", "v3"],
                "run_id": "old",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "cap_cny": 3,
                "max_requests": 24,
            }
        )
    )
    with pytest.raises(ValueError, match="Authorization"):
        authorization(p, "x", [])


def test_actual_sdk_callback_stops_before_second_http(tmp_path):
    from acs.config import Settings
    from acs.episode_budget import EpisodeLedger, EpisodeLimits
    from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport
    from acs.langgraph_poc import deepseek_model

    task = Task(
        task_id="SDK",
        split="development",
        goal="SDK accounting",
        fixture_index=99,
        steps=[
            {
                "text": "只问状态",
                "expect": {"draft": None, "requests": 0},
                "answers": [
                    {
                        "text": "尚未提交退货申请。",
                        "claims": [
                            {"span": "尚未提交退货申请", "fact": "submission", "value": False}
                        ],
                    }
                ],
            }
        ]
        * 2,
    )
    ledger = EpisodeLedger(tmp_path / "fees.jsonl", cap_cny=1, max_requests=1)
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "尚未提交退货申请。"},
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    with httpx.Client(
        transport=EpisodeSyncTransport(
            ledger, "SDK:v3", "agent", lambda: httpx.MockTransport(respond)
        )
    ) as http:
        model = deepseek_model(
            Settings(_env_file=None, llm_api_key="offline-test"),
            http_client=http,
            callbacks=[EpisodeCallback(ledger, "SDK:v3")],
        )
        result = run_task(
            task,
            [],
            tmp_path / "fixture",
            "v3",
            model=model,
            ledger=ledger,
            limits=EpisodeLimits(requests=2),
        )
    assert len(sent) == 1 and ledger.summary()["requests"] == 1
    assert result["status"] == "failed" and result["model_http_requests"] == 1
    assert ledger.summary()["roles"]["agent"]["prompt_tokens"] == 7
    assert ledger.summary()["roles"]["agent"]["completion_tokens"] == 3


@pytest.mark.parametrize("decision", ["accept", "reject", "cancel", "lost_commit_receipt"])
def test_application_bridge_actions_and_recovery_with_real_sql(tmp_path, decision):
    from copy import deepcopy

    from acs.multiturn_evaluation import Step

    tasks, contracts, _ = load_suite(SUITE)
    original = next(t for t in tasks if t.task_id == "D03")
    chat = original.steps[1].model_copy(deep=True)
    chat.allowed_tools.append("preview_return")
    chat.required_tools.append("preview_return")
    chat.expect.update(preview_count=1, valid_preview=True)
    success = {"requests": 1, "draft": {"status": "submitted"}, "valid_preview": False}
    not_submitted = {"requests": 0, "draft": {"status": "draft"}, "valid_preview": True}
    steps = [
        chat,
        Step(action="open", expect=not_submitted),
        Step(
            action=decision,
            expect=success if decision in ("accept", "lost_commit_receipt") else not_submitted,
        ),
    ]
    if decision == "accept":
        steps.append(Step(action="accept", expect=success))
    if decision == "lost_commit_receipt":
        steps.append(Step(action="restart_lookup", expect=success))
    task = Task(
        task_id="GENERIC_APP",
        split="development",
        cohort="incremental",
        fixture_index=original.fixture_index,
        goal="Generic bridge contract",
        steps=steps,
    )
    plan = deepcopy(contracts["D03"][1])
    plan[0]["calls"].append({"id": "unit-preview", "name": "preview_return", "args": {}})
    result = run_task(task, [plan, *[[] for _ in steps[1:]]], tmp_path / "fixture", "v3")
    assert result["error"] is None and result["sql_correct"] and result["tool_valid"]
    assert result["final"]["requests"] == int(decision in ("accept", "lost_commit_receipt"))
    if decision == "lost_commit_receipt":
        assert result["steps"][-1]["recovery"] is True
    if result["final"]["requests"]:
        assert result["final"]["counts"]["return_confirmations"] == 1
        assert result["final"]["counts"]["return_audit"] == 1


def test_temporary_stream_error_is_not_the_final_display_or_sql_success(tmp_path):
    import asyncio
    import time
    from contextlib import contextmanager
    from threading import Event

    from fastapi.testclient import TestClient

    from acs.config import Settings
    from acs.episode_budget import EpisodeLedger, EpisodeLimits
    from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport
    from acs.langgraph_poc import deepseek_model
    from acs.local_submission import init_formal_fixture
    from acs.web_service import create_app

    from .test_episode_streaming import USAGE, frame
    from .test_web_service import ORIGIN, browser, counts, done, events, post

    root = tmp_path / "stream"
    init_formal_fixture(root)
    ledger = EpisodeLedger(root / "fees.jsonl", cap_cny=1, max_requests=1)
    ledger.begin("stream", EpisodeLimits(requests=1, episode_timeout_s=60))
    release = Event()
    raw = "退款已完成，您还需要帮助吗？" + "这只是模型正文，业务应核实。" * 12

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame(raw)
            assert await asyncio.to_thread(release.wait, 10)
            yield frame("", finish="stop") + frame(usage=USAGE) + b"data: [DONE]\n\n"

    @contextmanager
    def factory():
        with httpx.Client(
            transport=EpisodeSyncTransport(
                ledger,
                "stream",
                "agent",
                lambda: httpx.MockTransport(
                    lambda r: httpx.Response(
                        200, headers={"content-type": "text/event-stream"}, stream=Body()
                    )
                ),
                streaming=True,
            )
        ) as http:
            yield deepseek_model(
                Settings(_env_file=None, llm_api_key="offline-test"),
                http_client=http,
                callbacks=[EpisodeCallback(ledger, "stream")],
            ).model_copy(update={"streaming": True})

    with TestClient(create_app(root, factory, answer_streaming=True), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        try:
            assert post(client, sid, headers, "messages", text="只查询，不退款").status_code == 202
            deadline = time.monotonic() + 12
            while True:
                provisional = [e for e in events(client, sid) if e["type"] == "answer_delta"]
                if provisional:
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
            assert "退款已完成" in provisional[0]["data"]["text"]
            assert ledger.summary()["unknown_requests"] == 1  # Still in flight, never free.
        finally:
            release.set()
        state = done(client, sid)
        assert "不具备审核、退款或换货办理能力" in state["history"][-1]["content"]
        assert "退款已完成" not in state["history"][-1]["content"]
        assert counts(root) == (0, 0, 0)
        with sqlite3.connect(root / "business.sqlite") as c:
            assert c.execute("SELECT answer FROM conversation_turns").fetchone()[0] == raw


def test_business_not_found_is_not_a_successful_tool_even_without_exception():
    step = load_suite(SUITE)[0][0].steps[0]
    bad = {"call_id": "x", "name": "get_product", "error": None, "result": '{"found":false}'}
    assert not score_tools(step, [{"name": "get_product"}], [bad])
    good = bad | {"result": '{"found":true,"product_id":"P003"}'}
    assert score_tools(step, [{"name": "get_product"}], [good])


def test_authorization_is_bound_to_one_output_directory(tmp_path):
    from eval_v3_multiturn import authorization

    p = tmp_path / "authorization.json"
    p.write_text(
        json.dumps(
            {
                "authorized": True,
                "user_instruction_reference": "unit test only",
                "source_sha": "fixture-sha",
                "task_ids": ["unit"],
                "arms": ["old", "v3"],
                "run_id": "unit",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "cap_cny": 1,
                "max_requests": 1,
                "output_dir": str(tmp_path / "one"),
            }
        )
    )
    assert authorization(p, "fixture-sha", ["unit"], tmp_path / "one")["max_requests"] == 1
    with pytest.raises(ValueError, match="one unique"):
        authorization(p, "fixture-sha", ["unit"], tmp_path / "two")


def test_pilot_plan_never_creates_ledger_or_reads_credentials(tmp_path):
    from eval_v3_multiturn import execute

    args = Namespace(
        suite=SUITE,
        out=tmp_path / "not-created",
        mode="pilot-plan",
        ids=None,
        authorization=None,
        price_review=None,
        env_file=tmp_path / "missing-env",
    )
    plan = execute(args)
    assert plan["episodes"] == 12 and plan["total_request_ceiling"] == 48
    assert plan["paid_authorization_present"] is False
    assert not args.out.exists()


def test_fixture_order_totals_equal_actual_line_items_and_clock_is_shared(tmp_path):
    from acs.answer_grounding import Evidence
    from acs.multiturn_evaluation import NOW

    task = load_suite(SUITE)[0][0]
    with business_clock():
        build_fixture(tmp_path / "fixture", task)
        assert Evidence().checked_at == NOW.isoformat()
    with sqlite3.connect(tmp_path / "fixture/business.sqlite") as c:
        mismatches = c.execute(
            "SELECT o.order_id FROM orders o JOIN order_items i USING(order_id) "
            "GROUP BY o.order_id HAVING ABS(o.total_amount-SUM(i.quantity*i.purchase_price))>.01"
        ).fetchall()
        assert mismatches == []


def test_faithful_but_incomplete_answer_does_not_complete_the_goal(tmp_path):
    tasks, contracts, _ = load_suite(SUITE)
    task = next(t for t in tasks if t.task_id == "D02")
    result = run_task(task, contracts["D02"], tmp_path / "fixture", "v3")
    assert result["sql_correct"] and result["tool_valid"]
    assert all(s["raw_grade"]["status"] == "faithful" for s in result["steps"])
    assert result["task_success"] is False
