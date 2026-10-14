"""The visible comparison must be tied to the same facts and unmodified raw results."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from acs.eval_view import catalog_knowledge_rows


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


def report_fixture(root, settings):
    base = root / "results/catalog_knowledge"
    source = root / "data/eval/catalog_knowledge_cases.json"
    write(source, {"cases": [{"id": "example"}]})
    doc = root / "data/docs/policy.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("来源正文")
    with sqlite3.connect(settings.db_path) as conn:
        facts = {t: hashlib.sha256(json.dumps(conn.execute(
            f"SELECT * FROM {t} ORDER BY 1",
        ).fetchall(), ensure_ascii=False, sort_keys=True).encode()).hexdigest() for t in (
            "users", "products", "orders", "order_items", "logistics", "price_history",
        )}
    manifest = {"cases_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "knowledge_fingerprints": {doc.name: hashlib.sha256(doc.read_bytes()).hexdigest()},
                "business_fingerprints": facts}
    write(base / "frozen_manifest.json", manifest)
    reviews = {"attempts": {}}
    for mode in ("baseline", "retrieval", "full_context", "documents"):
        path = base / mode / "per_case.jsonl"
        write(path, {"id": "example", "automatic_checks": {"completed": True}, "turns": [{
            "handler_wall_ms": 15, "result": {"tool_calls": 1,
                                             "totals": {"calls": 2, "total_tokens": 20,
                                                        "cost_cny": "0.001"}},
        }]})
        write(base / mode / "summary.json", {
            "cases_sha256": manifest["cases_sha256"],
            "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "cases": 1, "turns": 1,
            "model": "fake", "temperature": 0, "thinking": False, "retrieval_mode": "bm25",
            "reranker_enabled": False, "evidence_backfill": False, "clock": "2026-10-15",
        })
        reviews["attempts"][mode] = {"raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "cases": {"example": {"passed": True}}}
    write(base / "manual_review.json", reviews)
    return base


@pytest.mark.parametrize("change", [None, "raw", "knowledge", "conditions", "business", "review"])
def test_report_recomputes_and_rejects_incomparable_inputs(settings, tmp_path, change):
    base = report_fixture(tmp_path, settings)
    rows = catalog_knowledge_rows(tmp_path, settings.db_path)
    assert len(rows) == 4 and rows[0][1:] == [1, 1, 1, 1, 1, 2, 20, 15, "0.001"]
    if change is None:
        return
    if change == "raw":
        (base / "full_context/per_case.jsonl").write_text("{}\n")
    elif change == "knowledge":
        (tmp_path / "data/docs/policy.md").write_text("已改变正文")
    elif change == "conditions":
        path = base / "documents/summary.json"
        summary = json.loads(path.read_text())
        summary["temperature"] = 0.7
        write(path, summary)
    elif change == "review":
        path = base / "manual_review.json"
        reviews = json.loads(path.read_text())
        reviews["attempts"]["documents"]["raw_sha256"] = "different"
        write(path, reviews)
    else:
        with sqlite3.connect(settings.db_path) as conn:
            conn.execute("UPDATE products SET price=123 WHERE product_id='P001'")
    with pytest.raises(ValueError):
        catalog_knowledge_rows(tmp_path, settings.db_path)
