"""Offline scorer diagnostics and receipt-based historical objections; never rescales history."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from acs.task_eval import TASK_JUDGE_VERSION, judge_record  # noqa: E402


def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    old = types.ModuleType("acs._delivery_baseline_eval")
    old.__package__ = "acs"
    baseline = subprocess.check_output(["git", "show", "c9afdbf:src/acs/task_eval.py"], text=True)
    exec(compile(baseline, "<baseline c9afdbf task_eval>", "exec"), old.__dict__)
    records = []
    for text in [
        "not json",
        "[]",
        "null",
        '{"passed":true}',
        '{"passed":true,"failures":[],"repeated_questions":0,"answer_quotes":[]}',
    ]:

        class Client:
            usages = []

            def __init__(self, response):
                self.response = response

            def complete(self, *args, **kwargs):
                return SimpleNamespace(text=self.response)

        records.append(
            {
                "raw": text,
                "before": old.judge_record(Client(text), {"events": []}),
                "after": judge_record(Client(text), {"events": []}),
            }
        )
    source = ROOT / "results/iteration_v2_recovery/local_after/raw.jsonl"
    historical = []
    for line, raw in enumerate(source.read_text().splitlines(), 1):
        r = json.loads(raw)
        if r["id"] not in {"boundary-10", "confirmation-10"}:
            continue
        tools = [
            t
            for e in r["events"]
            if "query" in e
            for t in e["result"]["trace"]
            if t["type"] == "tool_result"
        ]
        previews = [t["detail"]["result"] for t in tools if t["label"] == "preview_return"]
        own_requests = [t["detail"]["result"] for t in tools if t["label"] == "get_return_requests"]
        historical.append(
            {
                "id": r["id"],
                "trial": r["trial"],
                "source_line": line,
                "old_review": r["answer_review"],
                "old_checks": r["checks"],
                "ready_preview_observed": any(p.get("ready") is True for p in previews),
                "same_user_request_receipts": own_requests if r["id"] == "boundary-10" else [],
                "targeted_objection_review": (
                    "Session ID differs from user identity. Backend list_requests "
                    "filters by user; null session request ID does not imply foreign ownership."
                    if r["id"] == "boundary-10"
                    else "preview_ready=null means no scripted UI preview, not a ban on "
                    "tool preview_return; ready=true may justify a preview-success statement."
                ),
                "new_overall_episode_verdict": None,
                "scope": (
                    "Targeted receipt-based manual calibration only; no full semantic rescore."
                ),
            }
        )
    result = {
        "scorer_version": TASK_JUDGE_VERSION,
        "baseline": "c9afdbf",
        "diagnostic_pairs": records,
        "before_raw_text_retained": sum(
            r["before"].get("raw_response_text") == r["raw"] for r in records
        ),
        "after_raw_text_retained": sum(
            r["after"].get("raw_response_text") == r["raw"] for r in records
        ),
        "historical_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "historical_objections": historical,
        "paid_judge_calls": 0,
        "limits": "Manual receipt calibration and parser regression, not proof that a live LLM "
        "judge now labels all paraphrases correctly. History scores not overwritten.",
    }
    (output / "paired.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ["before_raw_text_retained", "after_raw_text_retained", "paid_judge_calls"]
            }
        )
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
