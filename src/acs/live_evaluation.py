"""M8.1 seen-data evaluation contract. Does not change either customer runtime.

Authored references label only their exact, fully annotated fixture answers.
Novel language is reviewable evidence, never a failed task merely for its wording.
The frozen M8 executor continues to own tool dispatch, SQLite and Graph execution.
"""

from __future__ import annotations

import json
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import ValidationError

from . import multiturn_evaluation as legacy
from .episode_budget import PRICE
from .episode_transport import EpisodeSyncTransport
from .local_return_tools import WRITES

CONTRACT = "m8.1-open-answer-v3"
PILOT_IDS = ["D02", "D04"]
LIMITS = replace(legacy.LIMITS, requests=8)
BODY_BYTES = 32768
OUT_TOKENS = 1024
GLOBAL_REQUESTS = 40
GLOBAL_CNY = 5
OUTCOMES = ("pass", "fail", "pending_review", "execution_error")


def pilot_plan():
    bound = 2 * BODY_BYTES + 4096
    reserve = (Decimal(bound) * 2 + Decimal(OUT_TOKENS) * 8) / 1_000_000
    return {
        "contract": CONTRACT,
        "task_ids": PILOT_IDS,
        "seen_development_data": True,
        "order": [f"{t}:{a}" for t in PILOT_IDS for a in ("old", "v3")],
        "episodes": 4,
        "episode_requests": 8,
        "global_requests": GLOBAL_REQUESTS,
        "planned_agent_request_max": 32,
        "cap_cny": GLOBAL_CNY,
        "body_bytes": BODY_BYTES,
        "output_tokens": OUT_TOKENS,
        "input_token_bound": bound,
        "reservation_per_request_cny": str(reserve),
        "planned_32_request_reservation_cny": str(reserve * 32),
        "global_40_request_reservation_cny": str(reserve * 40),
        "first_limit_wins": True,
        "limits": vars(LIMITS),
        "price_assumption": PRICE,
        "fresh_price_verification_required": True,
        "paid_authorization": False,
        "automatic_retries": 0,
    }


def assess_answer(text, references, state, required=()):
    """Reference claims are independent SQL annotations, NOT grounder findings.

    Goal substrings are review hints, sufficient for no novel-language verdict.
    Missing hints fail only an exact authored fixture response: its annotations
    and frozen goal contract are known; a paraphrase may express goals differently.
    """
    hints = all(any(p in text for p in atom.split("|")) for atom in required)
    try:
        assessment = legacy.assess_answer(text, references, state)
    except (KeyError, ValueError, TypeError):
        return dict(
            status="execution_error",
            reason="invalid_answer_evidence",
            goal_hints_present=hints,
            claims=[],
        )
    if assessment["status"] == "unresolved":
        status, reason = "pending_review", "novel_language_requires_independent_review"
    elif assessment["status"] == "unfaithful":
        status, reason = "fail", "authored_claim_contradicts_authority"
    elif not hints:
        status, reason = "fail", "authored_answer_omits_frozen_goal_element"
    else:
        status, reason = "pass", "exact_authored_fixture_annotation"
    return dict(status=status, reason=reason, claims=assessment["claims"], goal_hints_present=hints)


def score_episode(task, result):
    answers, queue = [], []
    for record in result["steps"]:
        if record["raw"] is None:
            continue
        step = task.steps[record["step"]]
        graded = {"step": record["step"], "changed": record["raw"] != record["display"]}
        for kind in ("raw", "display"):
            grade = assess_answer(record[kind], step.answers, record["state"], step.answer_requires)
            graded[kind] = grade
            if grade["status"] in {"pending_review", "execution_error"}:
                queue.append(
                    dict(
                        review_id=uuid4().hex,
                        task_id=task.task_id,
                        arm=result["arm"],
                        step=record["step"],
                        kind=kind,
                        user_inputs=[
                            s.text for s in task.steps[: record["step"] + 1] if s.action == "chat"
                        ],
                        answer=record[kind],
                        evidence=record["state"],
                        reason=grade["reason"],
                    )
                )
        answers.append(graded)

    def outcome(kind):
        states = [a[kind]["status"] for a in answers]
        if result["error"] or "execution_error" in states:
            return "execution_error"
        if not result["sql_correct"] or not result["tool_valid"] or "fail" in states:
            return "fail"
        if not states or "pending_review" in states:
            return "pending_review"
        return "pass"

    raw_outcome = outcome("raw")
    return dict(
        contract=CONTRACT,
        raw_outcome=raw_outcome,
        task_success={"pass": True, "fail": False}.get(raw_outcome),
        display_outcome=outcome("display"),
        answers=answers,
        review_queue=queue,
    )


