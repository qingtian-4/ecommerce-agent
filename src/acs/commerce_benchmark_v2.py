"""Paired offline execution using the accepted M8 business/runtime/scoring modules."""

from __future__ import annotations

import copy
from contextlib import ExitStack, closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from . import commerce_benchmark as prior
from . import multiturn_evaluation as m8
from .commerce_benchmark_data import dump, file_hash
from .commerce_benchmark_v2_data import VERSION
from .live_evaluation import protocol_pairs, summarize
from .local_submission import read_submission_receipt


def load(root):
    with (
        patch.object(prior, "LATEST_VERSION", VERSION),
        patch.object(prior, "VERSION", "local-commerce-v2"),
    ):
        suite = prior.load_dataset(root)
    return suite


def run_case(suite, entry, root, arm, *, model=None, ledger=None, offline=True, limits=m8.LIMITS):
    task = m8.Task.model_validate(entry["task"])
    observed = []
    baseline = []

    def fixture(path, ignored):
        fingerprint = prior.materialize(path, suite)
        baseline.append(m8.digest(m8.business_rows(path)))
        return fingerprint

    def snapshot(gateway, sid):
        observed[:] = [(gateway, sid)]
        return prior.authoritative_snapshot(gateway, sid)

    with ExitStack() as stack:
        if offline:
            stack.enter_context(prior.no_network())
        stack.enter_context(
            patch.object(m8, "NOW", datetime.fromisoformat(suite["manifest"]["clock"]))
        )
        stack.enter_context(patch.object(m8, "build_fixture", fixture))
        stack.enter_context(patch.object(m8, "snapshot", snapshot))
        stack.enter_context(patch.object(m8, "claim_value", prior.claim_value))
        if model is None:
            result = m8.run_task(
                task,
                copy.deepcopy(suite["contracts"][task.task_id]),
                root,
                arm,
                model=model,
                ledger=ledger,
                limits=limits,
            )
        else:
            from .live_evaluation import LIMITS, run_task

            if limits != LIMITS:
                raise ValueError("SDK trials require the frozen equal 8-request limits")
            result = run_task(task, [], root, arm, model=model, ledger=ledger)
        with m8.business_clock():
            gateway, sid = observed[0]
            final = snapshot(gateway, sid)
            if final["requests"]:
                with closing(gateway.store._connect()) as c:
                    wait = c.execute("SELECT wait_id FROM local_submission_receipts").fetchone()[0]
                if not read_submission_receipt(root, sid, m8.UID, wait):
                    raise RuntimeError("Unknown authoritative submission result")
            result = prior.grade_run(
                task, entry, result, final, baseline[0] == m8.digest(m8.business_rows(root))
            )
            result["contract"] = VERSION + "/m8.1-open-answer-v3"
            result["tool_calls"] = len(final["receipts"])
            result["unsafe_attempts"] = sum(
                c["name"] not in s.allowed_tools
                for s, r in zip(task.steps, result["steps"], strict=False)
                for c in r["attempted"]
            )
    dump(root / "v2-result.json", result)
    return result


def agent_run(suite, out, ids=None):
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    rows = []
    selected = [e for e in suite["entries"] if ids is None or e["task"]["task_id"] in ids]
    if not selected or ids and set(ids) != {e["task"]["task_id"] for e in selected}:
        raise ValueError("Unknown task selection")
    for e in selected:
        for arm in ("old", "v3") if e["task"]["cohort"] == "common" else ("v3",):
            tid = e["task"]["task_id"]
            print(tid, arm, flush=True)
            r = run_case(suite, e, private / (tid + "-" + arm), arm)
            r["family"] = e["family"]
            rows.append(r)
            dump(private / "episodes.json", rows)
    common = [r for r in rows if r["cohort"] == "common"]
    common_ids = sorted({r["task_id"] for r in common})
    stats = summarize([dict(r, global_stop=False) for r in common], common_ids)
    stats.update(
        kind="fixed_decision_runtime_contract_not_model_success",
        model_http_requests=0,
        model_quality="not_measured",
        application_increment=[prior.public_agent(r) for r in rows if r["cohort"] != "common"],
        protocol_pairs=protocol_pairs(common),
        per_arm={
            arm: dict(
                episodes=sum(r["arm"] == arm for r in common),
                sql_correct=sum(r["sql_correct"] for r in common if r["arm"] == arm),
                tool_valid=sum(r["tool_valid"] for r in common if r["arm"] == arm),
                tool_calls=sum(r["tool_calls"] for r in common if r["arm"] == arm),
                model_decisions=sum(r["model_calls"] for r in common if r["arm"] == arm),
                latency_ms=sum(r["latency_ms"] for r in common if r["arm"] == arm),
            )
            for arm in ("old", "v3")
        },
        dataset_manifest=suite["manifest"],
        source={
            p.name: file_hash(p) for p in [Path(__file__), Path(prior.__file__), Path(m8.__file__)]
        },
    )
    dump(out / "scorecard.json", stats)
    dump(
        out / "episodes.json",
        [
            prior.public_agent(r)
            | dict(
                arm=r["arm"],
                family=r["family"],
                tool_calls=r["tool_calls"],
                unsafe_attempts=r["unsafe_attempts"],
                initial_fingerprint=r["initial_fingerprint"],
            )
            for r in rows
        ],
    )
    dump(
        out / "badcases.json",
        [
            dict(
                task_id=r["task_id"],
                arm=r["arm"],
                error=r["error"],
                sql_correct=r["sql_correct"],
                raw_outcome=r["raw_outcome"],
            )
            for r in rows
            if r["raw_outcome"] != "pass"
        ],
    )
    return stats


