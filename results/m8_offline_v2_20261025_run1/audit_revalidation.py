"""Read-only v2 integrity audit, separate from the frozen task scorer."""

import json
import sqlite3
from pathlib import Path

from acs.answer_grounding import Evidence
from acs.multiturn_evaluation import NOW, business_clock, load_suite


def audit(root):
    load_suite(Path(__file__).resolve().parents[2] / "data/eval/v3_multiturn_m8_v2")
    manifest = json.loads((root / "manifest.json").read_text())
    score = json.loads((root / "scorecard.json").read_text())
    bad_money = []
    for path in (root / ".private-evidence").glob("*/business.sqlite"):
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            rows = conn.execute("""SELECT o.order_id
                FROM orders o JOIN order_items i USING(order_id)
                GROUP BY o.order_id
                HAVING ABS(o.total_amount-SUM(i.quantity*i.purchase_price))>.01""").fetchall()
        if rows:
            bad_money.append({"episode": path.parent.name, "orders": rows})
    with business_clock():
        logical_clock_matches = Evidence().checked_at == NOW.isoformat()
    episodes = json.loads((root / ".private-evidence/episodes.json").read_text())
    coverage = []
    for row in episodes:
        missing = any(not s["raw_goal_covered"] for s in row["steps"] if s["raw"] is not None)
        if missing:
            coverage.append(
                {
                    "task_id": row["task_id"],
                    "arm": row["arm"],
                    "not_falsely_successful": not row["task_success"],
                }
            )
    return {
        "source_sha": manifest["source_sha"],
        "paid_model_http": 0,
        "episodes": len(episodes),
        "money_mismatches": bad_money,
        "logical_clock_matches": logical_clock_matches,
        "incomplete_answers": coverage,
        "covered_integrity_passed": (
            not bad_money
            and logical_clock_matches
            and all(r["not_falsely_successful"] for r in coverage)
            and score["execution_gate_passed"]
        ),
        "novelty": "seen-data revalidation after v1 measurement/fixture defects",
        "full_user_goal_semantic_certification": False,
        "real_model_comparison_completed": False,
        "graph_mirror_repair_claimed": False,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    result = audit(root)
    (root / "validity_review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(result)
