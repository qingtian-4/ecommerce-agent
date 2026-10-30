"""Deterministic memory MVP evaluation: recall, isolation, expiry, PII; no model/judge."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from acs.db_seed import build
from acs.memory import MemoryStore, extract_facts
from acs.sessions import SessionStore

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data/eval/memory_v1/cases.json"


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    rows = []
    with tempfile.TemporaryDirectory(prefix="memory-eval-") as tmp:
        db_path = Path(tmp) / "business.sqlite"
        build(db_path)
        SessionStore(db_path)
        store = MemoryStore(db_path)
        for index, case in enumerate(cases, 1):
            kind = case["kind"]
            extracted = extract_facts(case["user_text"])
            if kind == "fact":
                for fact_kind, text in extracted:
                    store.add_fact(
                        "USER001",
                        text,
                        kind=fact_kind,
                        source_session_id="eval-a",
                        source_turn_id=f"t{index}",
                    )
            elif kind == "expired":
                for fact_kind, text in extracted:
                    store.add_fact("USER001", text, kind=fact_kind, ttl_days=-1)
            elif kind == "cross_user":
                store.add_fact("USER002", "USER002的秘密")
            elif kind == "episode":
                store.add_episode(
                    "USER001",
                    "eval-a",
                    f"t{index}",
                    "用户查询订单状态",
                    outcome={"tools": ["get_order"]},
                )
            context = store.context("USER001", case["query"])
            hit = [text for text in case["expected"] if text in context]
            forbidden = [text for text in case["forbidden"] if text in context]
            rows.append(
                {
                    "id": case["id"],
                    "kind": kind,
                    "query": case["query"],
                    "expected": case["expected"],
                    "forbidden": case["forbidden"],
                    "hit": hit,
                    "forbidden_hits": forbidden,
                    "fact_extracted": len(extracted),
                    "context": context,
                }
            )
            print(f"{index}/{len(cases)} {case['id']} hit={len(hit)}/{len(case['expected'])}")
    all_expected = sum(len(row["expected"]) for row in rows)
    all_hits = sum(len(row["hit"]) for row in rows)
    result = {
        "kind": "memory_eval",
        "cases": len(rows),
        "expected_hit": all_hits,
        "expected_total": all_expected,
        "recall_rate": round(all_hits / all_expected, 4) if all_expected else 1.0,
        "forbidden_hits": sum(len(row["forbidden_hits"]) for row in rows),
        "rows_with_forbidden": sum(bool(row["forbidden_hits"]) for row in rows),
        "fact_extraction_failures": sum(
            row["kind"] == "fact" and row["fact_extracted"] == 0 for row in rows
        ),
        "model_calls": 0,
        "llm_judge_calls": 0,
        "rows": rows,
    }
    out = ROOT / "results/memory_eval_20261030"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scorecard.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
