"""Read-only post-freeze validity audit; does NOT change the frozen scorer/data."""

import json
import sqlite3
from pathlib import Path

from acs.answer_grounding import Evidence
from acs.multiturn_evaluation import NOW, business_clock, load_suite


def audit(root):
    load_suite(Path(__file__).resolve().parents[2] / "data/eval/v3_multiturn_m8_v1")
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["mode"] == "offline"
    money = []
    for p in sorted((root / ".private-evidence").glob("*/business.sqlite")):
        with sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            rows = conn.execute("""SELECT o.order_id,o.total_amount,SUM(i.quantity*i.purchase_price)
                FROM orders o JOIN order_items i USING(order_id)
                GROUP BY o.order_id
                HAVING ABS(o.total_amount-SUM(i.quantity*i.purchase_price))>.01""").fetchall()
        if rows:
            money.append(
                {
                    "episode": p.parent.name,
                    "mismatches": [
                        {"order_id": r[0], "order_total": r[1], "line_item_total": r[2]}
                        for r in rows
                    ],
                }
            )
    with business_clock():
        evidence_time = Evidence().checked_at
    return {
        "evaluation_source_sha": manifest["source_sha"],
        "frozen_files_modified": False,
        "paid_requests": 0,
        "monetary_invariant": {
            "assumption": "No separate order discount is modeled in this schema",
            "affected_episodes": len(money),
            "details": money,
        },
        "clock_scope": {
            "business_now": NOW.isoformat(),
            "evidence_checked_at": evidence_time,
            "same_logical_time": evidence_time == NOW.isoformat(),
            "scope": "SQL uses synthetic clock; numeric deadline answers not covered",
        },
        "goal_coverage_review": [
            {
                "task_id": "D02",
                "step": 0,
                "observation": "请先选择订单 does not enumerate the requested IDs",
            },
            {
                "task_id": "D02",
                "step": 1,
                "observation": "Reference omits requested order details",
            },
            {
                "task_id": "D06",
                "step": 2,
                "observation": "Draft retention alone is not a full task summary",
            },
        ],
        "interpretation": "v1 task_success is authored-contract success, not full goal completion",
        "quality_evaluation_gate": "NO_GO_pending_new_version_fixture_clock_and_goal_rubric_audit",
        "correction_policy": "Retain v1; any later version is seen-data revalidation",
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    result = audit(root)
    (root / "validity_review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print({k: v for k, v in result.items() if k not in ("monetary_invariant",)})
