"""Expanded data correctness, real paired tools and independent scorer controls."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from acs import commerce_benchmark as prior
from acs.commerce_benchmark_v2 import BoundedUser, load, run_case
from acs.commerce_benchmark_v2_data import ROOT, build
from acs.commerce_retrieval_comparison import metrics
from acs.rag import Chunk


@pytest.fixture(scope="module")
def suite():
    return load(ROOT)


def test_byte_reproducible_frozen_data_and_no_overwrite(tmp_path, suite):
    assert build(tmp_path / "rebuilt") == suite["manifest"]
    with pytest.raises(FileExistsError):
        build(tmp_path / "rebuilt")


def test_counts_and_family_document_split_no_leak(suite):
    assert len(suite["business"]["products"]) == 40
    assert len(suite["business"]["orders"]) == 240
    assert len(list((ROOT / "docs").glob("*.md"))) == 30
    assert len(suite["tasks"]) == 60 and len(suite["rag"]["cases"]) == 120
    dev = {e["family"] for e in suite["entries"] if e["task"]["split"] == "development"}
    test = {e["family"] for e in suite["entries"] if e["task"]["split"] == "validation"}
    assert len(dev) == 4 and len(test) == 8 and not dev & test
    groups = {
        split: {q["isolation_group"] for q in suite["rag"]["cases"] if q["split"] == split}
        for split in ["development", "validation"]
    }
    assert not groups["development"] & groups["validation"]
    assert sum(t.cohort == "common" for t in suite["tasks"]) == 50


def test_actual_sql_has_all_entities_amounts_foreign_keys_and_original_barriers(tmp_path, suite):
    prior.materialize(tmp_path / "fixture", suite)
    with sqlite3.connect(tmp_path / "fixture/business.sqlite") as c:
        assert c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 40
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 240
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert not c.execute(
            "SELECT o.order_id FROM orders o JOIN order_items i "
            "ON o.order_id=i.order_id GROUP BY o.order_id "
            "HAVING o.total_amount!=SUM(i.quantity*i.purchase_price)"
        ).fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="trusted"):
            c.execute("UPDATE orders SET status='shipped'")


@pytest.mark.parametrize(
    "tid,arm",
    [
        ("EC01-5", "old"),
        ("EC01-5", "v3"),
        ("EC05-3", "old"),
        ("EC05-3", "v3"),
        ("EC09-4", "v3"),
        ("EC14-5", "v3"),
    ],
)
def test_real_paired_runtime_sql_claims_and_recovery(tmp_path, suite, tid, arm):
    e = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
    r = run_case(suite, e, tmp_path / (tid + "-" + arm), arm)
    assert r["error"] is None, r["error"]
    assert r["sql_correct"] and r["tool_valid"] and r["raw_outcome"] == "pass"
    assert r["model_http_requests"] == 0 and r["input_tokens"] is None
    if tid.startswith("EC14"):
        assert r["final"]["requests"] == 1
        assert any(s["recovery"] is True for s in r["steps"])


def test_misleading_goal_word_does_not_replace_sql_truth(tmp_path, suite):
    e = copy.deepcopy(next(e for e in suite["entries"] if e["task"]["task_id"] == "EC03-1"))
    e["task"]["steps"][-1]["expect"]["draft"]["quantity"] = 2
    r = run_case(suite, e, tmp_path / "wrong-gold-control", "v3")
    assert not r["sql_correct"] and r["raw_outcome"] == "fail"
    # This is an explicit evaluator negative control, never a replacement frozen task.
    assert (
        next(e for e in suite["entries"] if e["task"]["task_id"] == "EC03-1")["task"]["steps"][-1][
            "expect"
        ]["draft"]["quantity"]
        == 1
    )


def test_unknown_natural_answer_stays_pending_not_automatic_success(suite):
    from acs.live_evaluation import assess_answer

    t = suite["tasks"][0]
    assert (
        assess_answer("我会先认真核实您的情况。", t.steps[0].answers, {})["status"]
        == "pending_review"
    )


def test_dynamic_user_reacts_only_to_public_observation_and_has_bound():
    u = BoundedUser("O72001", max_turns=4)
    assert "先列出" in u.next("")
    assert "不要把另一笔" in u.next("您选择哪一笔？")
    assert "O72001" in u.next("O72001和O72002都有同款耳机。")
    assert u.next("O72001已签收。") is None and u.done
    v = BoundedUser("O72001", max_turns=2)
    assert v.next("") and v.next("没有找到匹配信息。")
    assert v.next("还需要帮助吗？") is None and not v.done
    assert set(vars(v)) == {"order_id", "max_turns", "turns", "selected", "done"}


def test_rag_oracle_missing_evidence_and_unknown_controls():
    # No embedding fake is used; these are grading inputs, not retrieval quality evidence.
    a = Chunk(chunk_id="d#1", document_id="d", title="d", category="product", content="事实甲")
    b = Chunk(chunk_id="e#1", document_id="e", title="e", category="product", content="事实乙")
    case = {"evidence_groups": [["d#1"], ["e#1"]]}
    assert metrics(case, [a, b])["3"]["all_evidence"]
    assert metrics(case, [a])["3"]["recall"] == 0.5
    assert metrics(case, [b, a])["document_recall_at3"] == 1
    assert metrics({"evidence_groups": []}, [a])["unsupported_context_exposed"]


def test_old_files_not_modified_by_loading_and_gold_not_in_user_prompts(suite):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest == suite["manifest"]
    for t in suite["tasks"]:
        assert all("expect" not in s.text and "evidence_groups" not in s.text for s in t.steps)


def test_user_item_ids_refer_to_actual_order_lines(suite):
    import re

    valid = {str(i["id"]) for o in suite["business"]["orders"] for i in o["items"]}
    seen = []
    for t in suite["tasks"]:
        for s in t.steps:
            seen.extend(re.findall(r"明细\s*(\d+)", s.text))
    assert seen and set(seen) <= valid


@pytest.mark.parametrize("arm", ["old", "v3"])
def test_live_catalog_badcase_recovers_below_original_body_limit(tmp_path, suite, monkeypatch, arm):
    """Replay the observed alternative tool choice, not a second paid attempt."""
    from acs.config import PROJECT_ROOT
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS

    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    from eval_live_readiness import model_for

    root = tmp_path / "sdk"
    root.mkdir()
    (root / ".private-evidence").mkdir()
    ledger = EpisodeLedger(root / "fees.jsonl", cap_cny=5, max_requests=40)
    episode = "EC03-1:" + arm
    ledger.begin(episode, LIMITS)
    queue = [
        dict(
            calls=[
                dict(id="observed-order", name="get_order", args=dict(order_id="O72001")),
                dict(id="observed-catalog", name="list_products", args={}),
            ]
        ),
    ]
    queue.extend(copy.deepcopy(d) for turn in suite["contracts"]["EC03-1"] for d in turn)
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == "EC03-1")
    with model_for(root, ledger, episode, queue=queue) as model:
        r = run_case(
            suite, entry, root / "fixture", arm, model=model, ledger=ledger, limits=LIMITS
        )
    assert not r["global_stop"] and r["failure_class"] is None, r["error"]
    assert r["sql_correct"] and r["tool_valid"] and r["raw_outcome"] == "pass"
    assert not queue
    refused = [x for x in ledger.rows() if x["type"] == "preflight_rejected"]
    assert not refused
    assert max(x["body_bytes"] for x in ledger.rows()
               if x["type"] == "mock_http_observed") < 32768
    assert r["final"]["draft"]["item_id"] == 720010 and r["final"]["requests"] == 0
    page = next(json.loads(c["result"]) for c in r["final"]["receipts"]
                if c["name"] == "list_products")
    assert page["total_count"] == 40 and page["has_more"] and page["next_arguments"]


def test_live_key_preflight_has_no_episode_or_ledger_side_effect(tmp_path, monkeypatch):
    from argparse import Namespace

    from acs.config import PROJECT_ROOT

    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    import eval_commerce_pilot as pilot

    env = tmp_path / "empty.env"
    env.write_text("ACS_LLM_API_KEY=\n")
    monkeypatch.setenv("ACS_LLM_API_KEY", "")
    monkeypatch.setattr(pilot, "permit", lambda *args: dict(cap_cny=5, max_requests=40))
    args = Namespace(
        mode="live",
        authorization=tmp_path / "auth.json",
        price_review=tmp_path / "price.json",
        env_file=env,
        out=tmp_path / "must-not-start",
    )
    with pytest.raises(ValueError, match="missing in preflight"):
        pilot.run(args)
    assert not args.out.exists()
