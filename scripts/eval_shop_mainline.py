"""Frozen shop controls on real SDK/runtime; paid mode requires new bounded authorization."""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_live_readiness import model_for  # noqa: E402

from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.episode_budget import PRICE, EpisodeLedger  # noqa: E402
from acs.live_evaluation import BODY_BYTES, LIMITS, OUT_TOKENS  # noqa: E402
from acs.shop_mainline_data import ROOT as DATA  # noqa: E402
from acs.shop_mainline_data import VERSION, load  # noqa: E402
from acs.shop_mainline_eval import grade, run  # noqa: E402

PILOT_IDS = ["SX02", "SX04"]


def proposal(*, uncapped=False, model_context=False):
    suite = load()
    per_request = (Decimal(2*BODY_BYTES+4096)*PRICE["peak_input_cny_per_million"]
                   + Decimal(OUT_TOKENS)*PRICE["peak_output_cny_per_million"])/1_000_000
    value = dict(contract=VERSION, authorized=False, task_ids=PILOT_IDS, arms=["old", "v3"],
        episodes=4, requests_per_episode=8, max_requests=32, cap_cny=5,
        body_bytes=BODY_BYTES, output_tokens=OUT_TOKENS, model=PRICE["model"],
        thinking=False, temperature=0, max_planned_reservation_cny=str(per_request*32),
        tariff=PRICE, official_tariff_checked="2026-10-28", validity_hours=2,
        source_sha_binding="exact committed source SHA required at authorization",
        dataset_manifest_sha256=file_hash(DATA / "manifest.json"),
        environment_manifest_sha256=suite["manifest"]["environment_manifest_sha256"],
        user_simulator="frozen text", judge="independent_review_pending",
        embedding_rerank="local BM25 only", automatic_retries=0,
        interpretation="reservation ceiling, not predicted spend; first limit wins")
    if uncapped:
        value.update(quota_mode="uncapped", cap_cny=None, max_requests=None,
                     max_planned_reservation_cny=None,
                     interpretation="Explicit current-task authorization; usage always recorded")
    value["request_protocol"] = "shared-context-v1" if model_context else "legacy"
    return value


def permit(path, out, sha, *, model_context=False):
    if path is None:
        raise ValueError("Fresh explicit paid authorization required")
    value = json.loads(path.read_text())
    uncapped = value.get("quota_mode") == "uncapped"
    plan = proposal(uncapped=uncapped, model_context=model_context)
    expiry = datetime.fromisoformat(value["expires_at"])
    remaining_seconds = (expiry-datetime.now(UTC)).total_seconds()
    bound = ("contract", "task_ids", "arms", "requests_per_episode", "body_bytes", "output_tokens",
             "model", "dataset_manifest_sha256", "environment_manifest_sha256")
    if (value.get("authorized") is not True or not value.get("user_instruction_reference")
            or not value.get("run_id") or value.get("source_sha") != sha
            or value.get("request_protocol", "legacy") != plan["request_protocol"]
            or any(value.get(k) != plan[k] for k in bound)
            or Path(value.get("output_dir", "")).resolve() != out.resolve()
            or not 0 < remaining_seconds <= 7200
            or (uncapped and (value.get("max_requests") is not None
                              or value.get("cap_cny") is not None))
            or (not uncapped and (type(value.get("max_requests")) is not int
                or not 0 < value["max_requests"] <= 32
                or type(value.get("cap_cny")) not in {int, float}
                or not 0 < value["cap_cny"] <= 5))):
        raise ValueError("Authorization source, data, limits, output or expiry invalid")
    watched = ["src", "scripts", "data/eval", "data/demo", "data/docs", "pyproject.toml", "uv.lock"]
    if (subprocess.check_output(["git", "diff", "HEAD", "--", *watched], cwd=ROOT)
            or subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard",
                                        "--", *watched], cwd=ROOT)):
        raise ValueError("Relevant source and data must be committed before paid execution")
    return value


