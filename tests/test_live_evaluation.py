"""M8.1 open-answer and paired scheduling contracts; never contact a model service."""

from __future__ import annotations

import json
import multiprocessing
import os
import socket
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError

from acs.live_evaluation import PreflightError
from acs.multiturn_evaluation import AnswerReference, Claim
from acs.runtime import BudgetExceeded

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))

    def blocked(*args, **kwargs):
        raise AssertionError("M8.1 tests must not open network sockets")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def reference(text, fact="submission", value=False):
    return AnswerReference(text=text, claims=[Claim(span=text, fact=fact, value=value)])


def test_correct_paraphrase_is_pending_instead_of_false_failure():
    from acs.live_evaluation import assess_answer

    result = assess_answer("目前还没有送出申请。", [reference("尚未提交。")], {"requests": 0})
    assert result["status"] == "pending_review"


def test_goal_word_inside_negation_is_not_semantic_success():
    from acs.live_evaluation import assess_answer

    result = assess_answer("并没有保存草稿。", [], {}, ["保存", "草稿"])
    assert result["status"] == "pending_review"
    assert result["goal_hints_present"] is True


def test_sql_failure_cannot_be_hidden_by_a_success_claim():
    from acs.live_evaluation import assess_answer

    result = assess_answer("已提交。", [reference("已提交。", value=True)], {"requests": 0})
    assert result["status"] == "fail"


def test_authored_goal_omission_and_novel_expression_have_different_evidence():
    from acs.live_evaluation import assess_answer

    refs = [reference("尚未提交。")]
    assert assess_answer("尚未提交。", refs, {"requests": 0}, ["运费"])["status"] == "fail"
    assert assess_answer("运费由您承担。", refs, {"requests": 0})["status"] == "pending_review"


def row(*, error=None, sql=True, tool=True):
    return {
        "task_id": "D02",
        "arm": "old",
        "cohort": "common",
        "error": error,
        "sql_correct": sql,
        "tool_valid": tool,
        "status": "completed",
        "steps": [],
        "final": {"receipts": []},
    }


def test_sql_success_with_unreviewed_answer_is_pending_and_independent():
    from acs.live_evaluation import score_episode
    from acs.multiturn_evaluation import Step, Task

    task = Task(
        task_id="D02",
        split="development",
        goal="提交状态及运费",
        fixture_index=2,
        steps=[
            Step(
                text="状态和运费？",
                expect={},
                answer_requires=["运费"],
                answers=[reference("尚未提交，运费您承担。")],
            )
        ],
    )
    result = row()
    result["steps"] = [
        {
            "step": 0,
            "raw": "还没申请，邮资需要您支付。",
            "display": "尚未提交。",
            "state": {"requests": 0},
        }
    ]
    scored = score_episode(task, result)
    assert scored["raw_outcome"] == "pending_review"
    assert scored["task_success"] is None
    assert scored["display_outcome"] == "pending_review"
    assert len(scored["review_queue"]) == 2
    # An explicitly annotated deletion of the required goal is a known failure.
    task.steps[0].answers.append(reference("尚未提交。"))
    scored = score_episode(task, result)
    assert scored["display_outcome"] == "fail"
    assert scored["raw_outcome"] == "pending_review"


def test_ordinary_old_failure_still_runs_its_v3_partner(tmp_path):
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import paired_run

    seen = []

    def execute(tid, arm):
        seen.append((tid, arm))
        result = row(sql=arm != "old")
        result.update(
            task_id=tid,
            arm=arm,
            failure_class="model_task_failure" if arm == "old" else None,
            global_stop=False,
            raw_outcome="fail" if arm == "old" else "pass",
        )
        return result

    report = paired_run(["D02", "D04"], execute, EpisodeLedger(tmp_path / "fees"))
    assert seen == [("D02", "old"), ("D02", "v3"), ("D04", "old"), ("D04", "v3")]
    assert report["complete_pairs"] == 2
    assert report["resolved_pairs"] == 2


def test_unknown_write_stops_globally_and_excludes_incomplete_pair(tmp_path):
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import paired_run

    seen = []

    def execute(tid, arm):
        seen.append((tid, arm))
        return dict(
            row(),
            failure_class="business_state_unknown",
            global_stop=True,
            raw_outcome="execution_error",
        )

    ledger = EpisodeLedger(tmp_path / "fees")
    report = paired_run(["D02", "D04"], execute, ledger)
    assert seen == [("D02", "old")]
    assert report["complete_pairs"] == report["resolved_pairs"] == 0
    assert all(p["status"] == "incomplete" for p in report["pairs"])
    with pytest.raises(ValueError, match="resume|replay|started"):
        paired_run(["D02", "D04"], execute, ledger)


