"""Real-model intent-routing baseline on a stratified frozen subset; records usage/cost."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.config import Settings  # noqa: E402
from acs.intent_router import IntentRouter  # noqa: E402
from acs.llm import LLMClient  # noqa: E402

LABELS = ["orders", "logistics", "product", "policy", "return", "price"]
SYSTEM = (
    '你是电商客服意图分类器。只输出 JSON，格式为 {"routes": [...]}。'
    "允许标签只有 orders、logistics、product、policy、return、price。"
    "多意图要列出全部；无法判断返回空数组；不要解释，不要输出其他字段。"
)


def parse(text: str) -> list[str]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return []
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return []
    routes = value.get("routes") if isinstance(value, dict) else None
    if not isinstance(routes, list):
        return []
    return [r for r in routes if r in LABELS]


def metrics(rows: list[dict]) -> dict:
    exact = sum(set(r["expected"]) == set(r["predicted"]) for r in rows)
    coverage = sum(set(r["expected"]) <= set(r["predicted"]) for r in rows)
    extra = sum(len(set(r["predicted"]) - set(r["expected"])) for r in rows)
    missing = sum(len(set(r["expected"]) - set(r["predicted"])) for r in rows)
    return {
        "cases": len(rows),
        "exact_match": exact,
        "accuracy": round(exact / len(rows), 4) if rows else 0.0,
        "coverage": coverage,
        "coverage_rate": round(coverage / len(rows), 4) if rows else 0.0,
        "extra_labels": extra,
        "missing_labels": missing,
        "failures": sum(set(r["expected"]) != set(r["predicted"]) for r in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite: " + str(out))
    doc = json.loads((ROOT / "data/eval/intent_routing_v2/validation.json").read_text())
    cases = list(doc["cases"])
    rng = random.Random(args.seed)
    rng.shuffle(cases)
    selected = cases[: args.limit]
    settings = Settings(_env_file=ROOT / ".env")
    client = LLMClient(settings=settings)
    rows = []
    for index, case in enumerate(selected, 1):
        response = client.complete(
            case["query"],
            system=SYSTEM,
            response_format_json=True,
            temperature=0,
            max_tokens=120,
            stage="intent_routing_eval",
        )
        predicted = parse(response.text)
        rule = IntentRouter().route(case["query"]).names
        rows.append(
            {
                "id": case["id"],
                "query": case["query"],
                "expected": case["expected"],
                "model": sorted(set(predicted)),
                "rule": sorted(set(rule)),
                "model_exact": set(predicted) == set(case["expected"]),
                "rule_exact": set(rule) == set(case["expected"]),
                "model_raw": response.text[:500],
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cost_cny": str(response.usage.cost_cny),
            }
        )
        print(
            f"{index}/{len(selected)} {case['id']} "
            f"model={sorted(set(predicted))} expected={case['expected']}",
            flush=True,
        )
    report = {
        "kind": "intent_routing_model_baseline",
        "dataset": "data/eval/intent_routing_v2/validation.json",
        "dataset_sha256": hashlib.sha256(
            (ROOT / "data/eval/intent_routing_v2/validation.json").read_bytes()
        ).hexdigest(),
        "source_sha": __import__("subprocess")
        .check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
        .strip(),
        "model": settings.llm_model,
        "temperature": 0,
        "sample_seed": args.seed,
        "sample_size": len(selected),
        "model_metrics": metrics(
            [{"expected": r["expected"], "predicted": r["model"]} for r in rows]
        ),
        "rule_metrics_same_sample": metrics(
            [{"expected": r["expected"], "predicted": r["rule"]} for r in rows]
        ),
        "usage": client.totals(),
        "rows": rows,
        "note": "One classification call per query; not the product tool-routing path.",
    }
    out.mkdir(parents=True)
    (out / "scorecard.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("sample_size", "model_metrics", "rule_metrics_same_sample", "usage")
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
