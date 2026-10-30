"""Rescore saved planner predictions with optional-aware exact matching; no model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_intent_planner import _metrics  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scorecard", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite: " + str(out))
    scorecard = json.loads(args.scorecard.read_text(encoding="utf-8"))
    data = json.loads(args.dataset.read_text(encoding="utf-8"))
    optional = {case["id"]: case.get("optional", []) for case in data["cases"]}
    result = {
        "measurement_revision": "optional-aware-exact-v1",
        "source_scorecard": str(args.scorecard.resolve().relative_to(ROOT)),
        "dataset": str(args.dataset.resolve().relative_to(ROOT)),
        "model": scorecard.get("model"),
        "usage": scorecard.get("usage"),
        "challenge": None,
        "challenge_rule": None,
        "validation": None,
        "validation_rule": None,
    }
    for split, rows_key in (("challenge", "challenge_rows"), ("validation", "validation_rows")):
        rows = scorecard.get(rows_key) or []
        for row in rows:
            row["optional"] = optional.get(row["id"], row.get("optional", []))
        if rows:
            result[split] = _metrics(rows, "planner")
            result[f"{split}_rule"] = _metrics(rows, "rule")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "split": "challenge" if result["challenge"] else "validation",
                "strict": result["challenge"] or result["validation"],
                "rule": result["challenge_rule"] or result["validation_rule"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
