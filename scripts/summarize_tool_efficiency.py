"""Re-score the published experiment with one rule set, without API calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.eval_runner import (  # noqa: E402
    CASES_PATH,
    SCORING_VERSION,
    Observed,
    score_case,
    summarise,
)


def metrics(rows, cases):
    summary = summarise(rows, cases)
    summary["tool_calls"] = sum(len(row["tool_sequence"]) for row in rows)
    summary["redundant_calls_total"] = sum(row["redundant_calls"] for row in rows)
    summary["redundant_call_rate"] = round(
        summary["redundant_calls_total"] / summary["tool_calls"], 4
    ) if summary["tool_calls"] else 0.0
    return summary


def main(output: Path):
    base = ROOT / "results" / "tool_efficiency"
    cases = yaml.safe_load(CASES_PATH.read_text())["cases"]
    case_by_id = {case["id"]: case for case in cases}
    groups = {}
    report = {
        "scoring_version": SCORING_VERSION,
        "cases_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "method": "re-score all original outputs; no new model calls or discarded failures",
        "groups": {},
    }
    for name in ("before", "initial", "after"):
        path = base / name / "per_case.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        original = json.loads((path.parent / "summary.json").read_text())
        assert len(rows) == 105
        assert {(r["case_id"], r["attempt"]) for r in rows} == {
            (case["id"], attempt) for case in cases for attempt in (1, 2, 3)
        }
        for row in rows:
            case = case_by_id[row["case_id"]]
            assert row["query"] == case["query"], "query changed; re-run the model"
            observed = Observed(**{
                key: row[key] for key in Observed.__dataclass_fields__ if key in row
            })
            row["score"] = asdict(score_case(case, observed))
        groups[name] = rows
        report["groups"][name] = {
            "source_commit": original["run"]["commit"],
            "original_scoring_version": original["run"]["scoring_version"],
            "source_rows_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "metrics": metrics(rows, cases),
        }

    before = {(r["case_id"], r["attempt"]): r for r in groups["before"]}
    after = {(r["case_id"], r["attempt"]): r for r in groups["after"]}
    assert before.keys() == after.keys()
    paired = [key for key in before if not before[key]["errors"] and not after[key]["errors"]]
    report["paired_without_execution_errors"] = {
        "attempts": len(paired),
        "before": metrics([before[key] for key in paired], cases),
        "after": metrics([after[key] for key in paired], cases),
    }
    if output.exists():
        raise FileExistsError(f"published comparison already exists: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "results/tool_efficiency/comparison.json"
    )
    main(parser.parse_args().out)
