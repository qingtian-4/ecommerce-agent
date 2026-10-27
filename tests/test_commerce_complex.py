"""Frozen multi-intent evidence, independent omissions and literal entity normalization."""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from acs import commerce_benchmark as prior
from acs import multiturn_evaluation as m8
from acs.commerce_benchmark_v2 import run_case
from acs.commerce_complex_data import ROOT, build, load
from acs.commerce_complex_eval import evidence_present, grade_objectives
from acs.config import Settings
from acs.cs_tools import ToolRegistry
from acs.live_evaluation import LIMITS, assess_answer


@pytest.fixture(scope="module")
def suite():
    return load()


@pytest.fixture
def registry(tmp_path, suite):
    path = tmp_path / "business.sqlite"
    prior.seed_database(path, suite["business"])
    return ToolRegistry(Settings(_env_file=None, db_path=path))


@pytest.mark.parametrize("case_id", ["compact", "lowercase", "fullwidth", "series-compact",
                                    "series-fullwidth", "spaces"])
def test_product_typography_retains_every_candidate(registry, suite, case_id):
    case = next(c for c in suite["entity_probes"] if c["id"] == case_id)
    payload, _, error = registry.run("list_products", json.dumps({"name_contains": case["query"]}),
                                     current_user_id="USER001")
    assert error is None
    assert [p["product_id"] for p in payload["products"]] == case["expected_ids"]
    assert payload["total_count"] == len(case["expected_ids"])


def test_normalization_does_not_invent_aliases_or_parse_negation(registry):
    for query in ["晴岚A60", "不是晴岚A6", "晴岚 A-6", "A6退款"]:
        payload, _, error = registry.run("list_products", json.dumps({"name_contains": query}),
                                         current_user_id="USER001")
        assert error is None and payload["total_count"] == 0 and payload["query_complete"]


def test_frozen_cases_rebuild_and_environment_is_reused(tmp_path, suite):
    target = tmp_path / "rebuilt"
    assert build(target) == suite["manifest"]
    assert all(p.read_bytes() == (target / p.name).read_bytes() for p in ROOT.iterdir())
    assert len(suite["business"]["products"]) == 40
    assert len(suite["business"]["orders"]) == 240
    assert sum(len(t.steps) for t in suite["tasks"]) == 24
    for entry in suite["entries"]:
        for gold in entry["intent_gold"]:
            ids = [g["id"] for g in gold["goals"]]
            assert len(ids) >= 2 and len(ids) == len(set(ids))
    (target / "cases.json").write_text("[]")
    with pytest.raises(ValueError, match="fingerprint"):
        load(target)


def test_factual_partial_answer_is_not_full_intent_success(tmp_path, suite):
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == "CX03")
    result = run_case(suite, entry, tmp_path / "runtime", "v3", limits=LIMITS)
    assert result["sql_correct"] and result["tool_valid"] and result["raw_outcome"] == "pass"
    assert grade_objectives(entry, result)["raw_outcome"] == "pass"
    gold = entry["intent_gold"][0]
    text = next(t for t, covered in gold["answer_coverage"].items()
                if len(covered) < len(gold["goals"]))
    task = m8.Task.model_validate(entry["task"])
    with patch.object(m8, "claim_value", prior.claim_value):
        assessment = assess_answer(text, task.steps[0].answers, result["steps"][0]["state"])
        assert assessment["status"] == "pass"
    changed = copy.deepcopy(result)
    changed["steps"][0]["raw"] = text
    grade = grade_objectives(entry, changed)
    assert grade["raw_outcome"] == "fail" and grade["task_success"] is False
    changed["steps"][0]["raw"] = "两款各有特点，我已逐项核对。"
    assert grade_objectives(entry, changed)["raw_outcome"] == "pending_review"
    assert grade_objectives(entry, changed)["task_success"] is None


def test_evidence_missing_despite_sql_truth_is_not_success(tmp_path, suite):
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == "CX03")
    result = run_case(suite, entry, tmp_path / "runtime", "old", limits=LIMITS)
    assert grade_objectives(entry, result)["raw_outcome"] == "pass"
    for step in result["steps"]:
        step["state"]["receipts"] = [r for r in step["state"]["receipts"]
                                      if json.loads(r["arguments"]).get("product_id") != "B106"]
    assert result["sql_correct"] and grade_objectives(entry, result)["raw_outcome"] == "fail"


