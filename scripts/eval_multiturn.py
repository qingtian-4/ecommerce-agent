"""Run frozen local tasks, preserve every attempt, and report failures without tuning the suite."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.scenarios import scenario_settings, sha256  # noqa: E402
from acs.task_cases import validate_cases  # noqa: E402
from acs.task_eval import run_episode  # noqa: E402


def summarise(records, planned=None):
    grouped = defaultdict(list)
    for record in records:
        grouped[record["id"]].append(record)
    splits = {}
    for split in ("development", "holdout"):
        rows = [r for r in records if r["split"] == split]
        by_id = [rs for rs in grouped.values() if rs[0]["split"] == split]
        required = 1 if split == "development" else 3
        expected = ({k: v for k, v in planned.items() if v["split"] == split}
                    if planned is not None else None)

        def complete(rs, expected=expected, required=required):
            count = expected[rs[0]["id"]]["trials"] if expected is not None else required
            return (len(rs) == count
                    and {r.get("trial", i + 1) for i, r in enumerate(rs)}
                    == set(range(1, count + 1)))
        splits[split] = {
            "tasks": len(by_id),
            "episodes": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "mean_success": sum(r["passed"] for r in rows) / len(rows) if rows else None,
            "all_trials_success": sum(
                complete(rs) and all(r["passed"] for r in rs) for rs in by_id
            )
            / len(by_id)
            if by_id
            else None,
            "complete": (len(by_id) == (len(expected) if expected is not None else 40)
                         and all(complete(rs) for rs in by_id)),
        }
    turn_totals = [e["result"]["totals"] for r in records for e in r["events"] if "query" in e]
    judge_usages = [u for r in records for u in r["answer_review"]["usage"]]
    elapsed = sorted(e["elapsed_ms"] for r in records for e in r["events"] if "query" in e)
    return {
        "splits": splits,
        "failure_episodes": dict(
            Counter(f for r in records for f in {e["type"] for e in r["failures"]})
        ),
        "by_category": {
            category: {
                "episodes": sum(r["category"] == category for r in records),
                "passed": sum(r["category"] == category and r["passed"] for r in records),
            }
            for category in sorted({r["category"] for r in records})
        },
        "agent_api_requests": sum(t.get("calls", 0) for t in turn_totals),
        "agent_tokens": sum(t.get("total_tokens", 0) for t in turn_totals),
        "judge_api_requests": len(judge_usages),
        "judge_tokens": sum(u["total_tokens"] for u in judge_usages),
        "judge_unresolved": sum(any(f["type"] == "judge_error" for f in r["failures"])
                                for r in records),
        "agent_priced_cost_cny": sum(float(t.get("cost_cny") or 0) for t in turn_totals),
        "judge_priced_cost_cny": sum(float(u.get("cost_cny") or 0) for u in judge_usages),
        "cost_is_partial": any(t.get("cost_is_partial", False) for t in turn_totals)
        or any(u.get("usage_missing") or u.get("cost_cny") is None for u in judge_usages),
        "turn_latency_ms": {
            str(q): elapsed[min(len(elapsed) - 1, int((len(elapsed) - 1) * q))] if elapsed else None
            for q in (0.5, 0.95)
        },
        "semantic_grader": "Same-provider calibrated role; not human review.",
        "interpretation": "Authored scripts. Frozen first-use holdout, not blind.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("data/eval/multiturn_v1"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--split", choices=("development", "holdout", "all"), default="all")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--trials", type=int)
    parser.add_argument("--calibration", type=Path, required=True)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text())
    if not calibration.get("gate_passed"):
        raise ValueError("语义评分校准未通过，拒绝产生可称为任务完成率的结果")
    manifest = json.loads((args.suite / "manifest.json").read_text())
    cases_path = args.suite / "cases.json"
    if sha256(cases_path) != manifest["cases_sha256"]:
        raise ValueError("冻结任务hash不匹配")
    cases = json.loads(cases_path.read_text())
    validate_cases(cases)
    cases = [
        c
        for c in cases
        if (args.split == "all" or c["split"] == args.split)
        and (not args.ids or c["id"] in args.ids)
    ]
    if args.trials is not None and args.trials < 1:
        raise ValueError("trials必须为正数")
    settings = scenario_settings(get_settings(), args.suite / "scenario")
    args.out.mkdir(parents=True, exist_ok=False)
    frozen = {
        "suite": manifest,
        "calibration_sha256": sha256(args.calibration),
        "implementation_sha256": {
            str(p): sha256(p) for p in sorted([*Path("src/acs").glob("*.py"), Path(__file__)])
        },
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "thinking": settings.llm_thinking,
        "knowledge_mode": settings.knowledge_mode,
        "context_max_chars": settings.context_max_chars,
        "turn_timeout_s": settings.turn_timeout_s,
        "max_steps": settings.max_steps,
        "max_api_requests": settings.max_api_requests,
        "worker_count": 2,
        "case_ids": [c["id"] for c in cases],
        "trials_override": args.trials,
    }
    (args.out / "frozen_manifest.json").write_text(json.dumps(frozen, indent=2) + "\n")
    planned = {c["id"]: {"split": c["split"], "trials": args.trials or manifest.get(
        c["split"] + "_trials", 1 if c["split"] == "development" else 3,
    )} for c in cases}
    frozen["planned"] = planned
    (args.out / "frozen_manifest.json").write_text(json.dumps(frozen, indent=2) + "\n")
    records = []
    with (args.out / "raw.jsonl").open("x") as raw, ThreadPoolExecutor(2) as pool:
        futures = {
            pool.submit(run_episode, settings, case): (case, trial)
            for case in cases
            for trial in range(1, planned[case["id"]]["trials"] + 1)
        }
        for future in as_completed(futures):
            case, trial = futures[future]
            # Preserve infrastructure failures as failed episodes rather than dropping them.
            try:
                record = future.result()
            except Exception as exc:
                record = {
                    "id": case["id"],
                    "split": case["split"],
                    "category": case["category"],
                    "passed": False,
                    "events": [],
                    "checks": {"runner": False},
                    "answer_review": {"usage": []},
                    "failures": [{"type": "runtime", "evidence": type(exc).__name__}],
                }
            record["trial"] = trial
            raw.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw.flush()
            records.append(record)
            summary = summarise(records, planned) | {
                "raw_sha256": sha256(args.out / "raw.jsonl"),
            }
            (args.out / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
            )
            (args.out / "failures.json").write_text(
                json.dumps(
                    [
                        {"id": r["id"], "trial": r["trial"], "failures": r["failures"]}
                        for r in records
                        if not r["passed"]
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            print(
                f"{len(records)}/{len(futures)} {case['id']} trial={trial} "
                f"pass={record['passed']} failures="
                + ",".join(sorted({f["type"] for f in record["failures"]})),
                flush=True,
            )


if __name__ == "__main__":
    main()