def execute(args):
    if args.mode == "plan":
        return proposal(uncapped=getattr(args, "uncapped", False),
                        model_context=getattr(args, "model_context", False))
    suite = load()
    if args.mode == "verify":
        return suite["manifest"]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    auth = (permit(args.authorization, args.out, sha,
                   model_context=getattr(args, "model_context", False))
            if args.mode == "live" else None)
    if auth and args.price_review is None:
        raise ValueError("Fresh official price review required")
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=False)
    private = args.out / ".private-evidence"
    private.mkdir()
    dump(args.out / "manifest.json", dict(source_sha=sha, suite=suite["manifest"], mode=args.mode,
        authorization=auth, source_fingerprints={str(p.relative_to(ROOT)): file_hash(p)
            for p in [ROOT / "src/acs/shop_mainline_eval.py", ROOT / "src/acs/cs_tools.py",
                      ROOT / "src/acs/retrieval.py", Path(__file__).resolve()]}))
    paid_ledger = (EpisodeLedger(private / "fees.jsonl", cap_cny=auth["cap_cny"],
        max_requests=auth["max_requests"], uncapped_authorization=(
            auth["user_instruction_reference"] if auth.get("quota_mode") == "uncapped"
            else None)) if auth else None)
    rows, controls, ledgers, bodies = [], [], {}, []
    entries = [e for e in suite["entries"] if args.mode == "offline"
               or e["task"]["task_id"] in PILOT_IDS]
    variants = ["global", "scoped"] if args.mode == "offline" else ["scoped"]
    for variant in variants:
        for entry in entries:
            tid = entry["task"]["task_id"]
            for arm in ("old", "v3"):
                episode = f"{tid}:{arm}"
                trial = f"{variant}:{episode}"
                print(trial, flush=True)
                ledger = paid_ledger or EpisodeLedger(
                    private / (trial.replace(":", "-")+"-fees.jsonl"), max_requests=8)
                ledger.begin(episode, LIMITS)
                queue = None if auth else [copy.deepcopy(d) for turn in suite["contracts"][tid]
                                          for d in turn]
                if variant == "global":
                    for decision in queue:
                        for call in decision.get("calls", []):
                            call["args"].pop("document_id", None)
                with model_for(args.out, ledger, episode, queue=queue, auth=auth,
                               env_file=args.env_file, price_review=args.price_review) as model:
                    result = run(suite, entry, private / trial.replace(":", "-"), arm,
                        model=model, ledger=ledger, offline=not bool(auth),
                        model_context=getattr(args, "model_context", False))
                # Full raw traces stay private. Never replace original model language.
                rows.append(result | dict(variant=variant))
                ledgers[f"{variant}:{episode}"] = ledger.summary(episode)
                if not auth:
                    bodies.extend(r["body_bytes"] for r in ledger.rows()
                                  if r["type"] == "mock_http_observed")
                if not auth and variant == "scoped" and arm == "v3" and result["sql_correct"]:
                    for index, gold in enumerate(entry["intent_gold"]):
                        for text, covered in gold["answer_coverage"].items():
                            if len(covered) == len(gold["goals"]):
                                continue
                            changed = copy.deepcopy(result)
                            changed["raw_outcome"] = result["legacy_raw_outcome"]
                            changed["steps"][index]["raw"] = text
                            controls.append(dict(task=tid, step=index, sql_unchanged=True,
                                omitted=[g["id"] for g in gold["goals"] if g["id"] not in covered],
                                outcome=grade(entry, changed)["raw_outcome"]))
                dump(private / "episodes.json", rows)
                summaries = [dict(task_id=r["task_id"], arm=r["arm"], variant=r["variant"],
                    outcome=r["raw_outcome"], sql_correct=r["sql_correct"],
                    tool_valid=r["tool_valid"], error=r["error"], objectives=r["objectives"],
                    tools=r["tool_calls"]) for r in rows]
                report = dict(kind="paid_pilot" if auth else "scripted_sdk_not_model_quality",
                    paid_requests=ledger.summary()["requests"] if auth else 0,
                    cases=summaries, counts={v+":"+a: dict(Counter(r["raw_outcome"] for r in rows
                        if r["variant"] == v and r["arm"] == a)) for v in variants
                        for a in ("old", "v3")}, omission_controls=controls,
                    ledgers=ledgers, body_bytes=bodies,
                    ledger_total=(paid_ledger.summary() if auth else dict(
                        requests=sum(v["requests"] for v in ledgers.values()),
                        paid_cny=0, kind="mock_http_not_billable")),
                    model_context=getattr(args, "model_context", False),
                    semantic_scope="exact authored controls only; novel language pending_review")
                dump(args.out / "scorecard.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["verify", "offline", "rehearsal", "plan", "live"],
                        default="verify")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--price-review", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--model-context", action="store_true",
                        help="Enable reversible shared JSON and policy/completion instructions")
    parser.add_argument("--uncapped", action="store_true", help="Uncapped proposal; not permission")
    args = parser.parse_args()
    if args.mode not in {"verify", "plan"} and args.out is None:
        parser.error("--out required")
    result = execute(args)
    print(json.dumps({k: v for k, v in result.items() if k not in
                      {"cases", "omission_controls", "body_bytes", "ledgers"}}, ensure_ascii=False))
