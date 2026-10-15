"""Correct evidence presentation on identical frozen transcripts; never rerun the tested agent."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval_multiturn import summarise  # noqa: E402

from acs.config import get_settings  # noqa: E402
from acs.llm import LLMClient  # noqa: E402
from acs.scenarios import scenario_settings, sha256  # noqa: E402
from acs.task_eval import answer_facts, judge_record  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("data/eval/multiturn_v1"))
    parser.add_argument("--raw", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    args = parser.parse_args()
    if not json.loads(args.calibration.read_text())["gate_passed"]:
        raise ValueError("评分校准没有通过")
    cases = {c["id"]: c for c in json.loads((args.suite / "cases.json").read_text())}
    records = [json.loads(line) for path in args.raw for line in path.read_text().splitlines()]
    if len({(r["id"], r["trial"]) for r in records}) != len(records):
        raise ValueError("重复的任务/尝试，不能合并不同运行充当独立重复")
    settings = scenario_settings(get_settings(), args.suite / "scenario")
    args.out.mkdir(parents=True, exist_ok=False)
    frozen = {
        "source_raw_sha256": {str(p): sha256(p) for p in args.raw},
        "suite_sha256": sha256(args.suite / "cases.json"),
        "calibration_sha256": sha256(args.calibration),
        "grader_sha256": sha256(Path("src/acs/task_eval.py")),
        "script_sha256": sha256(Path(__file__)),
        "model": settings.llm_model,
        "scope": "Scoring correction on identical answers and SQL checks, not agent improvement.",
    }
    (args.out / "frozen_manifest.json").write_text(json.dumps(frozen, indent=2) + "\n")

    def one(record):
        case = cases[record["id"]]
        client = LLMClient(settings=settings.model_copy(update={"context_max_chars": 200000}))
        review = judge_record(
            client,
            {
                "rubric": case["answer_rubric"],
                "facts": answer_facts(settings, case),
                "events": record["events"],
            },
        )
        deterministic_failures = [
            f
            for f in record["failures"]
            if f["evidence"] in {k for k, v in record["checks"].items() if not v}
        ]
        return record | {
            "previous_passed": record["passed"],
            "previous_answer_review": record["answer_review"],
            "answer_review": review,
            "passed": all(record["checks"].values()) and review["passed"],
            "failures": deterministic_failures + review["failures"],
        }

    completed = []
    with (args.out / "raw.jsonl").open("x") as raw, ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(one, r) for r in records]
        for future in as_completed(futures):
            record = future.result()
            completed.append(record)
            raw.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw.flush()
            summary = summarise(completed) | {
                "raw_sha256": sha256(args.out / "raw.jsonl"),
                "measurement_correction": True,
                "tested_agent_calls_added": 0,
            }
            (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            (args.out / "failures.json").write_text(
                json.dumps(
                    [
                        {"id": r["id"], "trial": r["trial"], "failures": r["failures"]}
                        for r in completed
                        if not r["passed"]
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            print(
                f"{len(completed)}/{len(records)} {record['id']} trial={record['trial']} "
                f"pass={record['passed']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
