"""Build a held-out intent-routing validation set from existing frozen tasks.

Intent labels are derived from the gold tool families, not from the router's keyword
list.  This does not make the set human-labelled or independent of the benchmark
authors; it is a frozen, previously unseen-by-router evaluation set.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path("/home/qingtian/projects/E-commerce Agent")
OUT = ROOT / "data/eval/intent_routing_v2"
SOURCES = [
    ("shop_mainline", ROOT / "data/eval/shop_mainline_v1_1/cases.json"),
    ("commerce_complex", ROOT / "data/eval/commerce_complex_v1/cases.json"),
    ("commerce_benchmark", ROOT / "data/eval/commerce_benchmark_v2_2/agent_tasks.json"),
]
ORDER = ["orders", "logistics", "product", "policy", "return", "price"]
TOOL_ROUTE = {
    "list_orders": "orders",
    "get_order": "orders",
    "get_logistics": "logistics",
    "list_products": "product",
    "get_product": "product",
    "get_price_history": "price",
    "search_knowledge": "policy",
    "get_knowledge_documents": "policy",
    "get_return_task": "return",
    "update_return_task": "return",
    "cancel_return_task": "return",
    "preview_return": "return",
    "get_return_requests": "return",
}


def main():
    cases = []
    source_hashes = {}
    for name, path in SOURCES:
        source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        entries = json.loads(path.read_text(encoding="utf-8"))
        for entry in entries:
            task = entry["task"]
            gold = entry.get("tool_gold") or []
            for index, step in enumerate(task["steps"]):
                if step.get("action", "chat") != "chat" or not step.get("text", "").strip():
                    continue
                if index >= len(gold):
                    continue
                names = [item["name"] for item in gold[index] if not item.get("error")]
                expected = sorted(
                    {TOOL_ROUTE[n] for n in names if n in TOOL_ROUTE}, key=ORDER.index
                )
                cases.append(
                    {
                        "id": f"{name}:{task['task_id']}:{index}",
                        "query": step["text"],
                        "expected": expected,
                        "source": name,
                        "task_id": task["task_id"],
                        "step": index,
                        "gold_tools": sorted(set(names)),
                    }
                )
    route_counts = {}
    for case in cases:
        for route in case["expected"]:
            route_counts[route] = route_counts.get(route, 0) + 1
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "intent-routing-v2-validation",
        "frozen_at": "2026-10-30",
        "note": (
            "Frozen existing task texts; expected routes derived from gold tool families "
            "in the source data, not from router cues. Not human-labelled, not a model result."
        ),
        "route_order": ORDER,
        "cases": cases,
        "source_sha256": source_hashes,
        "route_counts": route_counts,
        "multi_intent_cases": sum(len(c["expected"]) > 1 for c in cases),
        "unknown_cases": sum(not c["expected"] for c in cases),
    }
    (OUT / "validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "manifest.json").write_text(
        json.dumps(
            {
                "version": payload["version"],
                "frozen_at": payload["frozen_at"],
                "cases": len(cases),
                "route_counts": route_counts,
                "multi_intent_cases": payload["multi_intent_cases"],
                "unknown_cases": payload["unknown_cases"],
                "source_sha256": source_hashes,
                "validation_sha256": hashlib.sha256(
                    (OUT / "validation.json").read_bytes()
                ).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                k: payload[k]
                for k in ("cases", "route_counts", "multi_intent_cases", "unknown_cases")
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
