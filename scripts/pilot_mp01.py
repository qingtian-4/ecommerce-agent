"""One-off, explicitly authorized live pilot for the mixed order + catalog request.

This is a development probe, not a benchmark.  It records every HTTP request through
the existing EpisodeLedger/RequestCapture path, and never treats the answer as a
quality score.  Run only with fresh user authorization; ``--authorization-reference``
is stored in the private manifest exactly as given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from eval_live_readiness import model_for  # noqa: E402

from acs import multiturn_evaluation as m8  # noqa: E402
from acs.commerce_benchmark_data import file_hash  # noqa: E402
from acs.episode_budget import EpisodeLedger, EpisodeLimits  # noqa: E402
from acs.shop_demo_data import CONSULTATION_SOURCE, materialize  # noqa: E402

QUERY = "我目前都有哪些订单，然后我们这个店都有哪些产品"
TASK_ID = "MP01"
ALLOWED = [
    "get_logistics",
    "get_order",
    "get_price_history",
    "get_product",
    "get_return_requests",
    "get_return_task",
    "list_orders",
    "list_products",
    "search_knowledge",
]
PRICE_REVIEW = ROOT / "results/shop_mainline_20261028/price_review.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task() -> m8.Task:
    return m8.Task(
        task_id=TASK_ID,
        split="development",
        cohort="common",
        goal="同一轮同时取得本人订单候选与完整商品概览",
        fixture_index=0,
        steps=[
            m8.Step(
                text=QUERY,
                expect={"requests": 0},
                allowed_tools=ALLOWED,
                answers=[],
            )
        ],
    )


def fixture_factory(dataset: Path):
    def fixture(path, _task):
        materialize(path, dataset)
        return m8.initial_fingerprint(path)

    return fixture


def receipts(result: dict) -> list[dict]:
    parsed = []
    for row in result.get("final", {}).get("receipts", []):
        try:
            payload = json.loads(row["result"])
        except (KeyError, TypeError, ValueError):
            payload = {}
        parsed.append({"name": row.get("name"), "payload": payload})
    return parsed


def summarize(result: dict, arm: str) -> dict:
    step = result.get("steps", [{}])[0]
    rows = receipts(result)
    orders = next((r for r in rows if r["name"] == "list_orders"), None)
    products = next((r for r in rows if r["name"] == "list_products"), None)
    order_items = (orders or {}).get("payload", {}).get("orders") or []
    product_items = (products or {}).get("payload", {}).get("products") or []
    catalog_complete = bool((products or {}).get("payload", {}).get("complete_catalog"))
    names = [r["name"] for r in (step.get("attempted") or [])]
    return {
        "arm": arm,
        "status": result.get("status"),
        "error": result.get("error"),
        "answer": step.get("raw"),
        "display": step.get("display"),
        "raw_grade": (step.get("raw_grade") or {}).get("status"),
        "tool_names": names,
        "tool_calls": len(names),
        "orders_returned": len(order_items),
        "products_returned": len(product_items),
        "catalog_complete": catalog_complete,
        "goals": {
            "orders": bool(orders and order_items),
            "catalog": bool(products and catalog_complete),
        },
        "mentions_order_count": str(len(order_items)) in (step.get("raw") or ""),
        "mentions_product_count": str(len(product_items)) in (step.get("raw") or ""),
        "model_http_requests": result.get("model_http_requests"),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_ms": result.get("latency_ms"),
        "cost": result.get("cost"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", choices=["old", "v3", "both"], default="both")
    parser.add_argument("--dataset", type=Path, default=CONSULTATION_SOURCE)
    parser.add_argument("--authorization-reference", required=True)
    parser.add_argument("--expires-minutes", type=int, default=120)
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--price-review", type=Path, default=PRICE_REVIEW)
    args = parser.parse_args()

    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite an existing output: " + str(out))
    out.mkdir(parents=True)
    private = out / ".private-evidence"
    private.mkdir()
    arms = ["old", "v3"] if args.arm == "both" else [args.arm]
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    run_id = uuid4().hex
    expires = (datetime.now(UTC) + timedelta(minutes=args.expires_minutes)).isoformat()
    limits = EpisodeLimits(requests=args.requests, tools=24, steps=8, episode_timeout_s=600)
    authorization = {
        "authorized": True,
        "quota_mode": "uncapped",
        "cap_cny": None,
        "max_requests": None,
        "user_instruction_reference": args.authorization_reference,
        "run_id": run_id,
        "source_sha": source_sha,
        "output_dir": str(out),
        "expires_at": expires,
    }
    (private / "authorization.json").write_text(
        json.dumps(authorization, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "mp01_live_development_probe",
                "task_id": TASK_ID,
                "query": QUERY,
                "dataset": str(args.dataset),
                "dataset_manifest_sha256": file_hash(args.dataset / "manifest.json"),
                "source_sha": source_sha,
                "source_dirty": dirty,
                "script_sha256": sha(Path(__file__).resolve()),
                "price_review_sha256": sha(args.price_review),
                "authorization": authorization,
                "arms": arms,
                "limits": limits.__dict__,
                "started_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    ledger = EpisodeLedger(
        private / "fees.jsonl",
        cap_cny=None,
        max_requests=None,
        uncapped_authorization=args.authorization_reference,
    )
    fixture = fixture_factory(args.dataset)
    summary = []
    for arm in arms:
        episode = f"{TASK_ID}:{arm}"
        trial = private / arm
        trial.mkdir()
        ledger.begin(episode, limits)
        print("running", episode, flush=True)
        with (
            patch.object(m8, "build_fixture", fixture),
            model_for(
                out,
                ledger,
                episode,
                auth={"expires_at": expires},
                env_file=ROOT / ".env",
                price_review=args.price_review,
            ) as model,
        ):
            result = m8.run_task(task(), [], trial, arm, model=model, ledger=ledger, limits=limits)
        row = summarize(result, arm)
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
        (private / (arm + "-result.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (out / "scorecard.json").write_text(
        json.dumps(
            {
                "kind": "mp01_live_development_probe",
                "paid_requests": ledger.summary()["requests"],
                "cost": ledger.summary(),
                "arms": summary,
                "note": "One development probe per arm; not a benchmark, not a success rate.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
