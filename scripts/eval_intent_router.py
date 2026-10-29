"""Score the bounded rule router on a frozen, authored regression set.

No model calls.  This measures route-label consistency and multi-label exact match;
it does not measure whether the model then chooses the right tool or answers well.
"""

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

DEFAULT = ROOT / "data/eval/intent_routing_v1/cases.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score(cases: list[dict]) -> dict:
    router = IntentRouter()
    rows = []
    per = defaultdict(lambda: {"support": 0, "predicted": 0, "tp": 0})
    exact = 0
    multi_total = 0
    multi_exact = 0
    for case in cases:
        decision = router.route(case["query"])
        predicted = set(decision.names)
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
                "tools": sorted(decision.tools) if decision.tools is not None else "all",
            }
        )
    metrics = {}
    for label, value in sorted(per.items()):
        tp, support, predicted = value["tp"], value["support"], value["predicted"]
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics[label] = {
            "support": support,
            "predicted": predicted,
            "tp": tp,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    return {
        "cases": len(cases),
        "exact_match": exact,
        "accuracy": round(exact / len(cases), 4) if cases else 0.0,
        "multi_intent_cases": multi_total,
        "multi_intent_exact": multi_exact,
        "per_route": metrics,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite: " + str(out))
    cases_doc = json.loads(args.cases.read_text(encoding="utf-8"))
    report = score(cases_doc["cases"])
    report.update(
        kind="intent_routing_regression",
        dataset=str(args.cases.relative_to(ROOT)),
        dataset_sha256=sha(args.cases),
        source_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        paid_requests=0,
        note=(
            "Authored frozen regression set, not unseen data. Measures route labels only; "
            "does not measure model tool choice or answer quality."
        ),
    )
    out.mkdir(parents=True)
    (out / "scorecard.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": report["cases"],
                "exact_match": report["exact_match"],
                "accuracy": report["accuracy"],
                "multi_intent": [report["multi_intent_exact"], report["multi_intent_cases"]],
                "per_route": report["per_route"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
