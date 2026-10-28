"""Audit all demo entities and frozen retrieval controls with no model/network calls."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]
from acs.commerce_benchmark import no_network  # noqa: E402
from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.local_return_tools import (  # noqa: E402
    FORMAL_FIXTURE_KIND,
    LocalKnowledge,
    fixture_settings,
)
from acs.shop_demo_data import REVISED_SOURCE, SOURCE, load_dataset, materialize  # noqa: E402


def audit(dataset, root):
    materialize(root, dataset)
    suite = load_dataset(dataset)
    settings = fixture_settings(root, kind=FORMAL_FIXTURE_KIND)
    registry = ToolRegistry(settings, kb=LocalKnowledge(settings))
    data = suite["business"]
    docs = {p.stem: p.read_text() for p in (dataset / "docs").glob("*.md")}
    mismatches, unknown_anc = [], []
    for p in data["products"]:
        actual = registry.db.get_product(p["id"])
        if actual["name"] != p["name"] or actual["current_price"] != p["current_price"]:
            mismatches.append(dict(product_id=p["id"], field="name_or_price"))
        for fact in p["attributes"]:
            if fact not in actual["attributes"] or not any(fact in t for t in docs.values()):
                mismatches.append(dict(product_id=p["id"], field="attribute", fact=fact))
        if p["category"] == "无线耳机" and not any(
            a in p["attributes"] for a in ("主动降噪", "不支持主动降噪")
        ):
            unknown_anc.append(dict(product_id=p["id"],
                sql_values=[s["anc"] for s in actual["skus"]],
                consistent=all(s["anc"] is None for s in actual["skus"])))
    with closing(sqlite3.connect(root / "business.sqlite")) as db:
        missing = [r[0] for r in db.execute("SELECT product_id FROM products EXCEPT "
                                           "SELECT product_id FROM order_items")]
        traces = [json.loads(r[0]) for r in db.execute("SELECT trace_json FROM logistics")]
        invalid_amounts = db.execute("SELECT o.order_id FROM orders o JOIN order_items i "
            "USING(order_id) GROUP BY o.order_id HAVING ABS(o.total_amount-"
            "SUM(i.quantity*i.purchase_price))>0.000001").fetchall()
    if invalid_amounts:
        raise AssertionError("SQL total differs from its order lines")
    signatures = Counter(tuple(e["location"] for e in t) for t in traces)
    report = dict(version=suite["manifest"]["version"], manifest_sha256=file_hash(
        dataset / "manifest.json"), products=len(data["products"]), orders=len(data["orders"]),
        documents=len(docs), categories=dict(Counter(p["category"] for p in data["products"])),
        orders_by_status=dict(Counter(o["status"] for o in data["orders"])),
        products_without_orders=missing, sql_kb_mismatches=mismatches,
        unspecified_earphone_anc=unknown_anc, logistics_rows=len(traces),
        route_shapes=len(signatures), largest_route_shape_count=max(signatures.values()),
        timezone_missing_events=sum("+08:00" not in e["time"] for t in traces for e in t),
        after_review_date_orders=sum(o["created_at"] > "2026-10-28" for o in data["orders"]),
        synthetic_clock=data["reference_date"], paid_requests=0)
    casefile = dataset / "rag_cases.json"
    if casefile.exists():
        results = []
        for case in json.loads(casefile.read_text())["cases"]:
            chunks = registry.kb.search(case["query"])
            matching = [c for c in chunks if c.document_id == case["document_id"]]
            results.append(dict(id=case["id"], document_hit=bool(matching),
                facts_hit=[any(f in c.content for c in matching) for f in case["required_facts"]],
                evidence=[dict(document_id=c.document_id, chunk_id=c.chunk_id) for c in chunks],
                semantic_status=case["semantic_status"]))
        report["rag"] = dict(cases_sha256=file_hash(casefile), cases=results,
            document_hits=sum(r["document_hit"] for r in results),
            facts_hit=sum(sum(r["facts_hit"]) for r in results), total_facts=2*len(results),
            failures=[r["id"] for r in results if not all(r["facts_hit"])])
    return report


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    with no_network():
        reports = [audit(dataset, out / ".private-evidence" / name)
                   for name, dataset in (("old", SOURCE), ("revised", REVISED_SOURCE))]
    dump(out / "report.json", dict(kind="data_and_retrieval_not_model_quality", reports=reports))
    print(json.dumps([{k: v for k, v in r.items() if k not in {"rag", "unspecified_earphone_anc"}}
                      for r in reports], ensure_ascii=False))
    if "rag" in reports[-1]:
        print(json.dumps({k: v for k, v in reports[-1]["rag"].items() if k != "cases"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
