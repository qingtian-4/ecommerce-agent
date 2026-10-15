"""Create an immutable 80-task suite and a clean, independent business snapshot."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.scenarios import build_scenario, sha256  # noqa: E402
from acs.task_cases import build_cases, validate_cases  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/eval/multiturn_v1"))
    parser.add_argument(
        "--materialize",
        action="store_true",
        help="Rebuild only the ignored scenario of an existing frozen suite",
    )
    args = parser.parse_args()
    if args.materialize:
        manifest = json.loads((args.out / "manifest.json").read_text())
        if sha256(args.out / "cases.json") != manifest["cases_sha256"]:
            raise ValueError("冻结任务hash不一致")
        scenario = build_scenario(args.out / "scenario")
        if scenario != manifest["scenario"]:
            raise ValueError("生成业务事实与冻结清单不一致，不能用于本套评测")
        if build_cases(args.out / "scenario/business.db") != json.loads(
            (args.out / "cases.json").read_text(),
        ):
            raise ValueError("生成用例与冻结任务不同")
        print("Scenario rebuilt; frozen business facts and task cases match")
        return
    args.out.mkdir(parents=True, exist_ok=False)
    scenario = build_scenario(args.out / "scenario")
    cases = build_cases(args.out / "scenario/business.db")
    validate_cases(cases)
    path = args.out / "cases.json"
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "format": 1,
        "cases_sha256": sha256(path),
        "scenario": scenario,
        "author_knows_cases": True,
        "user": "fixed scripts, not LLM simulation",
        "development_trials": 1,
        "holdout_trials": 3,
        "success": "all deterministic checks AND calibrated semantic judge pass",
        "thresholds": {"holdout_mean": 0.85, "holdout_all_three": 0.75},
        "failure_taxonomy": "runtime/permission/confirmation/quantity/state_transition/"
        "memory/grounding/false_completion/clarification/unsupported/judge_error",
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} tasks at {args.out}; SHA256={manifest['cases_sha256']}")


if __name__ == "__main__":
    main()