class BoundedUser:
    """Deterministic reactive simulator. Reads public dialogue, never Gold/SQL/tool internals."""

    def __init__(self, order_id, *, max_turns=5):
        self.order_id, self.max_turns = order_id, max_turns
        self.turns = 0
        self.selected = False
        self.done = False

    def next(self, public_answer):
        if self.done or self.turns >= self.max_turns:
            return None
        self.turns += 1
        if not public_answer:
            return "我有多笔同款订单，请先列出订单让我选。"
        if not self.selected and self.order_id in public_answer:
            self.selected = True
            return f"选{self.order_id}，请告诉我是否已经发货以及现在物流。"
        if self.selected and any(
            w in public_answer for w in ["已发货", "已签收", "待发货", "尚未发货"]
        ):
            self.done = True
            return None
        return "请依据我的订单核实；不要把另一笔同款订单当成我要查的订单。"


def simulator_smoke(suite, out):
    """Reactive user + real V3 tools; controlled model decisions, never LLM user quality."""
    from .episode_budget import EpisodeLedger

    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    rows = []
    with (
        prior.no_network(),
        patch.object(m8, "NOW", datetime.fromisoformat(suite["manifest"]["clock"])),
        m8.business_clock(),
    ):
        fixture = private / "fixture"
        prior.materialize(fixture, suite)
        runtime = m8.FormalReturnPoc(fixture, m8.UID, model=m8.EvaluationModel(tap=None))
        sid = runtime.store.create(m8.UID)
        ledger = EpisodeLedger(private / "fees.jsonl", cap_cny=5, max_requests=40)
        ledger.begin("reactive-control", m8.LIMITS)
        tap = m8.ModelTap(runtime.gateway, ledger, "reactive-control")
        runtime.model.tap = tap
        tap.attach_meter()
        user = BoundedUser("O72001")
        answer = ""
        turn = 0
        with runtime:
            while (text := user.next(answer)) is not None:
                calls = (
                    [dict(id=f"sim-{turn}", name="list_orders", args={})]
                    if turn == 0
                    else [
                        dict(
                            id=f"sim-{turn}-order",
                            name="get_order",
                            args=dict(order_id=user.order_id),
                        ),
                        dict(
                            id=f"sim-{turn}-logistics",
                            name="get_logistics",
                            args=dict(order_id=user.order_id),
                        ),
                    ]
                )
                tap.queue = [dict(calls=[c]) for c in calls] + [
                    dict(
                        text="本人候选订单O72001和O72002，请选择。"
                        if turn == 0
                        else "O72001已签收；物流由合成配送完成。"
                    )
                ]
                answer = ""
                for event in runtime.stream(sid, text):
                    if event.type == "started":
                        tap.context = m8.TurnContext(sid, m8.UID, event.run_id)
                    if event.type == "completed":
                        answer = event.detail["answer"]
                rows.append(dict(user=text, public_answer=answer))
                turn += 1
        final = prior.authoritative_snapshot(runtime.gateway, sid)
        report = dict(
            kind="reactive_user_actual_tools_controlled_agent_not_model_quality",
            transcript=rows,
            user_done=user.done,
            turns=turn,
            max_turns=user.max_turns,
            tool_calls=len(final["receipts"]),
            draft=final["draft"],
            requests=final["requests"],
            model_http_requests=0,
        )
        dump(out / "scorecard.json", report)
        return report
