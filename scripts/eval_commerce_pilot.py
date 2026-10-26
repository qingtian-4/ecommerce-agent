"""Small SDK rehearsal or authorized paired DeepSeek pilot over the frozen v2 corpus."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from eval_live_readiness import model_for  # noqa: E402

from acs.commerce_benchmark_data import dump  # noqa: E402
from acs.commerce_benchmark_v2 import load, run_case  # noqa: E402
from acs.commerce_benchmark_v2_data import ROOT as SUITE  # noqa: E402
from acs.commerce_benchmark_v2_data import VERSION  # noqa: E402
from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.live_evaluation import LIMITS, paired_run  # noqa: E402

IDS = ["EC02-1", "EC03-1"]


def permit(path, out, sha):
    a = json.loads(path.read_text())
    if (
        a.get("authorized") is not True
        or not a.get("user_instruction_reference")
        or a.get("source_sha") != sha
        or a.get("contract") != VERSION
        or a.get("task_ids") != IDS
        or a.get("arms") != ["old", "v3"]
        or not a.get("run_id")
        or Path(a.get("output_dir", "")).resolve() != out.resolve()
        or datetime.fromisoformat(a["expires_at"]) <= datetime.now(UTC)
        or not 0 < a["cap_cny"] <= 5
        or not 0 < a["max_requests"] <= 40
    ):
        raise ValueError("Paid authorization scope, source, expiry or budget invalid")
    if subprocess.check_output(
        ["git", "diff", "HEAD", "--", "src", "scripts", "data/eval/commerce_benchmark_v2"], cwd=ROOT
    ):
        raise ValueError("Relevant source/input must be committed before paid execution")
    return a


def run(args):
    os.umask(0o077)
    suite = load(SUITE)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    auth = None
    if args.mode == "live":
        if args.authorization is None or args.price_review is None:
            raise ValueError("Fresh permission and price required")
        auth = permit(args.authorization, args.out, sha)
    args.out.mkdir(parents=True, exist_ok=False)
    private = args.out / ".private-evidence"
    private.mkdir(mode=0o700)
    ledger = EpisodeLedger(
        private / "fees.jsonl",
        cap_cny=auth["cap_cny"] if auth else 5,
        max_requests=auth["max_requests"] if auth else 40,
    )
    dump(
        args.out / "manifest.json",
        dict(
            source_sha=sha,
            mode=args.mode,
            contract=VERSION,
            task_ids=IDS,
            authorization=auth,
            logical_clock=suite["manifest"]["clock"],
            dataset=suite["manifest"],
            source_dirty_unrelated=bool(
                subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
            ),
        ),
    )

    def one(tid, arm):
        entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
        episode = tid + ":" + arm
        ledger.begin(episode, LIMITS)
        queue = [dict(d) for turn in suite["contracts"][tid] for d in turn]
        with model_for(
            args.out,
            ledger,
            episode,
            queue=queue,
            auth=auth,
            env_file=args.env_file,
            price_review=args.price_review,
        ) as model:
            return run_case(
                suite,
                entry,
                private / (tid + "-" + arm),
                arm,
                model=model,
                ledger=ledger,
                offline=not bool(auth),
                limits=LIMITS,
            )

    def persist(rows, report):
        dump(private / "episodes.json", rows)
        queue = [q for r in rows for q in r.get("review_queue", [])]
        dump(private / "review-queue-with-key.json", queue)
        dump(
            private / "reviewer-queue.json",
            [{k: q[k] for k in ["review_id", "user_inputs", "answer", "evidence"]} for q in queue],
        )
        dump(
            args.out / "scorecard.json",
            report
            | dict(
                mode=args.mode,
                source_sha=sha,
                actual_paid_requests=ledger.summary()["requests"] if auth else 0,
                mock_http_requests=0 if auth else ledger.summary()["requests"],
                ledger=ledger.summary(),
                episode_checks=[
                    {
                        k: r.get(k)
                        for k in [
                            "task_id",
                            "arm",
                            "raw_outcome",
                            "display_outcome",
                            "sql_correct",
                            "tool_valid",
                            "model_http_requests",
                            "latency_ms",
                            "tool_calls",
                            "failure_class",
                            "global_stop",
                            "error",
                        ]
                    }
                    for r in rows
                ],
            ),
        )

    paired_run(IDS, one, ledger, persist=persist)
    return json.loads((args.out / "scorecard.json").read_text())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["rehearsal", "live"], default="rehearsal")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--authorization", type=Path)
    p.add_argument("--price-review", type=Path)
    p.add_argument("--env-file", type=Path)
    print(json.dumps(run(p.parse_args()), ensure_ascii=False, indent=2))
