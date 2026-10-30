"""Evaluate the RAG-enhanced planner against rule routing and the semantic challenge set."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.config import Settings  # noqa: E402
from acs.intent_planner import IntentPlanner  # noqa: E402
from acs.intent_router import IntentRouter  # noqa: E402
from acs.llm import LLMClient  # noqa: E402

VALIDATION = ROOT / "data/eval/intent_routing_v2/validation.json"
CHALLENGE = ROOT / "data/eval/intent_semantic_v1/challenge.json"


def _metrics(rows, key):
    per = defaultdict(lambda: {"support": 0, "predicted": 0, "tp": 0})
    exact = coverage = multi_total = multi_exact = multi_cover = 0
    write_false = clarify_false = 0
    for row in rows:
        expected = set(row["required"])
        predicted = set(row[key])
        exact += predicted == expected
        coverage += expected <= predicted
        if len(expected) > 1:
            multi_total += 1
            multi_exact += predicted == expected
            multi_cover += expected <= predicted
        for label in expected:
            per[label]["support"] += 1
        for label in predicted:
            per[label]["predicted"] += 1
        for label in expected & predicted:
            per[label]["tp"] += 1
        if (
            "return" in predicted
            and "return" not in expected
            and not row.get("planned_clarify")
        ):
            write_false += 1
        if row.get("clarify") is False and row.get("planned_clarify"):
            clarify_false += 1
    per_route = {}
    for label, value in sorted(per.items()):
        tp, support, predicted = value["tp"], value["support"], value["predicted"]
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        per_route[label] = {
            "support": support,
            "predicted": predicted,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4)
            if precision + recall
            else 0.0,
        }
    total = len(rows)
    return {
        "cases": total,
        "exact_match": exact,
        "exact_rate": round(exact / total, 4) if total else 0.0,
        "coverage": coverage,
        "coverage_rate": round(coverage / total, 4) if total else 0.0,
        "missing_labels": sum(len(set(r["required"]) - set(r[key])) for r in rows),
        "extra_labels": sum(len(set(r[key]) - set(r["required"])) for r in rows),
        "multi_intent_cases": multi_total,
        "multi_intent_exact": multi_exact,
        "multi_intent_coverage": multi_cover,
        "write_false_positive": write_false,
        "unnecessary_clarification": clarify_false,
        "per_route": per_route,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--challenge-path", type=Path, default=CHALLENGE)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite: " + str(out))

    validation = json.loads(VALIDATION.read_text(encoding="utf-8"))["cases"]
    challenge = json.loads(args.challenge_path.read_text(encoding="utf-8"))["cases"]
    if args.skip_validation:
        validation = []
    if args.limit:
        validation = validation[: args.limit]
        challenge = challenge[: min(args.limit, len(challenge))]

    settings = Settings(_env_file=ROOT / ".env")
    client = LLMClient(settings=settings)
    planner = IntentPlanner(client)
    router = IntentRouter()
    rows = []
    history_by_task: dict[tuple[str, str], list[str]] = {}
    for index, case in enumerate(validation, 1):
        key = (case.get("source", ""), case.get("task_id", ""))
        history = list(history_by_task.get(key, []))[-4:]
        plan = planner.plan(case["query"], history=history)
        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "history": history,
                "required": case["expected"],
                "rule": router.route(case["query"]).names,
                "planner": plan.names,
                "planned_confidences": [goal.confidence for goal in plan.goals],
                "planned_clarify": plan.needs_clarification,
                "clarify": False,
                "source": plan.source,
                "retrieved": plan.retrieved,
            }
        )
        history_by_task.setdefault(key, []).append(case["query"])
        print(
            f"validation {index}/{len(validation)} {case['id']} "
            f"planner={plan.names} required={case['expected']}",
            flush=True,
        )
    challenge_rows = []
    for index, case in enumerate(challenge, 1):
        plan = planner.plan(case["query"])
        challenge_rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "history": [],
                "required": case["required"],
                "rule": router.route(case["query"]).names,
                "planner": plan.names,
                "planned_confidences": [goal.confidence for goal in plan.goals],
                "planned_clarify": plan.needs_clarification,
                "clarify": case["clarify"],
                "source": plan.source,
                "retrieved": plan.retrieved,
            }
        )
        print(
            f"challenge {index}/{len(challenge)} {case['id']} "
            f"planner={plan.names} required={case['required']}",
            flush=True,
        )

    summary = {
        "kind": "intent_planner_evaluation",
        "validation": _metrics(rows, "planner"),
        "validation_rule": _metrics(rows, "rule"),
        "challenge": _metrics(challenge_rows, "planner"),
        "challenge_rule": _metrics(challenge_rows, "rule"),
        "clarification_accuracy": round(
            sum(bool(row["planned_clarify"]) == bool(row["clarify"]) for row in challenge_rows)
            / len(challenge_rows),
            4,
        )
        if challenge_rows
        else 0.0,
        "usage": client.totals(),
        "model": settings.llm_model,
    }
    out.mkdir(parents=True)
    (out / "scorecard.json").write_text(
        json.dumps(
            {
                **summary,
                "validation_rows": rows,
                "challenge_rows": challenge_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps({k: v for k, v in summary.items() if k != "usage"}, ensure_ascii=False, indent=2)
    )
    print(json.dumps({"usage": summary["usage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
