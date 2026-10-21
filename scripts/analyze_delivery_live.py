"""Recompute measurements; semantic labels are explicit source-grounded human review."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

FAILURES = {
    "white-restore": "Lists black/beige/orange headphones despite active white-only constraint.",
    "white-correction": "Includes white S20008/S20014 after explicit change to black-only.",
    "white-cross-order": "White primary list, but offers black S20003/S20019/S20009 alternatives.",
    "quantity-units": "Answers 27 units; authoritative sum(quantity) is 29.",
    "quantity-all": "Answers 20 orders/22 lines/24 units; authoritative values are 20/25/29.",
}
DIRECT_RAW_ERRORS = {
    "white-restore": "Raw recommends black S20013/S20019; final contains only three white lines.",
    "white-cross-order": "Raw recommends black S20013/S20019; final contains only white lines.",
    "quantity-units": "Raw 26 units -> final 29 units.",
    "quantity-all": "Raw 20 orders/23 lines/26 units -> final 20/25/29.",
    "quantity-delivered": "Raw 8 orders/9 lines/12 units -> final 8/10/14.",
}


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    # Nearest-rank p95, median conventional average; small N is reported explicitly.
    from math import ceil
    from statistics import median

    return {
        "n": len(values),
        "p50": round(median(values), 3),
        "p95": round(values[ceil(0.95 * len(values)) - 1], 3),
        "max": round(max(values), 3),
    }


def run(root):
    calls = json.loads((root / "requests.json").read_text())["calls"]
    rows = [json.loads(line) for line in (root / "raw.jsonl").read_text().splitlines()]
    # Observed calls 2026-10-21 08:13-08:15 UTC, weekday peak pricing.
    assert all("2026-10-21T08:" in c["at_utc"] for c in calls)
    for call in calls:
        usage = call["provider_usage"]
        assert (
            usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"]
            == (usage["prompt_tokens"])
        )
        assert not call["usage"]["usage_missing"]
    assessments, metrics = [], {}
    for enabled in (False, True):
        selected = [r for r in rows if r["enabled"] == enabled]
        seqs = {n for r in selected for n in r["request_sequences"]}
        selected_calls = [c for c in calls if c["sequence"] in seqs]
        all_turns = [t for r in selected for t in r["turns"]]
        targets = [
            r["turns"][-1]
            for r in selected
            if r["case"]
            in (
                "white-restore",
                "white-correction",
                "white-cross-order",
                "quantity-units",
                "quantity-all",
                "quantity-delivered",
            )
        ]
        usage = Counter()
        fee = Decimal(0)
        for c in selected_calls:
            u = c["provider_usage"]
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            ):
                usage[key] += u[key]
            fee += (
                Decimal(u["prompt_cache_miss_tokens"]) * 2
                + Decimal(u["prompt_cache_hit_tokens"]) * Decimal(".04")
                + Decimal(u["completion_tokens"]) * 8
            ) / 1_000_000
        labels = Counter(
            e["label"]
            for t in all_turns
            for e in t["result"]["trace"]
            if e["type"] == "fact_render"
        )
        state_checks = Counter(
            e["detail"]["status"]
            for t in all_turns
            for e in t["result"]["trace"]
            if e["type"] == "state_check"
        )
        growth = []
        for r in selected:
            error = FAILURES.get(r["case"]) if not enabled else None
            fact_pass = enabled or r["case"] not in ("quantity-units", "quantity-all")
            t = r["turns"][-1]
            gates = [e for e in t["result"]["trace"] if e["type"] == "output_gate"]
            raw = gates[-1]["detail"]["original_answer"] if gates else t["result"]["answer"]
            patterns = {
                "white-restore": r"黑色",
                "white-correction": r"S20014|S20008",
                "white-cross-order": r"黑色耳机",
                "quantity-units": r"27\s*件",
                "quantity-all": r"22\s*条|24\s*件",
            }
            # These identify the already manually reviewed errors, not a general fact judge.
            bad_frames = [
                f for f in t["frames"] if error and re.search(patterns[r["case"]], f["answer"])
            ]
            growth.append(
                {
                    "case": r["case"],
                    "original_answer_chars": len(raw),
                    "final_answer_chars": len(t["result"]["answer"]),
                    "assistant_content_char_delta": len(t["result"]["answer"]) - len(raw),
                }
            )
            assessments.append(
                {
                    "case": r["case"],
                    "enabled": enabled,
                    "task_pass": not error,
                    "fact_pass": fact_pass,
                    "reason": error or "Matches frozen final task and SQL receipt.",
                    "candidate_raw_error_fixed": (
                        DIRECT_RAW_ERRORS.get(r["case"]) if enabled else None
                    ),
                    "source": "raw.jsonl, final turn, trace tool_result/list_orders",
                    "visible_error": bool(error),
                    "identified_wrong_frames": len(bad_frames),
                    "identified_wrong_running_frames": sum(
                        f["phase"] != "completed" for f in bad_frames
                    ),
                    "business_unchanged": r["business_unchanged"],
                    "all_turns_synchronized": all(t["synchronized"] for t in r["turns"]),
                }
            )
        arm = [a for a in assessments if a["enabled"] == enabled]
        metrics["candidate" if enabled else "baseline"] = {
            "conversations": len(selected),
            "turns": len(all_turns),
            "requests": len(selected_calls),
            "usage": dict(usage),
            "estimated_invoice_cny": str(fee),
            "task_pass": sum(a["task_pass"] for a in arm),
            "facts_pass": sum(a["fact_pass"] for a in arm),
            "visible_error_conversations": sum(a["visible_error"] for a in arm),
            "identified_wrong_frames": sum(a["identified_wrong_frames"] for a in arm),
            "identified_wrong_running_frames": sum(
                a["identified_wrong_running_frames"] for a in arm
            ),
            "fact_render_triggers": dict(labels),
            "state_check_statuses": dict(state_checks),
            "output_gate_triggers": sum(
                e["type"] == "output_gate" for t in all_turns for e in t["result"]["trace"]
            ),
            "turn_ms": distribution([t["elapsed_ms"] for t in all_turns]),
            "first_answer_ms": distribution([t["first_visible_ms"] for t in all_turns]),
            "targeted_first_answer_ms": distribution([t["first_visible_ms"] for t in targets]),
            "api_first_content_ms": distribution(
                [
                    c["first_content_ms"]
                    for c in selected_calls
                    if c.get("first_content_ms") is not None
                ]
            ),
            "actual_model_input_chars": sum(
                sum(t["result"]["totals"]["model_input_chars"]) for t in all_turns
            ),
            "final_vs_original_chars": growth,
        }
    result = {
        "semantic_method": "Manual review against frozen contract, SQL and full visible frames; "
        "not an independent blind judge, not a held-out set or population estimate.",
        "pricing": ("CNY peak per million: miss2/hit0.04/output8; "
                    "estimated, not invoice reconciled."),
        "arms": metrics,
        "assessments": assessments,
        "limits": [
            "One trial per arm; no stable causal or significance claim.",
            "P0/P1 shared; only color/aggregation toggle, no factorial ablation.",
            "Raw corrections are within-generation evidence, not counterfactual calls.",
            "Next-context char delta is assistant content only; no next paid turn added.",
            "Final source narrows cancel scope after live; offline replay is identical.",
        ],
    }
    (root / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: {k: v for k, v in arm.items() if k != "final_vs_original_chars"}
                for key, arm in metrics.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    run(parser.parse_args().root)
