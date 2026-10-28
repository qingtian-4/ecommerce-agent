"""Paired actual SDK serialization, native runtime and SQLite; never real HTTP."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_live_readiness import model_for  # noqa: E402

from acs import multiturn_evaluation as m8  # noqa: E402
from acs.commerce_benchmark import no_network  # noqa: E402
from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.cs_tools import ToolRegistry, expand_model_catalog  # noqa: E402
from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings  # noqa: E402
from acs.shop_demo_data import SOURCE, materialize  # noqa: E402

CASES = ROOT / "data/eval/shop_boundary_v1/cases.json"


def run(out, dataset=SOURCE):
    out.mkdir(parents=True, exist_ok=False)
    cases = json.loads(CASES.read_text())
    reports = []
    probe = out / ".private-evidence" / "probe"
    materialize(probe, dataset)
    registry = ToolRegistry(fixture_settings(probe, kind=FORMAL_FIXTURE_KIND))
    limits = replace(m8.LIMITS, requests=24, episode_timeout_s=600)
    for case in cases["cases"]:
        args = dict(case["query"])
        if case.get("start_after"):
            args["after_product_id"] = case["start_after"]
        pages, expected = [], []
        while True:
            value, _, error = registry.run("list_products", json.dumps(args),
                                           current_user_id=m8.UID)
            assert error is None
            pages.append(args)
            expected.extend(p["product_id"] for p in value["products"])
            if not value["has_more"] or len(pages) == case.get("max_pages"):
                break
            args = value["next_arguments"]
        for arm in ("old", "v3"):
            root = out / ".private-evidence" / (case["id"] + "-" + arm)
            root.mkdir(parents=True)
            texts = ["历史约束：" + "甲" * case["history_chars"]
                     for _ in range(case["history_turns"])] + ["按原条件查询，未穷尽不能声称完整。"]
            task = m8.Task(task_id=case["id"], split="development", goal="目录完整性与预算",
                           fixture_index=0, steps=[m8.Step(text=t, expect={"requests": 0},
                           allowed_tools=["list_products"], answers=[]) for t in texts])
            queue = [dict(text="已记录。") for _ in texts[:-1]] + [dict(calls=[dict(
                id=f"page-{i}", name="list_products", args=a)]) for i, a in enumerate(pages)]
            queue += [dict(text="目录查询结束。")]
            ledger = EpisodeLedger(root / "fees.jsonl", max_requests=40)
            episode = case["id"] + ":" + arm
            ledger.begin(episode, limits)

            def fixture(path, ignored):
                materialize(path, dataset)
                return m8.initial_fingerprint(path)

            with (no_network(), patch.object(m8, "build_fixture", fixture),
                  model_for(root, ledger, episode, queue=queue) as model):
                result = m8.run_task(task, [], root / "fixture", arm,
                                     model=model, ledger=ledger, limits=limits)
            bodies = [e["body_bytes"] for e in ledger.rows() if e["type"] == "mock_http_observed"]
            rejected = [e for e in ledger.rows() if e["type"] == "preflight_rejected"]
            receipts = [json.loads(r["result"]) for r in result["final"]["receipts"]]
            by_call = {r["call_id"]: json.loads(r["result"]) for r in result["final"]["receipts"]}
            records = json.loads((root / "fixture/model-inputs.json").read_text())
            projections = 0
            for record in records:
                users = [m["content"] for m in record["messages"] if m["role"] == "user"]
                assert users == texts[:len(users)], "History must never be silently evicted"
                for message in record["messages"]:
                    if message["role"] == "tool":
                        encoded = json.loads(message["content"])["result"]
                        assert expand_model_catalog(encoded) == by_call[message["tool_call_id"]]
                        projections += 1
            seen = [p["product_id"] for r in receipts for p in r["products"]]
            complete = (result["status"] == "completed" and not queue
                        and not case.get("start_after") and bool(receipts)
                        and not receipts[-1]["has_more"])
            reports.append(dict(id=case["id"], arm=arm, complete=complete,
                expected_complete=case["expect_complete"], status=result["status"],
                error=result["error"], seen_ids=seen, planned_ids=expected,
                body_bytes=bodies, rejected=[dict(reason=r["reason"], body_bytes=r["body_bytes"])
                                           for r in rejected],
                displayed=[s.get("display") for s in result["steps"]],
                wire_projections_reversed=projections, history_preserved=True,
                paid_requests=0, policy="No history eviction; actual wire limit unchanged."))
    report = dict(protocol=cases["version"], cases_sha256=file_hash(CASES),
                  dataset=str(dataset.relative_to(ROOT)) if dataset.is_relative_to(ROOT)
                  else str(dataset), limits=vars(limits), body_limit=32768, reports=reports)
    dump(out / "report.json", report)
    print(json.dumps([dict(id=r["id"], arm=r["arm"], complete=r["complete"],
                          max_bytes=max(r["body_bytes"], default=0), rejected=r["rejected"])
                      for r in reports]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=SOURCE)
    args = parser.parse_args()
    run(args.out.resolve(), args.dataset.resolve())
