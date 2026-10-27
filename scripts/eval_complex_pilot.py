"""Mock SDK by default; real HTTP requires a fresh source/data/budget-bound authorization."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from eval_live_readiness import model_for  # noqa: E402

from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.commerce_benchmark_v2 import run_case  # noqa: E402
from acs.commerce_complex_data import ROOT as DATA  # noqa: E402
from acs.commerce_complex_data import VERSION, load  # noqa: E402
from acs.commerce_complex_eval import grade_objectives  # noqa: E402
from acs.config import Settings  # noqa: E402
from acs.episode_budget import PRICE, EpisodeLedger  # noqa: E402
from acs.live_evaluation import BODY_BYTES, LIMITS, OUT_TOKENS, paired_run  # noqa: E402

IDS = ["CX01", "CX04"]
MAX_REQUESTS = 32
CAP_CNY = 5


def proposal():
    bound = 2 * BODY_BYTES + 4096
    per_request = (Decimal(bound) * PRICE["peak_input_cny_per_million"]
                   + Decimal(OUT_TOKENS) * PRICE["peak_output_cny_per_million"]) / 1_000_000
    suite = load()
    return dict(contract=VERSION, authorized=False, task_ids=IDS, arms=["old", "v3"],
                episodes=4, requests_per_episode=LIMITS.requests, max_requests=MAX_REQUESTS,
                cap_cny=CAP_CNY, body_bytes=BODY_BYTES, output_tokens=OUT_TOKENS,
                input_token_reservation_bound=bound,
                max_reservation_per_request_cny=str(per_request),
                max_planned_reservation_cny=str(per_request * MAX_REQUESTS),
                model=PRICE["model"], thinking=False, temperature=0,
                served_model_at_price_review="DeepSeek-V4.1-Flash-1017",
                tariff=PRICE, official_tariff_checked="2026-10-27",
                source_sha_binding="Exact committed SHA is required in the later authorization",
                dataset_manifest_sha256=file_hash(DATA / "manifest.json"),
                environment_manifest_sha256=suite["manifest"]["environment_manifest_sha256"],
                user_simulator="frozen user text; no paid user model",
                judge="independent review pending; no paid judge",
                embedding_rerank="no paid calls; existing local business/knowledge tools",
                retries=0, interpretation="Budget ceiling, not expected spend or quality guarantee")


def permit(path, out, sha):
    if path is None:
        raise ValueError("Fresh explicit paid authorization required")
    a = json.loads(path.read_text())
    plan = proposal()
    expected = {k: plan[k] for k in ["contract", "task_ids", "arms", "body_bytes",
                                    "output_tokens", "model", "dataset_manifest_sha256",
                                    "environment_manifest_sha256", "requests_per_episode"]}
    if (a.get("authorized") is not True or not a.get("user_instruction_reference")
            or not a.get("run_id") or a.get("source_sha") != sha
            or any(a.get(k) != v for k, v in expected.items())
            or Path(a.get("output_dir", "")).resolve() != out.resolve()
            or datetime.fromisoformat(a["expires_at"]) <= datetime.now(UTC)
            or type(a.get("max_requests")) is not int
            or not 0 < a["max_requests"] <= MAX_REQUESTS
            or type(a.get("cap_cny")) not in {int, float}
            or not 0 < a["cap_cny"] <= CAP_CNY):
        raise ValueError("Authorization source, data, limits, output or expiry invalid")
    watched = ["src", "scripts", "data/eval", "data/docs", "pyproject.toml", "uv.lock"]
    if (subprocess.check_output(["git", "diff", "HEAD", "--", *watched], cwd=ROOT)
            or subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard",
                                        "--", *watched], cwd=ROOT)):
        raise ValueError("Relevant source and data must be committed before paid execution")
    return a


def run(args):
    if args.mode == "plan":
        return proposal()
    os.umask(0o077)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    auth = None
    if args.mode == "live":
        auth = permit(args.authorization, args.out, sha)
        if args.price_review is None:
            raise ValueError("Fresh official price review required")
        args.env_file = args.env_file or ROOT / ".env"
        if not Settings(_env_file=args.env_file).llm_api_key:
            raise ValueError("DeepSeek key missing; no episode created")
    suite = load()
    args.out.mkdir(parents=True, exist_ok=False)
    private = args.out / ".private-evidence"
    private.mkdir(mode=0o700)
    ledger = EpisodeLedger(private / "fees.jsonl", cap_cny=auth["cap_cny"] if auth else CAP_CNY,
                           max_requests=auth["max_requests"] if auth else MAX_REQUESTS)
    sources = ["src/acs/cs_tools.py", "src/acs/commerce_complex_data.py",
               "src/acs/commerce_complex_eval.py", "src/acs/multiturn_evaluation.py",
               "scripts/eval_complex_pilot.py", "scripts/eval_live_readiness.py"]
    dump(args.out / "manifest.json", dict(
        source_sha=sha, source_worktree_dirty=bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT)),
        source_fingerprints={p: file_hash(ROOT / p) for p in sources},
        dataset=suite["manifest"], mode=args.mode, authorization=auth, plan=proposal()))

    def one(tid, arm):
        entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
        episode = tid + ":" + arm
        ledger.begin(episode, LIMITS)
        queue = None if auth else [copy.deepcopy(d) for t in suite["contracts"][tid] for d in t]
        with model_for(args.out, ledger, episode, queue=queue, auth=auth,
                       env_file=args.env_file, price_review=args.price_review) as model:
            result = run_case(suite, entry, private / (tid + "-" + arm), arm, model=model,
                              ledger=ledger, offline=not bool(auth), limits=LIMITS)
        result["legacy_raw_outcome"] = result["raw_outcome"]
        result.update(grade_objectives(entry, result))
        return result

    def persist(rows, report):
        dump(private / "episodes.json", rows)
        dump(private / "review_queue.json", [q for r in rows for q in r.get("review_queue", [])])
        dump(args.out / "scorecard.json", report | dict(
            kind="paid_model_pilot" if auth else "mock_sdk_rehearsal_not_model_quality",
            paid_requests=ledger.summary()["requests"] if auth else 0,
            mock_requests=0 if auth else ledger.summary()["requests"],
            request_body_bytes=[r["body_bytes"] for r in ledger.rows()
                                if r["type"] == "mock_http_observed"],
            ledger=ledger.summary(),
            objectives=[dict(task_id=r["task_id"], arm=r["arm"],
                             goals=r.get("objectives", [])) for r in rows]))

    paired_run(IDS, one, ledger, persist=persist)
    return json.loads((args.out / "scorecard.json").read_text())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["plan", "rehearsal", "live"], default="plan")
    p.add_argument("--out", type=Path)
    p.add_argument("--authorization", type=Path)
    p.add_argument("--price-review", type=Path)
    p.add_argument("--env-file", type=Path)
    a = p.parse_args()
    if a.mode != "plan" and a.out is None:
        p.error("--out is required")
    print(json.dumps(run(a), ensure_ascii=False, indent=2))
