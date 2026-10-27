"""Read-only export of the authorized pilot. Never calls a model or resumes an arm."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from acs import multiturn_evaluation as m8  # noqa: E402
from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.config import is_peak_now  # noqa: E402

RUN = HERE / ".private-evidence/run1"
PRIVATE = RUN / ".private-evidence"
SOURCE_TABLES = ["users", "products", "orders", "order_items", "logistics", "price_history",
                 "product_skus", "order_item_skus"]
FORMAL_TABLES = ["return_previews", "return_requests", "return_confirmations", "return_audit",
                 "return_reviews", "local_submission_receipts"]


def audit(out):
    out.mkdir(parents=True, exist_ok=False)
    ledger = [json.loads(line) for line in (PRIVATE / "fees.jsonl").read_text().splitlines()]
    episodes = json.loads((PRIVATE / "episodes.json").read_text())
    manifest = json.loads((RUN / "manifest.json").read_text())
    reserved = {r["request_id"]: r for r in ledger if r["type"] == "reserved"}
    ends = [r for r in ledger if r["type"] == "request_end"]
    assert len(episodes) == 4 and len(reserved) == len(ends) == 18
    assert not [r for r in ledger if r["type"] in {"stop", "experiment_stop", "preflight_rejected"}]
    assert {r["request_id"] for r in ends} == set(reserved)
    assert all(r["status"] == "completed" and r["timeband"] == "offpeak" for r in ends)
    bodies, models = {}, []
    for path in sorted((PRIVATE / "raw").glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("kind") == "authorized_private_model_request":
            body = json.dumps(record["body"], ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode()
            item = reserved[record["request_id"]]
            assert hashlib.sha256(body).hexdigest() == item["body_sha256"]
            assert 2 * len(body) + 4096 == item["input_token_bound"]
            assert len(body) <= 32768
            assert record["body"]["thinking"]["type"] == "disabled"
            assert record["body"]["temperature"] == 0
            bodies[record["request_id"]] = len(body)
        if "sdk_result" in record:
            models.append(record["sdk_result"]["llm_output"]["model_name"])
    assert set(bodies) == set(reserved) and set(models) == {"deepseek-flash"}
    accounting = []
    for end in ends:
        request = reserved[end["request_id"]]
        assert not is_peak_now(datetime.fromtimestamp(request["time"], UTC))
        usage = end["usage"]
        cost = (Decimal(usage["prompt_tokens"] - end["cached_tokens"])
                + Decimal(end["cached_tokens"]) * Decimal("0.02")
                + Decimal(usage["completion_tokens"]) * 4) / 1_000_000
        assert cost == Decimal(end["timeband_estimated_cny"])
        accounting.append(dict(
            request_id=end["request_id"], episode=end["episode"], status=end["status"],
            started_at_beijing=datetime.fromtimestamp(request["time"], UTC).astimezone(
                ZoneInfo("Asia/Shanghai")).isoformat(),
            body_bytes=bodies[end["request_id"]], body_sha256=request["body_sha256"],
            usage=usage, cached_tokens=end["cached_tokens"], latency_ms=end["latency_ms"],
            reserved_cny=request["reserved_cny"], peak_estimate_cny=end["estimated_cny"],
            offpeak_estimate_cny=str(cost), actual_invoice_read=False))
    dump(out / "request_accounting.json", accounting)

    reconciled, public_episodes, review = [], [], []
    for row in episodes:
        assert row["sql_correct"] and row["tool_valid"] and not row["error"]
        assert row["raw_outcome"] == row["display_outcome"] == "pending_review"
        root = PRIVATE / (row["task_id"] + "-" + row["arm"])
        before = file_hash(root / "business.sqlite")
        with closing(sqlite3.connect((root / "business.sqlite").as_uri() + "?mode=ro",
                                      uri=True)) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                      if t in tables else 0 for t in FORMAL_TABLES}
            assert not any(counts.values())
            source = {t: conn.execute("SELECT * FROM " + t + " ORDER BY 1").fetchall()
                      for t in SOURCE_TABLES}
            docs = {p.name: file_hash(p) for p in sorted((root / "docs").glob("*.md"))}
            assert m8.digest(dict(sql=source, docs=docs)) == row["initial_fingerprint"]
            assert conn.execute("SELECT DISTINCT user_id FROM conversations").fetchall() == [
                ("USER001",)]
            assert {r[0] for r in conn.execute("SELECT status FROM conversation_turns")} == {
                "completed"}
            tasks = conn.execute("SELECT data_json FROM return_tasks").fetchall()
            assert len(tasks) == (1 if row["task_id"] == "CX04" else 0)
            if tasks:
                actual = json.loads(tasks[0][0])
                for key in ("order_id", "item_id", "quantity", "reason", "condition",
                            "secondary_sale_ok", "revision", "status"):
                    assert actual.get(key) == row["final"]["draft"].get(key)
            columns = ["call_id", "name", "arguments", "result", "error"]
            actual_receipts = [dict(zip(columns, r, strict=True)) for r in conn.execute(
                "SELECT call_id,name,arguments,result,error FROM local_receipts ORDER BY rowid")]
            assert actual_receipts == row["final"]["receipts"]
            lines = conn.execute("SELECT id,product_id,quantity FROM order_items WHERE order_id=?",
                                 ("O72001",)).fetchall()
            assert len(lines) == 2 and sum(i[2] for i in lines) == 3
            sku = conn.execute("SELECT sku_id,anc,color,availability FROM product_skus "
                               "WHERE product_id='B501' ORDER BY sku_id").fetchall()
            assert all(s[1] == 1 and s[3] == "available" for s in sku)
            prices = conn.execute("SELECT price,changed_at FROM price_history WHERE product_id=? "
                                  "ORDER BY changed_at DESC,id DESC", ("B501",)).fetchall()
            assert prices[0] == (211.0, "2026-11-05") and prices[1][0] == 231.0
        assert before == file_hash(root / "business.sqlite")
        writes = [r for r in row["final"]["receipts"] if r["name"] == "update_return_task"]
        if row["task_id"] == "CX04":
            assert len(writes) == 2
            first = json.loads(writes[0]["result"])["task"]
            last = row["final"]["draft"]
            assert first["order_id"] == "O72001" and first["item_id"] is None
            assert first["quantity"] is None
            assert all(first.get(k) is None for k in ("reason", "condition", "secondary_sale_ok"))
            assert last["order_id"] == "O72001" and last["item_id"] == 720010
            assert last["quantity"] == 1 and last["status"] == "draft"
            assert last["reason"] == "no_reason" and last["condition"] == "sealed"
            assert last["secondary_sale_ok"] is True
        else:
            assert not writes and row["final"]["draft"] is None
        assert all(g["evidence"] for g in row["objectives"])
        reconciled.append(dict(task_id=row["task_id"], arm=row["arm"], read_only_sql=True,
                               source_unchanged=True, database_sha256=before,
                               formal_counts=counts, draft_write_receipts=len(writes),
                               owner="USER001", order_lines=lines, ordered_units=3,
                               B501_skus=sku, B501_price_history=prices,
                               graph_used_as_success_proof=False))
        calls = []
        for step in row["steps"]:
            assert step["raw"] == step["display"]
            calls.append(dict(step=step["step"], calls=[dict(name=c["name"], args=c["args"])
                                                       for c in step["attempted"]]))
            caveats = []
            if row["task_id"] == "CX04" and step["step"] == 0:
                assert "两件商品" in step["raw"]
                caveats.append(dict(kind="count_wording_requires_review", phrase="两件商品",
                                    evidence="SQL has 2 line items and 3 ordered units",
                                    reason=("May be colloquial line-item wording; "
                                            "do not keyword-grade")))
                if row["arm"] == "old":
                    assert "均现货" in step["raw"]
                    caveats.append(dict(kind="availability_wording_requires_review",
                                        phrase="均现货",
                                        evidence=(
                                            "SKU availability is available; no inventory count "
                                            "or stock-versus-preorder field"),
                                        reason="Wording may be stronger than the returned facts"))
            review.append(dict(task_id=row["task_id"], arm=row["arm"], step=step["step"],
                               reviewer="coding_assistant_source_audit_not_independent",
                               primary_facts_checked=(
                                   "Product IDs/specs/prices, policy, draft state "
                                   "and no-submission statements align with receipts"),
                               caveats=caveats, semantic_status="pending_independent_review",
                               independent_review_completed=False))
        public_episodes.append(dict(
            task_id=row["task_id"], arm=row["arm"], model_http_requests=row["model_http_requests"],
            tool_calls=row["tool_calls"], latency_ms=row["latency_ms"], cost=row["cost"],
            sql_correct=True, tool_valid=True, objective_evidence=len(row["objectives"]),
            raw_outcome=row["raw_outcome"], display_outcome=row["display_outcome"],
            raw_display_changes=0, calls=calls))
    dump(out / "sql_reconciliation.json", reconciled)
    dump(out / "episode_accounting.json", public_episodes)
    dump(out / "author_review.json", review)
    shutil.copyfile(RUN / "scorecard.json", out / "frozen_scorecard.json")
    report = dict(
        source_sha=manifest["source_sha"], contract=manifest["dataset"]["version"],
        logical_business_clock=manifest["dataset"]["clock"],
        source_fingerprints=manifest["source_fingerprints"],
        live_manifest_sha256=file_hash(RUN / "manifest.json"),
        frozen_scorecard_sha256=file_hash(RUN / "scorecard.json"),
        ledger_sha256=file_hash(PRIVATE / "fees.jsonl"),
        completed_episodes=4, actual_model_http=18, all_offpeak=True, all_usage_complete=True,
        prompt_tokens=sum(r["usage"]["prompt_tokens"] for r in ends),
        completion_tokens=sum(r["usage"]["completion_tokens"] for r in ends),
        cached_tokens=sum(r["cached_tokens"] for r in ends),
        conservative_reserved_cny=str(sum(Decimal(r["reserved_cny"]) for r in reserved.values())),
        peak_usage_estimate_cny=str(sum(Decimal(r["estimated_cny"]) for r in ends)),
        offpeak_usage_estimate_cny=str(sum(Decimal(r["timeband_estimated_cny"]) for r in ends)),
        actual_invoice_read=False, max_body_bytes=max(bodies.values()), body_limit=32768,
        sql_correct=4, tool_valid=4, objective_evidence=20, semantic_episodes_pending=4,
        semantic_queue_items=16, unique_answer_turns=8, independent_review_completed=False,
        observed_models=dict(Counter(models)), new_judge_user_embedding_rerank_http=0,
        inference_retried=False, runtime_modified=False, formal_requests_created=0,
        private_evidence=".private-evidence/run1/.private-evidence",
    )
    dump(out / "summary.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    result = audit(parser.parse_args().out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