def test_readiness_does_not_edit_historical_contracts():
    root = Path(__file__).resolve().parents[1]
    hashes = json.loads((root / "results/m8_1_readiness_20261025/baseline_hashes.json").read_text())
    import hashlib

    for directory, entry in hashes.items():
        for relative, expected in entry["public_files"].items():
            assert (
                hashlib.sha256((root / directory / relative).read_bytes()).hexdigest() == expected
            )


@pytest.mark.parametrize("tid", ["D02", "D04"])
def test_actual_sdk_mock_and_sqlite_both_arms_share_contract(tmp_path, tid):
    from eval_live_readiness import model_for

    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS, run_task
    from acs.multiturn_evaluation import load_suite, summarise

    tasks, contracts, _ = load_suite(ROOT / "data/eval/v3_multiturn_m8_v2")
    task = next(t for t in tasks if t.task_id == tid)
    ledger = EpisodeLedger(tmp_path / "fees", max_requests=40)
    results = []
    for arm in ("old", "v3"):
        episode = tid + ":" + arm
        ledger.begin(episode, LIMITS)
        queue = [item for step in contracts[tid] for item in step]
        with model_for(tmp_path, ledger, episode, queue=queue) as model:
            result = run_task(task, [], tmp_path / arm, arm, model=model, ledger=ledger)
        assert result["sql_correct"] and result["tool_valid"], result.get("error")
        assert not result["global_stop"]
        assert result["model_http_requests"] == 4
        assert not queue
        results.append(result)
    pair = summarise(results)["pairs"][0]
    assert pair["same_initial_db"] and pair["same_final_business_state"]
    assert pair["same_tool_calls"] and pair["same_normalized_model_inputs"]
    assert ledger.summary()["requests"] == 8 and ledger.summary()["cost_complete"]
    if tid == "D02":
        assert results[0]["raw_outcome"] == "fail"  # Known fixture answers omit order IDs.


def sdk_context(tmp_path, *, expires="2099-01-01T00:00:00+00:00", usage=True):
    from eval_live_readiness import mock_response

    from acs.config import Settings
    from acs.episode_budget import EpisodeLedger
    from acs.episode_transport import EpisodeCallback
    from acs.langgraph_poc import deepseek_model
    from acs.live_evaluation import LIMITS, BoundedTransport

    ledger = EpisodeLedger(tmp_path / "fees", max_requests=40)
    ledger.begin("e", LIMITS)
    sent = []

    def respond(request):
        sent.append(len(request.content))
        response = mock_response({"text": "尚未提交。"})
        if not usage:
            body = response.json()
            body.pop("usage")
            return httpx.Response(200, json=body)
        return response

    http = httpx.Client(
        transport=BoundedTransport(
            ledger, "e", expires, inner_factory=lambda: httpx.MockTransport(respond)
        )
    )
    model = deepseek_model(
        Settings(
            _env_file=None,
            llm_api_key="offline-test",
            llm_model="deepseek-flash",
            llm_max_output_tokens=1024,
            llm_temperature=0,
            llm_thinking=False,
        ),
        http_client=http,
        callbacks=[EpisodeCallback(ledger, "e")],
    )
    return ledger, sent, http, model


def test_eighth_http_allowed_ninth_blocked_and_restart_cannot_reset(tmp_path):
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS

    ledger, sent, http, model = sdk_context(tmp_path)
    with http:
        for _ in range(8):
            model.invoke("查询订单")
        with pytest.raises((APIConnectionError, BudgetExceeded, PreflightError)):
            model.invoke("第九次必须在HTTP前拒绝")
    assert len(sent) == 8
    reopened = EpisodeLedger(tmp_path / "fees", max_requests=40)
    reopened.begin("e", LIMITS)
    assert reopened.summary()["requests"] == 8
    assert "episode_request_limit" in reopened.summary()["stop_reasons"]
    assert sum(r["type"] == "sdk_end" for r in ledger.rows()) == 8


def test_known_long_context_over_old_20k_bound_is_accepted(tmp_path):
    ledger, sent, http, model = sdk_context(tmp_path)
    with http:
        model.invoke("长" * 8000)
    assert 20000 < sent[0] < 32768
    assert ledger.summary()["cost_complete"]


