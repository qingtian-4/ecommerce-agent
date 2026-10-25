"""Sample existing 228 anonymous M8 items; export no independent review verdicts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from collections import Counter
from pathlib import Path
from uuid import uuid4


def prepare(source, out, *, count=48, seed=8101):
    if not 40 <= count <= 60:
        raise ValueError("Review sample must contain 40..60 historical answer items")
    os.umask(0o077)
    materials = json.loads((source / "blind-goal-review-v2.json").read_text())
    keys = json.loads((source / "blind-goal-review-key-v2.json").read_text())
    episodes = json.loads((source / "episodes.json").read_text())
    if len(materials) != 228 or len(keys) != 228:
        raise ValueError("Expected exactly 228 existing materials and private keys")
    by_id = {r["review_id"]: r for r in materials}
    episode_map = {(r["task_id"], r["arm"]): r for r in episodes}
    candidates, changed_pairs = [], {}
    for key in keys:
        row = episode_map[key["task_id"], key["arm"]]
        step = next(s for s in row["steps"] if s["step"] == key["step"])
        raw_good = step["raw_grade"]["status"] == "faithful"
        display_good = step["display_grade"]["status"] == "faithful"
        kind = key["kind"]
        if kind == "raw" and not raw_good:
            stratum = "raw_error"
        elif kind == "display" and not raw_good and display_good:
            stratum = "correction_success"
        elif kind == "display" and not raw_good and not display_good:
            stratum = "missed_error"
        elif raw_good and step["raw"] != step["display"]:
            stratum = "correct_answer_changed"
        elif not step.get(kind + "_goal_covered", False):
            stratum = "goal_omission"
        else:
            stratum = "raw_correct" if kind == "raw" else "display_correct"
        candidates.append((stratum, key, step))
        if step["raw"] != step["display"]:
            changed_pairs.setdefault((key["task_id"], key["arm"], key["step"]), {})[kind] = key
    rng = random.Random(seed)
    rng.shuffle(candidates)
    # Include all scarce errors/changes, then goal omissions and balanced correct controls.
    quotas = {
        "raw_error": 6,
        "correction_success": 4,
        "missed_error": 2,
        "correct_answer_changed": 6,
        "goal_omission": 14,
        "raw_correct": 11,
        "display_correct": 11,
    }
    chosen = []
    for label, quota in quotas.items():
        chosen.extend([r for r in candidates if r[0] == label][:quota])
    chosen = chosen[:count]
    used = {r[1]["review_id"] for r in chosen}
    chosen.extend([r for r in candidates if r[1]["review_id"] not in used][: count - len(chosen)])
    rng.shuffle(chosen)
    queue, operator = [], []
    for stratum, key, step in chosen:
        item = dict(by_id[key["review_id"]])
        item["review_id"] = uuid4().hex
        item["evidence"] = dict(item["evidence"], policy=step["state"]["policy"])
        item["review_fields"] = {
            "fact": "correct/incorrect/unknown",
            "goal": "complete/partial/unanswered/unknown",
            "unsupported_business_claim": "yes/no/unknown",
            "evidence_citations": [],
            "unknown_reason": "",
            "reviewer_id": "",
            "reviewed_at": "",
        }
        queue.append(item)
        operator.append(dict(key, new_review_id=item["review_id"], stratum=stratum))
    pairs, pair_keys = [], []
    orientation_rng = random.SystemRandom()
    for pair in changed_pairs.values():
        orientation = ["raw", "display"]
        orientation_rng.shuffle(orientation)
        item = by_id[pair["raw"]["review_id"]]
        pid = uuid4().hex
        pairs.append(
            {
                "pair_id": pid,
                "conversation": item["conversation"],
                "evidence": item["evidence"],
                **{
                    label: by_id[pair[kind]["review_id"]]["answer"]
                    for label, kind in zip(("A", "B"), orientation, strict=True)
                },
                "review_fields": {
                    "preferred": "A/B/equal/unknown",
                    "fact_change": "improved/worse/equal/unknown",
                    "goal_change": "improved/worse/equal/unknown",
                    "reason": "",
                    "reviewer_id": "",
                },
            }
        )
        pair_keys.append(dict(pair_id=pid, A=orientation[0], B=orientation[1], keys=pair))
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    for name, data in (
        ("reviewer-items.json", queue),
        ("reviewer-pairs.json", pairs),
        ("operator-key.json", operator),
        ("operator-pair-key.json", pair_keys),
    ):
        with (private / name).open("x") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    available = Counter(r[0] for r in candidates)
    summary = dict(
        source_items=len(materials),
        sampled_items=len(queue),
        comparison_pairs=len(pairs),
        seed=seed,
        available_strata=dict(available),
        sampled_strata=dict(Counter(r["stratum"] for r in operator)),
        unavailable_strata=[k for k in quotas if not available[k]],
        review_performed=False,
        independent_reviewer_available=False,
        labels_hidden=["arm", "task_id", "kind", "automatic_score", "stratum"],
        randomized_pair_orientation=True,
        orientation_source="system_entropy_independent_of_public_sampling_seed",
        real_http_requests=0,
        source_hashes={
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in (
                "blind-goal-review-v2.json",
                "blind-goal-review-key-v2.json",
                "episodes.json",
            )
        },
        package_hashes={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(private.glob("*.json"))
        },
    )
    (out / "review_package_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.out), indent=2))