def classify_failure(
    result,
    *,
    uncertain_write=False,
    unsafe_effect=False,
    fee_unknown=False,
    stop_reasons=(),
    boundary_errors=(),
):
    """Stop the whole experiment only on safety/verification/global-cap failures."""
    if unsafe_effect:
        return "real_business_safety_violation", True
    if uncertain_write:
        return "business_state_unknown", True
    if fee_unknown:
        return "fee_unverifiable", True
    if any(
        r in {"global_request_limit", "global_cost_limit", "authorization_expired"}
        for r in stop_reasons
    ):
        return "budget_deadline_abort", True
    if any(r in {"body_limit", "output_limit", "experiment_stopped"} for r in stop_reasons):
        return "protocol_failure", True
    predicates = {e.get("predicate") for e in boundary_errors if e.get("predicate")}
    if predicates == {"unknown_tool"} and "state_uncertain" not in stop_reasons:
        return "tool_selection_failure_safe", False
    if any(r in {"adapter_protocol_error", "state_uncertain"} for r in stop_reasons):
        return "protocol_failure", True
    if stop_reasons:
        return "budget_deadline_abort", False
    if any(e.get("predicate") == "unknown_tool" for e in boundary_errors):
        return "tool_selection_failure_safe", False
    if any(e["type"] in {"ModelResponseError", "ReconciliationError"} for e in boundary_errors):
        return "protocol_failure", True
    if any(e["type"] == "ValidationError" for e in boundary_errors):
        return "tool_selection_failure_safe", False
    if result["error"]:
        return "execution_error", False
    receipts = result.get("final", {}).get("receipts", [])
    if not result["tool_valid"]:
        return (
            "authorization_business_rejection"
            if any(r.get("error") for r in receipts)
            else "tool_selection_failure_safe"
        ), False
    if not result["sql_correct"] or result.get("raw_outcome") == "fail":
        return "model_task_failure", False
    return None, False