@pytest.mark.parametrize("kind", ["long_body", "expired", "output_limit"])
def test_preflight_rejections_send_no_http_and_reserve_no_cost(tmp_path, kind):
    expiry = "2020-01-01T00:00:00+00:00" if kind == "expired" else "2099-01-01T00:00:00+00:00"
    ledger, sent, http, model = sdk_context(tmp_path, expires=expiry)
    with http, pytest.raises((APIConnectionError, BudgetExceeded, PreflightError)):
        model.invoke(
            "长" * 12000 if kind == "long_body" else "查询订单",
            **({"max_tokens": 1025} if kind == "output_limit" else {}),
        )
    assert not sent and ledger.summary()["requests"] == 0
    assert any(r["type"] == "preflight_rejected" for r in ledger.rows())


def test_missing_usage_is_unknown_cost_and_global_stop(tmp_path):
    from acs.live_evaluation import classify_failure

    ledger, sent, http, model = sdk_context(tmp_path, usage=False)
    with http, pytest.raises((APIConnectionError, BudgetExceeded, PreflightError)):
        model.invoke("查询订单")
    assert len(sent) == 1 and ledger.summary()["unknown_requests"] == 1
    assert classify_failure(row(), fee_unknown=True) == ("fee_unverifiable", True)


def test_body_cost_math_covers_all_32_planned_attempts(tmp_path):
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS, pilot_plan

    plan = pilot_plan()
    assert Decimal(plan["planned_32_request_reservation_cny"]) == Decimal("4.718592")
    assert Decimal(plan["global_40_request_reservation_cny"]) > 5
    body = json.dumps({"model": "deepseek-flash", "max_tokens": 1024, "padding": ""}).encode()
    body = body[:-2] + b"x" * (32768 - len(body)) + body[-2:]
    assert len(body) == 32768
    ledger = EpisodeLedger(tmp_path / "fees", max_requests=40)
    for episode in ["D02:old", "D02:v3", "D04:old", "D04:v3"]:
        ledger.begin(episode, LIMITS)
        for _ in range(8):
            reservation = ledger.reserve(episode, "agent", body)
            ledger.finish(
                reservation,
                status="completed",
                usage={"prompt_tokens": 100, "completion_tokens": 20},
            )
    assert ledger.summary()["reserved_cny"] == "4.718592"
    assert ledger.summary()["requests"] == 32


@pytest.mark.parametrize(
    "reason,global_stop",
    [
        ("episode_request_limit", False),
        ("episode_deadline", False),
        ("global_request_limit", True),
        ("global_cost_limit", True),
        ("authorization_expired", True),
        ("adapter_protocol_error", True),
    ],
)
def test_local_and_global_stops_are_different(reason, global_stop):
    from acs.live_evaluation import classify_failure

    assert classify_failure(row(), stop_reasons=[reason])[1] is global_stop


def test_exit_after_arm_started_is_inspect_only(tmp_path):
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import paired_run

    ledger = EpisodeLedger(tmp_path / "fees")

    def exit_now(*args):
        raise KeyboardInterrupt("simulated process exit")

    with pytest.raises(KeyboardInterrupt):
        paired_run(["D02"], exit_now, ledger)
    assert any(r["type"] == "arm_started" for r in ledger.rows())
    assert not any(r["type"] == "arm_completed" for r in ledger.rows())
    with pytest.raises(ValueError, match="started"):
        paired_run(["D02"], exit_now, EpisodeLedger(tmp_path / "fees"))


def test_new_live_authorization_is_required_before_output(tmp_path):
    from argparse import Namespace

    from eval_live_readiness import authorization, execute

    with pytest.raises(ValueError, match="Fresh explicit"):
        authorization(None, "sha", tmp_path / "new")
    with pytest.raises(ValueError, match="Fresh explicit"):
        execute(
            Namespace(
                mode="live",
                out=tmp_path / "new",
                authorization=None,
                price_review=None,
                env_file=None,
            )
        )
    assert not (tmp_path / "new").exists()
    auth = {
        "authorized": True,
        "user_instruction_reference": "test only",
        "source_sha": "sha",
        "contract": "m8.1-open-answer-v3",
        "task_ids": ["D02", "D04"],
        "arms": ["old", "v3"],
        "run_id": "test",
        "output_dir": str(tmp_path / "new"),
        "expires_at": datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
        "cap_cny": 5,
        "max_requests": 40,
    }
    file = tmp_path / "auth.json"
    file.write_text(json.dumps(auth))
    with pytest.raises(ValueError, match="invalid"):
        authorization(file, "sha", tmp_path / "new")


