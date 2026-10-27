"""Seen-data regressions: text/Claim disagreement and bounded catalog enumeration."""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from acs import commerce_benchmark as prior
from acs.commerce_benchmark_v2 import load, run_case
from acs.commerce_benchmark_v2_data import ROOT, build
from acs.config import Settings
from acs.cs_tools import ToolRegistry


def test_every_authored_item_id_matches_its_claim():
    suite = load(ROOT)
    checked = 0
    for task in suite["tasks"]:
        for step in task.steps:
            for answer in step.answers:
                for claim in answer.claims:
                    if claim.fact == "draft.item_id" and claim.value is not None:
                        ids = re.findall(r"(?<!\d)\d+(?!\d)", claim.span)
                        assert str(claim.value) in ids, (task.task_id, answer.text, claim.value)
                        checked += 1
    assert checked > 0


@pytest.mark.parametrize("variant", range(1, 6))
def test_sql_correct_does_not_certify_wrong_item_text(tmp_path, variant):
    suite = load(ROOT)
    tid = f"EC04-{variant}"
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
    suite["contracts"][tid][-1][-1]["text"] = "已改为明细6111并生成有效预览，数量1。"
    result = run_case(suite, entry, tmp_path / "wrong-answer", "v3")
    assert result["sql_correct"] and result["tool_valid"]
    assert result["raw_outcome"] == "fail"
    assert result["answers"][-1]["raw"]["reason"] == "authored_claim_contradicts_authority"


@pytest.fixture
def catalog(tmp_path):
    suite = load(ROOT)
    path = tmp_path / "business.sqlite"
    prior.seed_database(path, suite["business"])
    return ToolRegistry(Settings(_env_file=None, db_path=path)), suite


def test_catalog_has_bounded_pages_and_all_candidates(catalog):
    registry, suite = catalog
    args, seen = {}, []
    for _ in range(40):
        page, _, error = registry.run("list_products", json.dumps(args), current_user_id="USER001")
        assert error is None
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 8000
        assert page["total_count"] == 40
        seen.extend(p["product_id"] for p in page["products"])
        if not page["has_more"]:
            break
        args = page["next_arguments"]
    assert seen == sorted(p["id"] for p in suite["business"]["products"])
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("revision,folder", [
    ("local-commerce-v2", "commerce_benchmark_v2"),
    ("local-commerce-v2.1", "commerce_benchmark_v2_1"),
    ("local-commerce-v2.2", "commerce_benchmark_v2_2"),
])
def test_each_frozen_version_rebuilds_byte_for_byte(tmp_path, revision, folder):
    source = ROOT.parent / folder
    built = tmp_path / "rebuild"
    assert build(built, revision=revision) == load(source)["manifest"]
    assert {str(p.relative_to(built)): p.read_bytes() for p in built.rglob("*") if p.is_file()} == {
        str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }


def test_catalog_filter_last_page_empty_and_complete_details(catalog):
    registry, _ = catalog
    args = {"name_contains": "第5系列", "page_size": 3, "max_price": 300}
    seen = []
    while True:
        page, _, error = registry.run("list_products", json.dumps(args), current_user_id="USER001")
        assert error is None
        assert not page["complete_catalog"]
        for p in page["products"]:
            full = registry.db.get_product(p["product_id"])
            assert p["attributes"] == full["attributes"]
            assert p["current_price"] == full["current_price"] <= 300
            assert [dict(zip(page["sku_fields"], row, strict=True)) for row in p["skus"]] == [
                {k: v for k, v in s.items() if k != "product_id"} for s in full["skus"]]
        seen.extend(p["product_id"] for p in page["products"])
        if not page["has_more"]:
            assert page["next_arguments"] is None
            break
        args = page["next_arguments"]
    expected = [p["product_id"] for p in registry.db.list_products()
                if "第5系列" in p["name"]
                and registry.db.get_product(p["product_id"])["current_price"] <= 300]
    assert len(seen) > 3 and seen == expected
    empty, _, error = registry.run("list_products", '{"name_contains":"不存在型号"}',
                                   current_user_id="USER001")
    assert error is None and empty["total_count"] == 0 and not empty["has_more"]
    assert empty["products"] == [] and empty["query_complete"]
    last, _, error = registry.run("list_products", '{"after_product_id":"ZZZ"}',
                                  current_user_id="USER001")
    assert error is None and last["total_count"] == 40 and last["products"] == []
    assert not last["complete_catalog"] and not last["query_complete"]


@pytest.mark.parametrize("args", [
    {"page_size": 0}, {"page_size": 9}, {"page_size": True}, {"page_size": "8"},
    {"category": 4}, {"max_price": -1}, {"max_price": float("nan")}, {"unknown": 1},
])
def test_catalog_rejects_invalid_arguments(catalog, args):
    from acs.cs_tools import CatalogArgs
    from acs.langgraph_poc import READ_TOOLS
    from acs.local_return_tools import TOOLS

    assert READ_TOOLS["list_products"] is TOOLS["list_products"] is CatalogArgs
    with pytest.raises(ValueError):
        CatalogArgs.model_validate(args)
    result, _, error = catalog[0].run("list_products", json.dumps(args), current_user_id="USER001")
    assert error and "products" not in result