def run_task(task, contracts, root, arm, *, model=None, ledger=None):
    """Reuse M8 execution, observing authority on error before deciding to continue.

    Single-worker CLI only: a scoped subclass records failures swallowed by the
    old loop. No runtime source or model messages are changed by this observation.
    """
    from .episode_budget import EpisodeLedger

    ledger = ledger or EpisodeLedger(root.parent / "fees.jsonl", max_requests=GLOBAL_REQUESTS)
    episode = task.task_id + ":" + arm
    taps, errors, not_dispatched = [], [], set()

    class ObservedTap(legacy.ModelTap):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.immutable = legacy.digest(legacy.business_rows(root))
            taps.append(self)

        def invoke(self, messages):
            try:
                return super().invoke(messages)
            except Exception as exc:
                errors.append(
                    dict(type=type(exc).__name__, predicate=getattr(exc, "predicate", None))
                )
                raise

        def attach_meter(self):
            super().attach_meter()
            execute = self.gateway.execute

            def observed(name, args, call_id, context, **kwargs):
                try:
                    return execute(name, args, call_id, context, **kwargs)
                except ValidationError:
                    # Gateway validates Pydantic arguments before entering its transaction.
                    not_dispatched.add(call_id)
                    errors.append(dict(type="ValidationError", predicate=None))
                    raise

            self.gateway.execute = observed

    uncertain, unsafe = False, False
    try:
        with patch.object(legacy, "ModelTap", ObservedTap):
            result = legacy.run_task(
                task, contracts, root, arm, model=model, ledger=ledger, limits=LIMITS
            )
        if taps:
            tap = taps[0]
            unsafe = legacy.digest(legacy.business_rows(root)) != tap.immutable
            unsafe |= result["final"]["counts"]["return_reviews"] != 0
            unsafe |= task.cohort == "common" and result["final"]["requests"] != 0
            # Tool journal intents are not business receipts. Read the business DB
            # even when Graph or the model boundary failed after a commit.
            with closing(tap.gateway.store._connect()) as conn:
                for event in ledger.rows(episode):
                    if event["type"] != "tool_intent" or event["call"]["name"] not in WRITES:
                        continue
                    cid = event["call"]["id"]
                    if cid in not_dispatched:
                        continue
                    receipt = conn.execute(
                        "SELECT 1 FROM local_receipts WHERE session_id=? AND call_id=?",
                        (tap.context.session_id, cid),
                    ).fetchone()
                    uncertain |= receipt is None
    except Exception as exc:
        # Failed authority reads cannot prove a write was absent. Never replay.
        uncertain = True
        result = dict(
            task_id=task.task_id,
            arm=arm,
            cohort=task.cohort,
            status="failed",
            error={"type": type(exc).__name__},
            steps=[],
            final={},
            sql_correct=False,
            tool_valid=False,
        )
    result.update(score_episode(task, result))
    events = ledger.rows(episode)
    stops = [r["reason"] for r in events if r["type"] in {"stop", "preflight_rejected"}]
    errors.extend(
        dict(type="ModelResponseError", predicate=r["predicate"])
        for r in events
        if r["type"] == "response_contract" and r["predicate"]
    )
    if result["error"] and not stops and sum(r["type"] == "model_decision" for r in events) >= 8:
        ledger.stop(episode, "episode_request_limit")
        stops.append("episode_request_limit")
    failure, global_stop = classify_failure(
        result,
        uncertain_write=uncertain,
        unsafe_effect=unsafe,
        fee_unknown=bool(ledger.summary(episode)["unknown_requests"]),
        stop_reasons=stops,
        boundary_errors=errors,
    )
    result.update(failure_class=failure, global_stop=global_stop, boundary_errors=errors)
    if failure in {
        "model_task_failure",
        "tool_selection_failure_safe",
        "authorization_business_rejection",
    }:
        # A known safe model/tool failure is a completed failed trial, even if the
        # frozen executor surfaced it as an exception. No failed arm is retried.
        result.update(raw_outcome="fail", display_outcome="fail", task_success=False)
    return result


def summarize(rows, ids):
    pairs = []
    for tid in ids:
        selected = [r for r in rows if r["task_id"] == tid]
        complete = len(selected) == 2 and all(
            r["raw_outcome"] != "execution_error" for r in selected
        )
        resolved = complete and all(r["raw_outcome"] in {"pass", "fail"} for r in selected)
        pairs.append(
            dict(
                task_id=tid,
                status="complete" if complete else "incomplete",
                semantic_status="resolved" if resolved else "pending_or_incomplete",
            )
        )
    groups = {}
    for arm in ("old", "v3"):
        selected = [r for r in rows if r["arm"] == arm]
        counts = Counter(r["raw_outcome"] for r in selected)
        groups[arm] = dict(
            total=len(selected),
            resolved=counts["pass"] + counts["fail"],
            verified_pass=counts["pass"],
            pending=counts["pending_review"],
            execution_errors=counts["execution_error"],
            verified_pass_over_all=(counts["pass"] / len(selected)) if selected else None,
        )
    answers = [answer for row in rows for answer in row.get("answers", [])]
    changed = [a for a in answers if a["changed"]]
    unresolved_changes = sum(
        any(a[k]["status"] in {"pending_review", "execution_error"} for k in ("raw", "display"))
        for a in changed
    )
    corrections = dict(
        changed_answers=len(changed),
        resolved_changes=len(changed) - unresolved_changes,
        pending_or_error_changes=unresolved_changes,
        known_improvements=sum(
            a["raw"]["status"] == "fail" and a["display"]["status"] == "pass" for a in changed
        ),
        known_false_corrections=sum(
            a["raw"]["status"] == "pass" and a["display"]["status"] == "fail" for a in changed
        ),
        independent_human_review_performed=False,
    )
    return dict(
        contract=CONTRACT,
        episodes=len(rows),
        planned_episodes=2 * len(ids),
        outcomes={
            arm: {s: sum(r["arm"] == arm and r["raw_outcome"] == s for r in rows) for s in OUTCOMES}
            for arm in ("old", "v3")
        },
        display_outcomes=dict(Counter(r.get("display_outcome") for r in rows)),
        denominators=groups,
        answer_outcomes={
            kind: {s: sum(a[kind]["status"] == s for a in answers) for s in OUTCOMES}
            for kind in ("raw", "display")
        },
        corrections=corrections,
        pairs=pairs,
        complete_pairs=sum(p["status"] == "complete" for p in pairs),
        resolved_pairs=sum(p["semantic_status"] == "resolved" for p in pairs),
        pending_review_items=sum(len(r.get("review_queue", [])) for r in rows),
        pair_rate=None,
        pair_rate_reason="No aggregate success claim from development contracts",
        global_stop=next((r["failure_class"] for r in rows if r["global_stop"]), None),
    )