def test_legitimate_catalog_and_history_evidence_and_query_binding():
    payload = dict(product_id="B105", name="充电器C80", attributes=["80W"],
                   current_price=139, found=True)
    receipt = dict(name="get_product", arguments='{"product_id":"B105"}',
                   result=json.dumps(payload), error=None)
    record = dict(state=dict(receipts=[receipt], draft=None, requests=0))
    goal = dict(evidence=dict(kind="product", product_id="B105", attribute="80W"))
    assert evidence_present(goal, record, [], {})  # Static facts can be reused.
    price_goal = dict(evidence=dict(kind="product", product_id="B105", current_price=139))
    assert not evidence_present(price_goal, record, [], {})  # Current price needs current receipt.
    page = dict(name="list_products", arguments="{}", result=json.dumps({"products": [payload]}),
                error=None)
    assert evidence_present(goal, record, [page], {})
    history = receipt | {"name": "get_price_history"}
    assert evidence_present(price_goal, record, [history], {})
    assert not evidence_present(dict(evidence=dict(kind="candidates", ids=["B105"])),
                                record, [history], {})
    order = dict(name="get_order", arguments='{"order_id":"O72001"}', error=None,
                 result=json.dumps({"found": True, "items": [payload]}))
    assert evidence_present(price_goal, record, [order], {})
    policy = dict(name="get_return_task", arguments="{}", error=None, result=json.dumps({
        "policy": {"source": "return_policy.md", "shipping_payer": "买家"}}))
    assert evidence_present(dict(evidence=dict(kind="policy", document_id="return_policy",
                                               contains="买家")), record, [policy], {})
    no_match = dict(evidence=dict(kind="no_match", query="晴岚A60"))
    empty = dict(name="list_products", arguments='{"name_contains":"错误查询"}', error=None,
                 result='{"total_count":0,"query_complete":true}')
    assert not evidence_present(no_match, record, [empty], {})
    empty["arguments"] = '{"name_contains":"晴岚A60"}'
    assert evidence_present(no_match, record, [empty], {})
    empty["arguments"] = '{"name_contains":"晴岚A60","max_price":0}'
    assert not evidence_present(no_match, record, [empty], {})
    record["state"]["products"] = {"B105": ["80W"]}
    assert evidence_present(no_match, record, [page], {})
    record["state"]["products"]["B106"] = ["35W"]
    assert not evidence_present(no_match, record, [page], {})


@pytest.mark.parametrize("tid", ["CX01", "CX02", "CX04", "CX07", "CX08", "CX10", "CX12"])
def test_complex_runtime_keeps_all_goals_and_permissions(tmp_path, suite, tid):
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
    result = run_case(suite, entry, tmp_path / tid, "v3", limits=LIMITS)
    assert result["sql_correct"] and result["tool_valid"]
    assert grade_objectives(entry, result)["raw_outcome"] == "pass"
    assert result["final"]["requests"] == 0


def test_paid_proposal_is_bounded_and_does_not_authorize_execution(tmp_path, monkeypatch):
    from argparse import Namespace

    from acs.config import PROJECT_ROOT

    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    import eval_complex_pilot as pilot

    p = pilot.proposal()
    assert p["authorized"] is False and p["max_planned_reservation_cny"] == "4.718592"
    assert p["max_requests"] == 32 and p["cap_cny"] == 5 and p["episodes"] == 4
    monkeypatch.setattr(pilot, "Settings", lambda **kw: pytest.fail("must not read credentials"))
    with pytest.raises(ValueError, match="Fresh explicit"):
        pilot.run(Namespace(mode="live", authorization=None, out=tmp_path / "blocked"))
    assert not (tmp_path / "blocked").exists()


@pytest.mark.parametrize("field,value", [
    ("source_sha", "old"), ("dataset_manifest_sha256", "changed"),
    ("environment_manifest_sha256", "changed"), ("max_requests", 33), ("cap_cny", 6),
    ("body_bytes", 65536), ("output_tokens", 2048), ("requests_per_episode", 9),
    ("authorized", False), ("task_ids", ["CX01"]),
])
def test_paid_authorization_cannot_expand_frozen_scope(tmp_path, monkeypatch, field, value):
    from acs.config import PROJECT_ROOT

    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    import eval_complex_pilot as pilot

    a = pilot.proposal() | dict(authorized=True, user_instruction_reference="unit-test-only",
                                run_id="unit", source_sha="current",
                                output_dir=str(tmp_path / "out"),
                                expires_at="2099-01-01T00:00:00+00:00")
    a[field] = value
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(a))
    with pytest.raises(ValueError, match="Authorization"):
        pilot.permit(path, tmp_path / "out", "current")
    assert not (tmp_path / "out").exists()