@pytest.mark.parametrize("arm", ["old", "v3"])
def test_real_sdk_novel_answer_and_sql_success_become_pending(tmp_path, arm):
    from eval_live_readiness import model_for

    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS, run_task
    from acs.multiturn_evaluation import load_suite

    tasks, contracts, _ = load_suite(ROOT / "data/eval/v3_multiturn_m8_v2")
    task = next(t for t in tasks if t.task_id == "D04")
    queue = [dict(item) for step in contracts["D04"] for item in step]
    queue[-1] = {"text": "您的退货资料仍在草拟中，商品及件数没有变化。"}
    ledger = EpisodeLedger(tmp_path / "fees", max_requests=40)
    ledger.begin("D04:" + arm, LIMITS)
    with model_for(tmp_path, ledger, "D04:" + arm, queue=queue) as model:
        result = run_task(task, [], tmp_path / "db", arm, model=model, ledger=ledger)
    assert result["sql_correct"] and result["tool_valid"]
    assert result["raw_outcome"] == result["display_outcome"] == "pending_review"
    assert len(result["review_queue"]) == 2
    assert not result["global_stop"]


@pytest.mark.parametrize("point,stop", [("before_commit", True), ("after_commit", False)])
def test_actual_sql_write_fault_is_reconciled_before_pair_decision(
    tmp_path, monkeypatch, point, stop
):
    import sqlite3

    from acs.live_evaluation import run_task
    from acs.local_return_tools import LocalReturnGateway
    from acs.multiturn_evaluation import load_suite

    original = LocalReturnGateway.execute

    def execute(self, name, *args, **kwargs):
        def fault(where):
            if name == "update_return_task" and where == point:
                raise RuntimeError("Injected SQL delivery fault")

        self.fault = fault
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(LocalReturnGateway, "execute", execute)
    tasks, contracts, _ = load_suite(ROOT / "data/eval/v3_multiturn_m8_v2")
    task = next(t for t in tasks if t.task_id == "D04")
    result = run_task(task, contracts["D04"], tmp_path / "db", "old")
    assert result["global_stop"] is stop
    assert result["raw_outcome"] == "execution_error"
    with sqlite3.connect(tmp_path / "db/business.sqlite") as conn:
        receipts = conn.execute(
            "SELECT COUNT(*) FROM local_receipts WHERE name='update_return_task'"
        )
        assert receipts.fetchone()[0] == (0 if point == "before_commit" else 1)
        assert conn.execute("SELECT COUNT(*) FROM return_requests").fetchone()[0] == 0


def test_pending_items_stay_in_denominator_and_pairs_are_not_resolved():
    from acs.live_evaluation import summarize

    a = dict(row(), raw_outcome="pending_review", display_outcome="pass", global_stop=False)
    b = dict(a, arm="v3")
    report = summarize([a, b], ["D02"])
    assert report["complete_pairs"] == 1 and report["resolved_pairs"] == 0
    assert report["denominators"]["old"] == dict(
        total=1,
        resolved=0,
        verified_pass=0,
        pending=1,
        execution_errors=0,
        verified_pass_over_all=0,
    )


def test_complete_unknown_tool_response_is_safe_failure_not_unknown_write():
    from acs.live_evaluation import classify_failure

    assert classify_failure(
        row(error={"type": "APIConnectionError"}),
        stop_reasons=["adapter_protocol_error"],
        boundary_errors=[{"type": "ModelResponseError", "predicate": "unknown_tool"}],
    ) == ("tool_selection_failure_safe", False)


def test_display_deletion_of_valid_goal_is_a_false_correction():
    from acs.live_evaluation import score_episode, summarize
    from acs.multiturn_evaluation import Step, Task

    full, short = "尚未提交，运费由您承担。", "尚未提交。"
    task = Task(
        task_id="D02",
        split="development",
        fixture_index=2,
        goal="状态与运费",
        steps=[
            Step(
                text="状态与运费？",
                expect={},
                answer_requires=["运费"],
                answers=[reference(full), reference(short)],
            )
        ],
    )
    record = dict(row(), steps=[dict(step=0, raw=full, display=short, state={"requests": 0})])
    record.update(score_episode(task, record), global_stop=False)
    report = summarize([record], ["D02"])
    assert record["raw_outcome"] == "pass" and record["display_outcome"] == "fail"
    assert report["corrections"]["known_false_corrections"] == 1
    assert report["corrections"]["pending_or_error_changes"] == 0