def protocol_pairs(rows):
    # Reuse only the old deterministic comparator, never its boolean task metric.
    projection = [dict(r, task_success=r["raw_outcome"] == "pass") for r in rows]
    pairs = legacy.summarise(projection)["pairs"]
    for pair in pairs:
        for arm in ("old", "v3"):
            record = next(r for r in rows if r["task_id"] == pair["task_id"] and r["arm"] == arm)
            pair[arm + "_success"] = record["task_success"]
    return pairs


def paired_run(ids, execute, ledger, *, persist=None):
    """Append-only journal prevents retrying a failed arm or resetting after exit."""
    if any(r["type"] in {"arm_started", "experiment_stop"} for r in ledger.rows()):
        raise ValueError("Experiment already started; inspect only, never resume/replay an arm")
    rows = []
    for tid in ids:
        for arm in ("old", "v3"):
            episode = tid + ":" + arm
            ledger.event(episode, "arm_started", task_id=tid, arm=arm)
            try:
                record = execute(tid, arm)
            except Exception as exc:
                # An unclassified executor failure may conceal an authoritative write.
                record = dict(
                    task_id=tid,
                    arm=arm,
                    raw_outcome="execution_error",
                    display_outcome="execution_error",
                    error={"type": type(exc).__name__},
                    failure_class="business_state_unknown",
                    global_stop=True,
                )
            rows.append(record)
            ledger.event(
                episode,
                "arm_completed",
                outcome=record["raw_outcome"],
                failure_class=record["failure_class"],
            )
            if record["global_stop"]:
                ledger.event(episode, "experiment_stop", reason=record["failure_class"])
            report = summarize(rows, ids)
            if persist:
                persist(rows, report)
            if record["global_stop"]:
                return report
    return summarize(rows, ids)


class PreflightError(ValueError):
    pass


class BoundedTransport(httpx.BaseTransport):
    """Checks before the shared transport reserves money or creates any HTTP I/O."""

    def __init__(self, ledger, episode, expires_at, *, inner_factory=None, now=None):
        self.ledger, self.episode = ledger, episode
        self.expires_at = datetime.fromisoformat(expires_at)
        self.now = now or (lambda: datetime.now(UTC))
        self.inner = EpisodeSyncTransport(ledger, episode, "agent", inner_factory)

    def handle_request(self, request):
        body = request.read()
        reason = None
        if any(r["type"] == "experiment_stop" for r in self.ledger.rows()):
            reason = "experiment_stopped"
        elif self.now() >= self.expires_at:
            reason = "authorization_expired"
        elif len(body) > BODY_BYTES:
            reason = "body_limit"
        else:
            payload = json.loads(body)
            output = payload.get("max_tokens", payload.get("max_completion_tokens"))
            if type(output) is not int or not 0 < output <= OUT_TOKENS:
                reason = "output_limit"
        if reason:
            self.ledger.event(
                self.episode, "preflight_rejected", reason=reason, body_bytes=len(body)
            )
            raise PreflightError(reason)
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()