def test_oversized_single_product_is_explicit_and_never_skipped(catalog):
    registry, _ = catalog
    with sqlite3.connect(registry.settings.db_path) as conn:
        conn.execute("UPDATE products SET attributes=? WHERE product_id='B102'", ("长规格" * 4000,))
    first, _, error = registry.run("list_products", "{}", current_user_id="USER001")
    assert error is None and [p["product_id"] for p in first["products"]] == ["B101"]
    blocked, _, _ = registry.run("list_products", json.dumps(first["next_arguments"]),
                                 current_user_id="USER001")
    assert blocked["error_type"] == "catalog_product_too_large"
    assert blocked["has_more"] and blocked["blocked_product_id"] == "B102"
    assert blocked["total_count"] == 40 and not blocked["query_complete"]
    assert blocked["detail_arguments"] == {"product_id": "B102"}
    full, _, error = registry.run("get_product", json.dumps(blocked["detail_arguments"]),
                                  current_user_id="USER001")
    assert error is None and full["attributes"] == ["长规格" * 4000]


def test_broken_annotation_and_unknown_wording_cannot_pass():
    from acs.live_evaluation import assess_answer
    from acs.multiturn_evaluation import AnswerReference, Claim

    wrong = "已改为明细6111并生成有效预览，数量1。"
    ref = AnswerReference(text=wrong, claims=[
        Claim(span=wrong, fact="draft.item_id", value=720011)])
    assert assess_answer(wrong, [ref], {"draft": {"item_id": 720011}})["status"] == (
        "execution_error")
    for text in ["改好了。", "不是明细6111，请再核实。", "商品不是 B101。"]:
        assert assess_answer(text, [ref], {})["status"] == "pending_review"
    wrong_product = "B102为开放式挂耳。"
    ref = AnswerReference(text=wrong_product, claims=[
        Claim(span=wrong_product, fact="product.B103", value="开放式挂耳")])
    assert assess_answer(wrong_product, [ref], {
        "products": {"B103": ["开放式挂耳"]}})["status"] == "execution_error"
    specs = "支持PD3.0/QC4+。"
    ref = AnswerReference(text=specs, claims=[
        Claim(span="支持PD3.0/QC4+", fact="product.P003", value="支持PD3.0/QC4+")])
    assert assess_answer(specs, [ref], {
        "products": {"P003": ["支持PD3.0/QC4+"]}})["status"] == "pass"


def test_historical_execution_still_rejects_changed_source(tmp_path):
    from acs.config import PROJECT_ROOT
    from acs.multiturn_evaluation import load_suite

    from .conftest import load_current_regression_suite

    historical = PROJECT_ROOT / "data/eval/v3_multiturn_m8_v2"
    before = (historical / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="Frozen implementation mismatch"):
        load_suite(historical)
    tasks, _, manifest = load_current_regression_suite(historical)
    assert len(tasks) == 30 and manifest["regression_only"]
    assert (historical / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("arm", ["old", "v3"])
def test_all_five_catalog_pages_fit_original_sdk_budget(tmp_path, monkeypatch, arm):
    from acs.config import PROJECT_ROOT

    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    from audit_commerce_v22 import scan_catalog

    report = scan_catalog(load(ROOT), tmp_path / arm, arms=(arm,))
    row = report["arms"][0]
    assert row["candidate_count"] == 40 and row["max_body_bytes"] < 32768
    assert row["mock_requests"] == 6 and row["real_http_requests"] == 0


@pytest.mark.parametrize("arm", ["old", "v3"])
@pytest.mark.parametrize("boundary", ["body", "budget"])
def test_catalog_detail_overlimit_or_budget_stops_without_completion(tmp_path, monkeypatch,
                                                                  arm, boundary):
    from acs.config import PROJECT_ROOT
    from acs.episode_budget import EpisodeLedger
    from acs.live_evaluation import LIMITS

    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    from eval_live_readiness import model_for

    suite = load(ROOT)
    if boundary == "body":
        suite["business"]["products"][0]["attributes"] = ["完整超长规格" * 8000]
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == "EC03-1")
    root = tmp_path / "sdk"
    (root / ".private-evidence").mkdir(parents=True)
    ledger = EpisodeLedger(root / "fees.jsonl", cap_cny=5,
                           max_requests=1 if boundary == "budget" else 40)
    episode = "EC03-1:" + arm
    ledger.begin(episode, LIMITS)
    queue = [dict(calls=[dict(id="boundary-read", name="get_product" if boundary == "body"
                              else "list_products", args={"product_id": "B101"}
                              if boundary == "body" else {})]),
             dict(text="错误地声称已经完成，不应送出此请求。")]
    with model_for(root, ledger, episode, queue=queue) as model:
        result = run_case(suite, entry, root / "fixture", arm, model=model,
                          ledger=ledger, limits=LIMITS)
    assert result["global_stop"] and result["raw_outcome"] != "pass"
    assert result["final"]["draft"] is None and result["final"]["requests"] == 0
    assert ledger.summary()["requests"] == 1 and len(queue) == 1
    assert "错误地声称" not in str([s["display"] for s in result["steps"]])
    expected = "protocol_failure" if boundary == "body" else "budget_deadline_abort"
    assert result["failure_class"] == expected
