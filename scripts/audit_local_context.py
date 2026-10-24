"""Private-evidence-only context/cost audit; export numbers and hashes, never prompts.

Component token figures are UTF-8-byte-weighted allocations of provider prompt
usage, NOT DeepSeek tokenization. Exact wire bytes, counts, usage and reservations
are measured separately; the unchanged-decision counterfactual is offline only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

PREFIX = "当前应用核验事实："


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def components(body):
    groups = Counter()
    facts = []
    for message in body["messages"]:
        role = message["role"]
        content = message.get("content") or ""
        if role == "system":
            key = "current_fact_snapshots" if content.startswith(PREFIX) else "fixed_system_prompt"
            if key == "current_fact_snapshots":
                facts.append(message)
        elif role == "tool":
            result = json.loads(content)
            if result.get("current_task") is not None:
                groups["tool_current_task"] += len(encoded(result.pop("current_task")))
            payload = result.get("result", {})
            if payload.get("ready"):
                key = "tool_preview"
            elif "documents" in payload:
                key = "tool_policy"
            elif "items" in payload or "order" in payload:
                key = "tool_order"
            else:
                key = "tool_other_receipts"
            # Preserve wire totals by assigning JSON field scaffolding to the tool category.
            total = len(encoded(message))
            current = json.loads(content).get("current_task")
            groups[key] += total - (len(encoded(current)) if current is not None else 0)
            continue
        elif message.get("tool_calls"):
            key = "assistant_tool_calls"
        else:
            key = "user_and_assistant_text"
        groups[key] += len(encoded(message))
    groups["tools_schema"] += len(encoded(body.get("tools", [])))
    total = len(encoded(body))
    groups["http_json_and_other_settings"] += total - sum(groups.values())
    return groups, facts


def audit(run):
    private = run / ".private-evidence/runtime/.private-evidence"
    records = [json.loads(p.read_text()) for p in private.glob("*.json")]
    requests = {
        r["request_id"]: r["body"]
        for r in records
        if r.get("kind") == "authorized_private_model_request"
    }
    ledger = [json.loads(line) for line in (private.parent / "fees.jsonl").read_text().splitlines()]
    reservations = [r for r in ledger if r["type"] == "reserved"]
    ends = {r["request_id"]: r for r in ledger if r["type"] == "request_end"}
    rows = []
    all_groups = Counter()
    for index, reservation in enumerate(reservations, 1):
        body = requests[reservation["request_id"]]
        wire = encoded(body)
        assert hashlib.sha256(wire).hexdigest() == reservation["body_sha256"]
        groups, facts = components(body)
        all_groups.update(groups)
        usage = ends.get(reservation["request_id"], {}).get("usage") or {}
        prompt = usage.get("prompt_tokens")
        output = usage.get("completion_tokens")
        valid_prompt = type(prompt) is int and prompt >= 0
        projected = body | {
            "messages": [
                m
                for m in body["messages"]
                if not (m["role"] == "system" and (m.get("content") or "").startswith(PREFIX))
            ]
            + facts[-1:]
        }
        projected_bytes = len(encoded(projected))
        counterfactual_reserve = (
            Decimal(2 * projected_bytes + 4096) * 2 + Decimal(reservation["output_token_bound"]) * 8
        ) / 1_000_000
        repeated = sum(len(encoded(m)) for m in facts[:-1])
        rows.append(
            {
                "request": index,
                "message_count": len(body["messages"]),
                "fact_snapshots": len(facts),
                "wire_bytes": len(wire),
                "provider_prompt_tokens": prompt if valid_prompt else None,
                "provider_completion_tokens": output
                if type(output) is int and output >= 0
                else None,
                "component_bytes": dict(groups),
                "component_token_estimates": {
                    k: round(v / len(wire) * prompt, 2) if valid_prompt else None
                    for k, v in groups.items()
                },
                "redundant_historical_fact_bytes": repeated,
                "redundant_historical_fact_ratio": repeated / len(wire),
                "literal_duplicate_fact_count": len(facts) - len({m["content"] for m in facts}),
                "original_reserved_cny": reservation["reserved_cny"],
                "reservation_parts_cny": {
                    "actual_wire_bytes_input_bound": str(Decimal(len(wire)) * 4 / 1_000_000),
                    "fixed_4096_input_margin": "0.008192",
                    "max_1024_output": "0.008192",
                },
                "offline_same_decision_projected_wire_bytes": projected_bytes,
                "offline_same_decision_projected_reserve_cny": str(counterfactual_reserve),
            }
        )
    total_bytes = sum(r["wire_bytes"] for r in rows)
    return {
        "run": run.name,
        "scope": "offline analysis of private evidence; no HTTP",
        "token_estimate_method": (
            "allocate actual prompt tokens by compact serialized UTF-8 byte share; not a tokenizer"
        ),
        "requests": rows,
        "totals": {
            "provider_prompt_tokens": sum(r["provider_prompt_tokens"] or 0 for r in rows),
            "provider_completion_tokens": sum((r["provider_completion_tokens"] or 0) for r in rows),
            "wire_bytes": total_bytes,
            "component_bytes": dict(all_groups),
            "redundant_historical_fact_ratio": sum(
                r["redundant_historical_fact_bytes"] for r in rows
            )
            / total_bytes,
            "literal_duplicate_facts": sum(r["literal_duplicate_fact_count"] for r in rows),
            "original_reserved_cny": str(
                sum((Decimal(r["original_reserved_cny"]) for r in rows), Decimal(0))
            ),
            "offline_same_decision_projected_reserve_cny": str(
                sum(
                    (Decimal(r["offline_same_decision_projected_reserve_cny"]) for r in rows),
                    Decimal(0),
                )
            ),
        },
        "settlement": {
            "completed_with_usage": sum(
                r["status"] == "completed" and r.get("estimated_cny") is not None
                for r in ends.values()
            ),
            "unsettled": len(reservations) - len(ends),
            "unknown_usage": sum(r.get("estimated_cny") is None for r in ends.values()),
            "failed_requests": sum(r["status"] != "completed" for r in ends.values()),
            "in_flight": len(reservations) - len(ends),
        },
        "counterfactual_warning": (
            "Projected old requests hold decisions and tools fixed; "
            "not real optimized model results"
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.write_text(json.dumps(audit(args.run), ensure_ascii=False, indent=2) + "\n")
