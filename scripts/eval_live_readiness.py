"""M8.1 SDK/SQLite rehearsal by default; live mode requires NEW explicit permission."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from acs.config import Settings  # noqa: E402
from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.episode_transport import EpisodeCallback  # noqa: E402
from acs.langgraph_poc import deepseek_model  # noqa: E402
from acs.live_evaluation import (  # noqa: E402
    CONTRACT,
    GLOBAL_CNY,
    GLOBAL_REQUESTS,
    LIMITS,
    PILOT_IDS,
    BoundedTransport,
    paired_run,
    pilot_plan,
    protocol_pairs,
    run_task,
    summarize,
)
from acs.multiturn_evaluation import load_suite  # noqa: E402
from acs.response_evidence import ResponseEvidence  # noqa: E402

SUITE = ROOT / "data/eval/v3_multiturn_m8_v2"
VERSION = ROOT / "data/eval/v3_multiturn_m8_1_v3/manifest.json"


def load_contract():
    manifest = json.loads(VERSION.read_text())
    for name, expected in manifest["implementation"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("M8.1 source fingerprint mismatch: " + name)
    tasks, contracts, old = load_suite(SUITE)
    if manifest["inherited_files"] != old["files"]:
        raise ValueError("Inherited task fingerprint mismatch")
    return tasks, contracts, manifest


def authorization(path, sha, out):
    if path is None:
        raise ValueError("Fresh explicit paid authorization required; proposal is NOT permission")
    data = json.loads(path.read_text())
    if (
        data.get("authorized") is not True
        or not data.get("user_instruction_reference")
        or data.get("source_sha") != sha
        or data.get("contract") != CONTRACT
        or data.get("task_ids") != PILOT_IDS
        or data.get("arms") != ["old", "v3"]
        or not data.get("run_id")
        or Path(data.get("output_dir", "")).resolve() != out.resolve()
        or datetime.fromisoformat(data["expires_at"]) <= datetime.now(UTC)
        or not 0 < data["cap_cny"] <= GLOBAL_CNY
        or not 0 < data["max_requests"] <= GLOBAL_REQUESTS
    ):
        raise ValueError("Authorization source, scope, expiry or limits invalid")
    return data


def mock_response(item):
    calls = [
        {
            "id": c["id"],
            "type": "function",
            "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)},
        }
        for c in item.get("calls", [])
    ]
    return httpx.Response(
        200,
        json={
            "id": "offline-sdk",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": {
                        "role": "assistant",
                        "content": item.get("text", ""),
                        **({"tool_calls": calls} if calls else {}),
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


@contextmanager
def model_for(out, ledger, episode, *, queue=None, auth=None, env_file=None, price_review=None):
    """Real locked ChatOpenAI serializer/callbacks for BOTH offline and future live."""
    if auth:
        from local_return_poc import RequestCapture, verify_price

        settings = Settings(_env_file=env_file)
        price_root = out / ".private-evidence" / "price"
        price_root.mkdir(parents=True, exist_ok=True)
        (price_root / "price_review.json").write_bytes(price_review.read_bytes())
        verify_price(price_root, settings)  # Official snapshots, no inference HTTP.
        expires = auth["expires_at"]
    else:
        settings = Settings(
            _env_file=None,
            llm_api_key="offline-test",
            llm_model="deepseek-flash",
            llm_base_url="https://offline.invalid",
        )
        expires = "2099-01-01T00:00:00+00:00"
    settings = settings.model_copy(
        update={
            "llm_temperature": 0,
            "llm_max_output_tokens": 1024,
            "llm_thinking": False,
            "llm_timeout_s": 60,
        }
    )
    ledger.evidence = ResponseEvidence(
        out / ".private-evidence/raw", secrets=(settings.llm_api_key,)
    )

    def respond(request):
        ledger.event(episode, "mock_http_observed", body_bytes=len(request.content))
        ledger.evidence.save(uuid4().hex, {"offline_request_body": json.loads(request.content)})
        return mock_response(queue.pop(0))

    factory = (
        (lambda: RequestCapture(ledger, ledger.evidence))
        if auth
        else (lambda: httpx.MockTransport(respond))
    )
    with httpx.Client(
        transport=BoundedTransport(ledger, episode, expires, inner_factory=factory)
    ) as http:
        yield deepseek_model(
            settings, http_client=http, callbacks=[EpisodeCallback(ledger, episode)]
        )


def execute(args):
    os.umask(0o077)
    tasks, contracts, manifest = load_contract()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if args.mode == "pilot-plan":
        return dict(pilot_plan(), source_sha=sha)
    if args.mode == "inspect":
        # Reading an interrupted experiment must never reopen/reset its ledger.
        private = args.out / ".private-evidence"
        events = [json.loads(line) for line in (private / "fees.jsonl").read_text().splitlines()]
        path = private / "episodes.json"
        rows = json.loads(path.read_text()) if path.exists() else []
        started = {r["episode"] for r in events if r["type"] == "arm_started"}
        ended = {r["episode"] for r in events if r["type"] == "arm_completed"}
        return dict(
            summarize(rows, PILOT_IDS),
            interrupted_episodes=sorted(started - ended),
            writes_replayed=0,
            inspect_only=True,
        )
    auth = None
    if args.mode == "live":
        auth = authorization(args.authorization, sha, args.out)
        if args.price_review is None:
            raise ValueError("Fresh price verification required")
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
            raise ValueError("Live source must be clean and frozen")
    args.out.mkdir(parents=True, exist_ok=False)
    private = args.out / ".private-evidence"
    private.mkdir(mode=0o700)
    ledger = EpisodeLedger(
        private / "fees.jsonl",
        cap_cny=auth["cap_cny"] if auth else GLOBAL_CNY,
        max_requests=auth["max_requests"] if auth else GLOBAL_REQUESTS,
    )
    run_manifest = dict(
        source_sha=sha,
        source_worktree_dirty=bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        contract=manifest,
        mode=args.mode,
        plan=pilot_plan(),
        authorization=auth,
        status="running",
        logical_business_clock="2026-10-25T04:00:00+00:00",
    )
    (args.out / "manifest.json").write_text(json.dumps(run_manifest, indent=2))

    def one(tid, arm):
        task = next(t for t in tasks if t.task_id == tid)
        episode = tid + ":" + arm
        ledger.begin(episode, LIMITS)
        queue = [dict(item) for step in contracts[tid] for item in step]
        if args.mode == "rehearsal-open":
            variants = json.loads((VERSION.parent / "sdk_open_answers.json").read_text())[tid]
            finals = [item for item in queue if not item.get("calls")]
            for item, text in zip(finals, variants, strict=True):
                item["text"] = text
        with model_for(
            args.out,
            ledger,
            episode,
            queue=queue,
            auth=auth,
            env_file=args.env_file,
            price_review=args.price_review,
        ) as model:
            return run_task(task, [], private / (tid + "-" + arm), arm, model=model, ledger=ledger)

    def persist(rows, report):
        (private / "episodes.json").write_text(json.dumps(rows, ensure_ascii=False, default=str))
        queue = [q for r in rows for q in r.get("review_queue", [])]
        (private / "review-queue-with-operator-key.json").write_text(
            json.dumps(queue, ensure_ascii=False, default=str)
        )
        reviewer = [
            {k: q[k] for k in ("review_id", "user_inputs", "answer", "evidence")} for q in queue
        ]
        (private / "reviewer-queue.json").write_text(
            json.dumps(reviewer, ensure_ascii=False, default=str)
        )
        public = dict(
            report,
            mode=args.mode,
            actual_paid_requests=ledger.summary()["requests"] if auth else 0,
            mock_http_requests=0 if auth else ledger.summary()["requests"],
            ledger_usage_kind="provider_usage" if auth else "synthetic_mock_usage",
            ledger=ledger.summary(),
            episode_checks=[
                {
                    k: r.get(k)
                    for k in (
                        "task_id",
                        "arm",
                        "raw_outcome",
                        "display_outcome",
                        "sql_correct",
                        "tool_valid",
                        "model_http_requests",
                        "failure_class",
                        "global_stop",
                        "latency_ms",
                    )
                }
                for r in rows
            ],
        )
        if all("request_signatures" in r for r in rows):
            public["protocol_pairs"] = protocol_pairs(rows)
        (args.out / "scorecard.json").write_text(json.dumps(public, indent=2))

    def no_network(*args, **kwargs):
        raise AssertionError("External network forbidden during M8.1 rehearsal")

    if auth:
        report = paired_run(PILOT_IDS, one, ledger, persist=persist)
    else:
        with patch.object(socket.socket, "connect", no_network):
            report = paired_run(PILOT_IDS, one, ledger, persist=persist)
    run_manifest["status"] = "stopped" if report["global_stop"] else "finished"
    (args.out / "manifest.json").write_text(json.dumps(run_manifest, indent=2))
    return json.loads((args.out / "scorecard.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["rehearsal", "rehearsal-open", "pilot-plan", "live", "inspect"],
        default="rehearsal",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--price-review", type=Path)
    parser.add_argument("--env-file", type=Path)
    print(json.dumps(execute(parser.parse_args()), ensure_ascii=False, indent=2))
