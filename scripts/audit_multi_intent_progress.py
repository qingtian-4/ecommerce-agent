"""Replay the observed two-tool decision and a proposed overview path, offline."""
import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_live_readiness import model_for  # noqa: E402

from acs import multiturn_evaluation as m8  # noqa: E402
from acs.commerce_benchmark import no_network  # noqa: E402
from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.shop_demo_data import SOURCE, materialize  # noqa: E402


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    cases = ROOT / "data/eval/multi_intent_progress_v1/cases.json"
    reports = []
    for mode in ("observed_first_page", "proposed_overview"):
        for arm in ("old", "v3"):
            root = out / ".private-evidence" / (mode + "-" + arm)
            root.mkdir(parents=True)
            task = m8.Task(task_id="MP01", split="development", fixture_index=0,
                goal="本人订单候选和店铺商品概览", steps=[m8.Step(
                    text="我目前都有哪些订单，然后我们这个店都有哪些产品",
                    allowed_tools=["list_orders", "list_products"],
                    expect={"requests": 0}, answers=[])])
            queue = [dict(calls=[dict(id="orders", name="list_orders", args={}),
                dict(id="products", name="list_products", args=(
                    {"page_size": 8} if mode == "observed_first_page" else {"view": "overview"}))]),
                dict(text="订单查询已返回，商品信息请查看卡片。")]
            ledger = EpisodeLedger(root / "fees.jsonl")
            episode = "MP01:" + arm
            ledger.begin(episode, m8.LIMITS)

            def fixture(path, task):
                materialize(path, SOURCE)
                return m8.initial_fingerprint(path)

            with (no_network(), patch.object(m8, "build_fixture", fixture),
                  model_for(root, ledger, episode, queue=queue) as model):
                result = m8.run_task(task, [], root / "fixture", arm,
                                     model=model, ledger=ledger)
            receipts = {r["name"]: json.loads(r["result"]) for r in result["final"]["receipts"]}
            bodies = [r["body_bytes"] for r in ledger.rows() if r["type"] == "mock_http_observed"]
            assert result["status"] == "completed" and not queue
            assert len(receipts["list_orders"]["orders"]) == 30
            products = receipts["list_products"]
            assert len(products["products"]) == (8 if mode == "observed_first_page" else 48)
            text = result["steps"][-1]["display"]
            if mode == "observed_first_page":
                assert "订单查询已返回30笔" in text and "查询尚未完成" in text
            else:
                assert products["complete_catalog"] and not products["specifications_complete"]
            reports.append(dict(mode=mode, arm=arm, body_bytes=bodies,
                orders=30, products=len(products["products"]),
                enumeration_complete=products["query_complete"], displayed=text,
                semantic_status="pending_review", paid_requests=0,
                note="Scripted tool choices, not model-autonomous improvement."))
    assert reports[0]["body_bytes"] == reports[1]["body_bytes"]
    assert reports[2]["body_bytes"] == reports[3]["body_bytes"]
    dump(out / "report.json", dict(cases_sha256=file_hash(cases), reports=reports))
    print(json.dumps(reports, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out.resolve())