@pytest.mark.parametrize("arm", ["old", "v3"])
def test_real_sdk_unknown_tool_is_completed_safe_failure(tmp_path, arm):
    from eval_live_readiness import model_for

    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS, run_task
    from acs.multiturn_evaluation import load_suite

    tasks, _, _ = load_suite(ROOT / "data/eval/v3_multiturn_m8_v2")
    task = next(t for t in tasks if t.task_id == "D04")
    ledger = EpisodeLedger(tmp_path / "fees", max_requests=40)
    episode = "D04:" + arm
    ledger.begin(episode, LIMITS)
    queue = [{"calls": [{"id": "bad-tool", "name": "invented_tool", "args": {}}]}]
    with model_for(tmp_path, ledger, episode, queue=queue) as model:
        result = run_task(task, [], tmp_path / "db", arm, model=model, ledger=ledger)
    assert result["raw_outcome"] == "fail"
    assert result["failure_class"] == "tool_selection_failure_safe"
    assert not result["global_stop"] and ledger.summary()["cost_complete"]
    assert result["final"]["requests"] == 0 and result["final"]["draft"] is None


def exit_worker(path):
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import paired_run

    def exit_now(*args):
        os._exit(71)

    paired_run(["D02"], exit_now, EpisodeLedger(path))


def test_actual_process_exit_cannot_replay_or_reset_experiment(tmp_path):
    import hashlib
    from argparse import Namespace

    from eval_live_readiness import execute

    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import paired_run

    path = tmp_path / ".private-evidence/fees.jsonl"
    path.parent.mkdir()
    process = multiprocessing.get_context("fork").Process(target=exit_worker, args=(path,))
    process.start()
    process.join(20)
    assert process.exitcode == 71
    ledger = EpisodeLedger(path)
    assert any(r["type"] == "arm_started" for r in ledger.rows())
    assert not any(r["type"] == "arm_completed" for r in ledger.rows())
    with pytest.raises(ValueError, match="started"):
        paired_run(["D02"], lambda *args: pytest.fail("must not replay"), ledger)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    inspection = execute(Namespace(mode="inspect", out=tmp_path))
    assert inspection["interrupted_episodes"] == ["D02:old"]
    assert inspection["complete_pairs"] == 0 and inspection["writes_replayed"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_blind_package_uses_existing_material_and_keeps_keys_separate(tmp_path, monkeypatch):
    import random

    from prepare_m81_review import prepare

    original_shuffle = random.Random.shuffle

    def guard(rng, values):
        if values == ["raw", "display"]:
            assert isinstance(rng, random.SystemRandom), "Public sampling seed reveals A/B identity"
        return original_shuffle(rng, values)

    monkeypatch.setattr(random.Random, "shuffle", guard)

    source = tmp_path / "source"
    source.mkdir()
    materials, keys, steps = [], [], []
    for index in range(114):
        steps.append(
            dict(
                step=index,
                raw="正确。",
                display="正确。",
                raw_grade={"status": "faithful"},
                display_grade={"status": "faithful"},
                raw_goal_covered=True,
                display_goal_covered=True,
                state={"policy": {"hash": "synthetic"}},
            )
        )
        for kind in ("raw", "display"):
            identity = f"id-{index}-{kind}"
            materials.append(
                dict(review_id=identity, conversation=[], answer="正确。", evidence={"requests": 0})
            )
            keys.append(dict(review_id=identity, task_id="D02", arm="old", step=index, kind=kind))
    steps[0]["raw"] = "错误。"
    steps[0]["raw_grade"]["status"] = "unfaithful"
    materials[0]["answer"] = "错误。"
    for name, data in (
        ("blind-goal-review-v2.json", materials),
        ("blind-goal-review-key-v2.json", keys),
        ("episodes.json", [dict(task_id="D02", arm="old", steps=steps)]),
    ):
        (source / name).write_text(json.dumps(data))
    summary = prepare(source, tmp_path / "pack")
    assert summary["sampled_items"] == 48 and not summary["review_performed"]
    assert "correct_answer_changed" in summary["unavailable_strata"]
    queue = json.loads((tmp_path / "pack/.private-evidence/reviewer-items.json").read_text())
    assert len({r["review_id"] for r in queue}) == 48
    assert all(
        not {"task_id", "kind", "arm", "stratum", "raw_grade", "display_grade"} & r.keys()
        for r in queue
    )
    assert all(not r["review_fields"]["reviewer_id"] for r in queue)
    with pytest.raises(FileExistsError):
        prepare(source, tmp_path / "pack")
