"""Read-only blind-review package; no scoring, model calls, or experiment rewriting."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from acs.multiturn_evaluation import UID, load_suite


def export(root, suite):
    tasks, _, sealed = load_suite(suite)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["suite"]["files"] != sealed["files"]:
        raise ValueError("Review task version mismatch")
    records = json.loads((root / ".private-evidence/episodes.json").read_text())
    queue, keys = [], []
    for row in records:
        task = next(t for t in tasks if t.task_id == row["task_id"])
        runtime = root / ".private-evidence" / (task.task_id + "-" + row["arm"])
        if (
            json.loads((runtime / "fixture.json").read_text())["kind"]
            != "local-return-formal-synthetic-1"
        ):
            raise ValueError("Only isolated synthetic formal fixtures may be exported")
        with sqlite3.connect(
            (runtime / "business.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            catalog = [
                dict(r)
                for r in conn.execute(
                    "SELECT product_id,name,category,brand,price,currency,attributes FROM products"
                )
            ]
            orders = [
                dict(r)
                for r in conn.execute(
                    "SELECT order_id,status,total_amount,paid_amount FROM orders WHERE user_id=?",
                    (UID,),
                )
            ]
            items = [
                dict(r)
                for r in conn.execute(
                    "SELECT i.order_id,i.id,i.product_id,i.quantity,i.purchase_price "
                    "FROM order_items i JOIN orders o USING(order_id) WHERE o.user_id=?",
                    (UID,),
                )
            ]
        conversation = []
        for step in row["steps"]:
            if step["raw"] is None:
                continue
            user_text = task.steps[step["step"]].text
            conversation.append({"role": "user", "text": user_text})
            state = step["state"]
            evidence = {
                k: state[k]
                for k in (
                    "products",
                    "counts",
                    "requests",
                    "preview_count",
                    "previous_preview_count",
                    "valid_preview",
                )
            }
            evidence["draft"] = {
                k: v
                for k, v in (state["draft"] or {}).items()
                if k
                in ("status", "order_id", "item_id", "quantity", "reason", "condition", "revision")
            }
            evidence["policy"] = {k: state["policy"][k] for k in ("windows", "shipping", "hash")}
            evidence.update(
                product_catalog=catalog,
                orders=orders,
                order_items=items,
                logical_business_time=manifest["logical_business_clock"],
            )
            for kind in ("raw", "display"):
                identity = uuid4().hex
                queue.append(
                    {
                        "review_id": identity,
                        "conversation": list(conversation),
                        "answer": step[kind],
                        "evidence": evidence,
                        "axes": ["fact: true/false/unknown", "goal: complete/partial/unknown"],
                    }
                )
                keys.append(
                    {
                        "review_id": identity,
                        "task_id": task.task_id,
                        "step": step["step"],
                        "arm": row["arm"],
                        "kind": kind,
                    }
                )
            conversation.append({"role": "assistant", "text": step["display"]})
    private = root / ".private-evidence"
    for name, data in [
        ("blind-goal-review-v2.json", sorted(queue, key=lambda r: r["review_id"])),
        ("blind-goal-review-key-v2.json", keys),
    ]:
        with (private / name).open("x") as handle:
            json.dump(data, handle, ensure_ascii=False)
        (private / name).chmod(0o600)
    summary = {
        "items": len(queue),
        "source_sha": manifest["source_sha"],
        "review_performed": False,
        "label_id_strategy": "random_uuid_not_reversible_task_hash",
        "judge_http": 0,
        "sensitive_SQL_columns_exported": [],
        "automatic_grade_shown_to_reviewer": False,
        "arm_and_raw_display_key_separate": True,
        "queue_sha256": hashlib.sha256(
            (private / "blind-goal-review-v2.json").read_bytes()
        ).hexdigest(),
    }
    (root / "blind_review_export_v2.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--suite", type=Path, default=Path("data/eval/v3_multiturn_m8_v2"))
    args = parser.parse_args()
    print(export(args.run, args.suite))
