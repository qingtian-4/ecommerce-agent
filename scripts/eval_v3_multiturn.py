"""M8 frozen isolated tasks. Offline by default; live requires fresh authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from acs.episode_budget import PRICE, EpisodeLedger  # noqa: E402
from acs.multiturn_evaluation import LIMITS, digest, load_suite, run_task, summarise  # noqa: E402

PILOT_IDS = ["D02", "D03", "D04", "D05", "D07", "D09"]


def authorization(path, sha, ids, out=None):
    if path is None:
        raise ValueError(
            "Fresh explicit paid authorization required; expired M7 limits are invalid"
        )
    data = json.loads(path.read_text())
    if (
        data.get("authorized") is not True
        or not data.get("user_instruction_reference")
        or data.get("source_sha") != sha
        or data.get("task_ids") != ids
        or data.get("arms") != ["old", "v3"]
        or not data.get("run_id")
        or datetime.fromisoformat(data["expires_at"]) <= datetime.now(UTC)
        or not 0 < data["cap_cny"] <= 5
        or not 0 < data["max_requests"] <= 48
    ):
        raise ValueError("Authorization identity, source, tasks, expiry or limits invalid")
    if out is not None and Path(data.get("output_dir", "")).resolve() != out.resolve():
        raise ValueError("Authorization must bind one unique output directory and ledger")
    return data


@contextmanager
def live_model(out, ledger, episode, env_file, price_review, expires_at):
    import httpx
    from local_return_poc import RequestCapture, verify_price

    from acs.config import Settings
    from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport
    from acs.langgraph_poc import deepseek_model
    from acs.response_evidence import ResponseEvidence

    settings = Settings(_env_file=env_file).model_copy(
        update={
            "llm_temperature": 0,
            "llm_max_output_tokens": 1024,
            "llm_thinking": False,
            "llm_timeout_s": 60,
        }
    )
    price_root = out / ".private-evidence/price"
    price_root.mkdir(parents=True, exist_ok=True)
    (price_root / "price_review.json").write_bytes(price_review.read_bytes())
    verify_price(price_root, settings)  # Exact saved official CNY/USD snapshots, fail closed.
    ledger.evidence = ResponseEvidence(
        out / ".private-evidence/raw", secrets=(settings.llm_api_key,)
    )

    class BoundedCapture(RequestCapture):
        async def handle_async_request(self, request):
            if datetime.now(UTC) >= datetime.fromisoformat(expires_at):
                raise ValueError("Paid authorization deadline expired before HTTP")
            if len(await request.aread()) > 20000:
                raise ValueError("Frozen 20000-byte HTTP body bound exceeded before network")
            return await super().handle_async_request(request)

    with httpx.Client(
        transport=EpisodeSyncTransport(
            ledger, episode, "agent", lambda: BoundedCapture(ledger, ledger.evidence)
        )
    ) as http:
        yield deepseek_model(
            settings, http_client=http, callbacks=[EpisodeCallback(ledger, episode)]
        )


def execute(args):
    os.umask(0o077)
    tasks, contracts, manifest = load_suite(args.suite)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ids = args.ids or [t.task_id for t in tasks]
    if not set(ids) <= {t.task_id for t in tasks} or len(ids) != len(set(ids)):
        raise ValueError("Unknown or duplicate task IDs")
    source = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            ROOT / "src/acs/multiturn_evaluation.py",
            Path(__file__),
            ROOT / "src/acs/answer_grounding.py",
        ]
    }
    if args.mode == "pilot-plan":
        # A proposal, never authorization. No API keys read or ledger opened.
        return {
            "task_ids": PILOT_IDS,
            "arms": ["old", "v3"],
            "episodes": 12,
            "requests_per_episode": 4,
            "total_request_ceiling": 48,
            "output_token_ceiling": 1024,
            "http_body_bytes_ceiling": 20000,
            "conservative_input_bound": 44096,
            "reservation_per_request_cny": "0.096384",
            "total_worst_reservation_cny": str(Decimal("0.096384") * 48),
            "suggested_new_cap_cny": 5,
            "price_assumption": PRICE,
            "price_reverification_required": True,
            "source_sha": sha,
            "paid_authorization_present": False,
            "failure_retry": False,
        }
    auth = None
    if args.mode == "live":
        if ids != PILOT_IDS:
            raise ValueError("Live pilot restricted to the six frozen development tasks")
        auth = authorization(args.authorization, sha, ids, args.out)
        if args.price_review is None:
            raise ValueError("Fresh reviewed official prices required")
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ValueError("Live source must be clean and frozen")
    args.out.mkdir(parents=True, exist_ok=False)
    private = args.out / ".private-evidence"
    private.mkdir(mode=0o700)
    run_manifest = {
        "source_sha": sha,
        "source_fingerprints": source,
        "suite": manifest,
        "mode": args.mode,
        "task_order": ids,
        "arms": ["old", "v3"],
        "authorization": auth,
        "logical_business_clock": "2026-10-25T04:00:00+00:00",
        "single_worker": True,
        "retries": 0,
        "status": "running",
    }
    (args.out / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2))
    rows = []
    ledger = (
        EpisodeLedger(
            private / "fees.jsonl", cap_cny=auth["cap_cny"], max_requests=auth["max_requests"]
        )
        if auth
        else None
    )
    for tid in ids:
        task = next(t for t in tasks if t.task_id == tid)
        for arm in ["old", "v3"] if task.cohort == "common" else ["v3"]:
            root = private / (tid + "-" + arm)
            try:
                if auth:
                    episode = tid + ":" + arm
                    ledger.begin(episode, replace(LIMITS, requests=4))
                    with live_model(
                        args.out,
                        ledger,
                        episode,
                        args.env_file,
                        args.price_review,
                        auth["expires_at"],
                    ) as model:
                        record = run_task(
                            task,
                            [],
                            root,
                            arm,
                            model=model,
                            ledger=ledger,
                            limits=replace(LIMITS, requests=4),
                        )
                else:
                    record = run_task(task, contracts[tid], root, arm)
            except Exception as exc:
                # Startup/price/fixture failures remain planned failed episodes.
                # Never export the original exception body to public logs.
                record = dict(
                    task_id=tid,
                    split=task.split,
                    cohort=task.cohort,
                    arm=arm,
                    status="failed",
                    error={"type": type(exc).__name__},
                    steps=[],
                    final={},
                    initial_fingerprint=None,
                    sql_correct=False,
                    tool_valid=False,
                    task_success=False,
                    model_calls=0,
                    model_http_requests=0,
                    input_tokens=None,
                    output_tokens=None,
                    latency_ms=0,
                    request_signatures=[],
                )
                (private / (tid + "-" + arm + "-startup-error.json")).write_text(
                    json.dumps({"type": type(exc).__name__, "message": str(exc)})
                )
            rows.append(record)
            # Raw native answers and full model requests remain private. Public
            # result data is synthetic but still exports summaries, not raw prompts.
            (private / "episodes.json").write_text(
                json.dumps(rows, ensure_ascii=False, default=str)
            )
            public = summarise(rows)
            public["planned_episodes"] = sum(
                2 if t.cohort == "common" else 1 for t in tasks if t.task_id in ids
            )
            public["completed_episodes"] = len(rows)
            public["execution_gate_passed"] = (
                len(rows) == public["planned_episodes"]
                and all(r["sql_correct"] and r["tool_valid"] and not r["error"] for r in rows)
                and all(pair["same_initial_db"] for pair in public["pairs"])
                and (
                    auth is not None
                    or all(
                        all(
                            pair[k]
                            for k in (
                                "same_final_business_state",
                                "same_normalized_model_inputs",
                                "same_tool_calls",
                            )
                        )
                        for pair in public["pairs"]
                    )
                )
            )
            public["episode_checks"] = [
                {
                    k: r[k]
                    for k in (
                        "task_id",
                        "split",
                        "cohort",
                        "arm",
                        "status",
                        "error",
                        "sql_correct",
                        "tool_valid",
                        "task_success",
                        "model_calls",
                        "model_http_requests",
                        "latency_ms",
                    )
                }
                for r in rows
            ]
            for row in public["episode_checks"]:
                if row["error"]:
                    row["error"] = {"type": row["error"]["type"]}
            (args.out / "scorecard.json").write_text(
                json.dumps(public, ensure_ascii=False, indent=2)
            )
            print(
                tid,
                arm,
                record["status"],
                "sql",
                record["sql_correct"],
                "tool",
                record["tool_valid"],
                flush=True,
            )
            if auth and (record["error"] or not record["sql_correct"] or not record["tool_valid"]):
                run_manifest["status"] = "stopped_no_retry"
                break
        if run_manifest["status"] == "stopped_no_retry":
            break
    if run_manifest["status"] == "running":
        run_manifest["status"] = "completed"
    sql_audit, answer_audit = [], []
    for row in rows:
        root = private / (row["task_id"] + "-" + row["arm"])
        if not row.get("final"):
            continue
        with sqlite3.connect(
            (root / "business.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            requests = [
                dict(r)
                for r in conn.execute(
                    "SELECT request_id,preview_id,order_id,item_id,quantity,status "
                    "FROM return_requests"
                )
            ]
        sql_audit.append(
            {
                "task_id": row["task_id"],
                "arm": row["arm"],
                "requests": requests,
                "counts": row["final"]["counts"],
                "steps": [
                    {
                        "step": step["step"],
                        "action": step["action"],
                        "sql_correct": step["sql_correct"],
                        "tool_valid": step["tool_valid"],
                        "recovery": step["recovery"],
                        "recovery_diagnostic": step["recovery_diagnostic"],
                        "draft": {
                            k: v
                            for k, v in (step["state"]["draft"] or {}).items()
                            if k
                            in ("status", "order_id", "item_id", "quantity", "reason", "revision")
                        },
                        "preview_count": step["state"]["preview_count"],
                        "valid_preview": step["state"]["valid_preview"],
                        "counts": step["state"]["counts"],
                        "attempted": step["attempted"],
                    }
                    for step in row["steps"]
                ],
            }
        )
        answer_audit.extend(
            {
                "task_id": row["task_id"],
                "arm": row["arm"],
                "step": step["step"],
                "raw_sha256": hashlib.sha256(step["raw"].encode()).hexdigest(),
                "display_sha256": hashlib.sha256(step["display"].encode()).hexdigest(),
                "raw_grade": step["raw_grade"],
                "raw_goal_covered": step["raw_goal_covered"],
                "display_goal_covered": step["display_goal_covered"],
                "display_grade": step["display_grade"],
                "findings": step["findings"],
            }
            for step in row["steps"]
            if step["raw"] is not None
        )
    (args.out / "sql_checks.json").write_text(json.dumps(sql_audit, ensure_ascii=False, indent=2))
    (args.out / "answer_audit.json").write_text(
        json.dumps(answer_audit, ensure_ascii=False, indent=2)
    )
    # Novel responses are anonymized to a blind review queue; no Judge calls.
    queue = []
    key = []
    for r in rows:
        for s in r["steps"]:
            if s["raw"] is None:
                continue
            for kind in ("raw", "display"):
                qid = digest([r["task_id"], r["arm"], s["step"], kind])
                queue.append(
                    {
                        "review_id": qid,
                        "text": s[kind],
                        "evidence": {
                            k: s["state"][k]
                            for k in (
                                "preview_count",
                                "previous_preview_count",
                                "valid_preview",
                                "requests",
                                "counts",
                                "products",
                            )
                        }
                        | {
                            "draft": {
                                k: v
                                for k, v in (s["state"]["draft"] or {}).items()
                                if k
                                in (
                                    "status",
                                    "order_id",
                                    "item_id",
                                    "quantity",
                                    "reason",
                                    "condition",
                                    "revision",
                                )
                            },
                            "policy": {k: s["state"]["policy"][k] for k in ("windows", "shipping")},
                        },
                    }
                )
                key.append(
                    {"review_id": qid, "arm": r["arm"], "kind": kind, "task_id": r["task_id"]}
                )
    (private / "blind-review.json").write_text(
        json.dumps(sorted(queue, key=lambda q: q["review_id"]), ensure_ascii=False)
    )
    (private / "review-key.json").write_text(json.dumps(key))
    run_manifest["episodes"] = len(rows)
    if ledger:
        run_manifest["fees"] = ledger.summary()
    (args.out / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2))
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "data/eval/v3_multiturn_m8_v2")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--mode", choices=["offline", "pilot-plan", "live"], default="offline")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--price-review", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    result = execute(args)
    print(json.dumps(result, ensure_ascii=False))
    if args.mode != "pilot-plan" and result.get("execution_gate_passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
