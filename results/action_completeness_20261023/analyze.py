"""Derive cross-arm v2 metrics without changing frozen scores or raw rows."""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARMS = ["final-baseline-off", "final-baseline-p0-on", "final-candidate-off", "final-candidate-on"]


def main():
    rows = {
        arm: [json.loads(s) for s in (ROOT / arm / "raw.jsonl").read_text().splitlines()]
        for arm in ARMS
    }
    assert all(len(v) == 44 and len({r["id"] for r in v}) == 44 for v in rows.values())
    by_id = {arm: {r["id"]: r for r in values} for arm, values in rows.items()}
    combined = [r | {"arm": arm} for arm, values in rows.items() for r in values]
    candidate = by_id[ARMS[-1]]
    consultation_ids = {
        c["id"]
        for c in json.loads((ROOT / "cases_v2.json").read_text())["cases"]
        if c["consultation"]
    }
    comparisons = {}
    for arm in ARMS[:-1]:
        previous = by_id[arm]
        protected = [
            cid
            for cid, r in previous.items()
            if r["consultation_complete"] and cid in consultation_ids
        ]
        comparisons[arm] = dict(
            saved_regressions=[
                cid
                for cid, r in previous.items()
                if r["action_complete"] and not candidate[cid]["action_complete"]
            ],
            whole_task_regressions=[
                cid
                for cid, r in previous.items()
                if r["strict_complete"] and not candidate[cid]["strict_complete"]
            ],
            consultation_regressions=[
                cid for cid in protected if not candidate[cid]["consultation_complete"]
            ],
            preservation_numerator=sum(
                candidate[cid]["consultation_complete"] for cid in protected
            ),
            preservation_denominator=len(protected),
            mean_next_context_growth=sum(
                candidate[cid]["next_context_chars"] - r["next_context_chars"]
                for cid, r in previous.items()
            )
            / 44,
            snapshot_equal=all(
                r["business_snapshot_sha256"] == candidate[cid]["business_snapshot_sha256"]
                for cid, r in previous.items()
            ),
            model_calls_delta=sum(
                candidate[cid]["model_calls"] - r["model_calls"] for cid, r in previous.items()
            ),
            tool_calls_delta=sum(
                candidate[cid]["tool_calls"] - r["tool_calls"] for cid, r in previous.items()
            ),
        )
    left, right = by_id[ARMS[0]], by_id[ARMS[2]]
    off_equal = all(
        all(
            left[cid][k] == right[cid][k]
            for k in (
                "answer",
                "strict_complete",
                "action_complete",
                "unauthorized_write",
                "duplicate_write",
                "consultation_complete",
                "classification",
                "model_calls",
                "tool_calls",
                "next_context_chars",
            )
        )
        for cid in left
    )
    result = dict(
        version="action-completeness-v2",
        arms={arm: json.loads((ROOT / arm / "summary.json").read_text()) for arm in ARMS},
        candidate_comparisons=comparisons,
        off_ablation_equal=off_equal,
        context_probe_requests=44 * 4,
        token_usage=None,
        paid_requests=0,
    )
    (ROOT / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    fields = [
        "arm",
        "id",
        "necessary",
        "action_complete",
        "unauthorized_write",
        "duplicate_write",
        "consultation_complete",
        "state_consistent",
        "honest_unknown",
        "strict_complete",
        "classification",
        "model_calls",
        "tool_calls",
        "next_context_chars",
        "latency_ms",
    ]
    compact = [
        {k: r[k] for k in fields}
        | {"raw_evidence": r["arm"] + "/raw.jsonl", "raw_case_id": r["id"]}
        for r in combined
    ]
    (ROOT / "ACTION_COMPLETENESS_SCORECARD.json").write_text(
        json.dumps(compact, ensure_ascii=False, indent=2) + "\n"
    )
    for arm in ARMS:
        (ROOT / arm / "ACTION_COMPLETENESS_SCORECARD.json").write_text(
            json.dumps([r for r in compact if r["arm"] == arm], ensure_ascii=False, indent=2) + "\n"
        )
    with (ROOT / "ACTION_COMPLETENESS_SCORECARD.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(combined)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
