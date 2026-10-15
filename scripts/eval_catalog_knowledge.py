"""Run frozen development conversations on the real application chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def fingerprint(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as conn:
        return {t: hashlib.sha256(json.dumps(conn.execute(
            f"SELECT * FROM {t} ORDER BY 1",
        ).fetchall(), ensure_ascii=False, sort_keys=True).encode()).hexdigest() for t in (
            "users", "products", "orders", "order_items", "logistics", "price_history",
        )}


def automatic_checks(case: dict, turns: list[dict], counts: dict) -> dict:
    answers = [t["result"]["answer"] for t in turns]
    text = "\n".join(answers)
    return {
        "completed": all(t["result"]["stop_reason"] == "answered" for t in turns),
        "no_application_writes": all(n == 0 for n in counts.values()),
        "required_terms": all(s in answers[-1] for s in case.get("last_contains", [])),
        "answer_groups": all(any(s in text for s in group)
                             for group in case.get("answer_contains_any", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--knowledge-mode", choices=["retrieval", "full_context", "documents"],
                        default="retrieval")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    frozen = json.loads(Path("results/catalog_knowledge/frozen_manifest.json").read_text())
    path = Path("data/eval/catalog_knowledge_cases.json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == frozen["cases_sha256"]
    cases = json.loads(path.read_text())
    assert fingerprint(app.SETTINGS.db_path) == frozen["business_fingerprints"]
    for name, expected in frozen["knowledge_fingerprints"].items():
        assert hashlib.sha256((app.SETTINGS.docs_dir / name).read_bytes()).hexdigest() == expected
    original = app.SETTINGS
    records = []
    raw_path = args.out / "per_case.jsonl"
    # Capture before running; later source edits cannot become this attempt's identity.
    source_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        Path("app.py"), *sorted(Path("src/acs").glob("*.py")), Path(__file__),
    )}
    with raw_path.open("x") as raw, TemporaryDirectory(prefix="acs-catalog-eval-") as temp:
        try:
            for case in cases["cases"]:
                db_path = Path(temp) / (case["id"] + ".db")
                with closing(sqlite3.connect(original.db_path)) as src, closing(
                    sqlite3.connect(db_path),
                ) as dst:
                    src.backup(dst)
                    for table in ("return_reviews", "return_audit", "return_requests",
                                  "return_confirmations",
                                  "return_previews", "return_evidence", "return_tasks",
                                  "conversation_turns", "conversations"):
                        dst.execute(f"DELETE FROM {table}")
                    dst.commit()
                app.SETTINGS = original.model_copy(update={
                    "db_path": db_path, "reference_date": date.fromisoformat(cases["clock"]),
                    "knowledge_mode": args.knowledge_mode,
                })
                state, turns = {}, []
                for query in case["turns"]:
                    started, first_text_ms = perf_counter(), None
                    for update in app.on_send(query, [], state, "USER001"):
                        if (first_text_ms is None and update[0]
                                and update[0][-1]["role"] == "assistant"
                                and update[0][-1]["content"]):
                            first_text_ms = (perf_counter() - started) * 1000
                    wall_ms = (perf_counter() - started) * 1000
                    result = app.session_store().load(state["session_id"], "USER001")["last_result"]
                    turns.append({"query": query, "result": result,
                                  "handler_wall_ms": wall_ms, "first_text_ms": first_text_ms})
                with closing(sqlite3.connect(db_path)) as conn:
                    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                              for t in ("return_requests", "return_confirmations", "return_audit")}
                checks = automatic_checks(case, turns, counts)
                record = {"id": case["id"], "criteria": case["checks"], "turns": turns,
                          "counts": counts, "automatic_checks": checks,
                          "manual_review": "pending"}
                records.append(record)
                raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                raw.flush()
                print(case["id"], checks, flush=True)
        finally:
            app.SETTINGS = original
    summary = {
        "cases_sha256": frozen["cases_sha256"],
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "knowledge_mode": args.knowledge_mode, "clock": cases["clock"],
        "model": original.llm_model, "temperature": original.llm_temperature,
        "thinking": original.llm_thinking, "retrieval_mode": original.retrieval_mode,
        "reranker_enabled": original.reranker_enabled,
        "evidence_backfill": original.evidence_backfill,
        "cases": len(records), "turns": sum(len(r["turns"]) for r in records),
        "automatic_checks_passed": sum(all(r["automatic_checks"].values()) for r in records),
        "implementation_sha256": source_hashes,
        "boundary": "单遍开发场景；关键词与状态检查不是完整回答正确率，须另读答案与证据",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
