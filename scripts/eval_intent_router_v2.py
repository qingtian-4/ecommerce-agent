"""Evaluate the rule router on frozen, gold-tool-derived intent cases; no model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from acs.intent_router import IntentRouter  # noqa: E402

DEFAULT = ROOT / "data/eval/intent_routing_v2/validation.json"


def metrics(cases, router):
    per = defaultdict(lambda: {"support": 0, "predicted": 0, "tp": 0})
    rows = []
    exact = multi_total = multi_exact = 0
    for case in cases:
        predicted = set(router.route(case["query"]).names)
        expected = set(case["expected"])
        ok = predicted == expected
        exact += ok
        if len(expected) > 1:
            multi_total += 1
            multi_exact += ok
        for label in expected:
            per[label]["support"] += 1
        for label in predicted:
            per[label]["predicted"] += 1
        for label in expected & predicted:
            per[label]["tp"] += 1
        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "expected": sorted(expected),
                "predicted": sorted(predicted),
                "exact": ok,
                "source": case.get("source"),
            }
        )
    per_route = {}
    for label, value in sorted(per.items()):
        tp, support, predicted = value["tp"], value["support"], value["predicted"]
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        per_route[label] = {
            "support": support,
            "predicted": predicted,
            "tp": tp,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4)
            if precision + recall
            else 0.0,
        }
    return {
        "cases": len(cases),
        "exact_match": exact,
        "accuracy": round(exact / len(cases), 4) if cases else 0.0,
        "multi_intent_cases": multi_total,
        "multi_intent_exact": multi_exact,
        "per_route": per_route,
        "failures": [row for row in rows if not row["exact"]],
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite: " + str(out))
    doc = json.loads(args.cases.read_text(encoding="utf-8"))
    result = metrics(doc["cases"], IntentRouter())
    result.update(
        kind="intent_routing_rule_validation",
        dataset=str(args.cases.relative_to(ROOT)),
        dataset_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        source_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        paid_requests=0,
        note=(
            "Frozen existing task texts; expected routes derived from gold tool families, "
            "not from router cues. Rule labels only; no model or answer-quality evaluation."
        ),
    )
    out.mkdir(parents=True)
    (out / "scorecard.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": result["cases"],
                "exact_match": result["exact_match"],
                "accuracy": result["accuracy"],
                "multi": [result["multi_intent_exact"], result["multi_intent_cases"]],
                "per_route": result["per_route"],
                "failures": len(result["failures"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
