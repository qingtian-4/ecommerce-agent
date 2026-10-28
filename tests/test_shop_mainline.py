import copy
import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest

from acs.config import PROJECT_ROOT
from acs.cs_tools import CATALOG_CONTRACT
from acs.shop_mainline_data import ROOT, build, load
from acs.shop_mainline_eval import evidence_present, grade, run


def test_cases_rebuild_and_environment_is_exact(tmp_path):
    suite = load()
    out = tmp_path / "rebuilt"
    build(out)
    assert all(p.read_bytes() == (out / p.name).read_bytes() for p in ROOT.iterdir())
    assert len(suite["business"]["products"]) == 64
    assert len(suite["business"]["orders"]) == 304
    assert len(suite["entries"]) == 8


@pytest.mark.parametrize("tid", ["SX02", "SX03", "SX04", "SX05"])
def test_mixed_goals_and_original_draft_are_preserved(tmp_path, tid):
    suite = load()
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
    result = run(suite, entry, tmp_path / "case", "v3")
    assert result["sql_correct"] and result["tool_valid"]
    assert result["raw_outcome"] == "pass"
    assert result["final"]["requests"] == 0
    if tid == "SX04":
        assert result["final"]["draft"]["order_id"] == "O72001"
        assert result["final"]["draft"]["item_id"] == 720010
    gold = entry["intent_gold"][0]
    partial = next(t for t, ids in gold["answer_coverage"].items()
                   if len(ids) < len(gold["goals"]))
    changed = copy.deepcopy(result)
    changed["steps"][0]["raw"] = partial
    assert grade(entry, changed)["raw_outcome"] == "fail"
    changed["steps"][0]["raw"] = "我已经把您问到的事项都核实过了。"
    assert grade(entry, changed)["raw_outcome"] == "pending_review"


def test_no_tool_cannot_certify_access_denial_and_wrong_policy_cannot_certify_terms():
    state = dict(state=dict(receipts=[], draft=None, requests=0))
    refusal = dict(evidence=dict(kind="refusal", order_id="O72031"))
    assert evidence_present(refusal, state, [], {}) is False
    receipt = dict(name="get_order", error=None, arguments='{"order_id":"O72031"}',
                   result='{"order_id":"O72031","found":false,"error_type":"forbidden"}')
    assert evidence_present(refusal, state, [receipt], {})
    policy = dict(evidence=dict(kind="policy", document_id="appliance_consultation",
                               contains="12个月"))
    receipt = dict(name="search_knowledge", error=None, arguments="{}",
        result=json.dumps(dict(documents=[dict(document_id="warranty_policy", content="12个月")])))
    assert not evidence_present(policy, state, [receipt], {})
    receipt["result"] = json.dumps(dict(documents=[dict(
        document_id="appliance_consultation", content="模拟整机保修12个月")]))
    assert evidence_present(policy, state, [receipt], {})
    # An explicit product document is a legitimate alternative to get_product.
    spec = dict(evidence=dict(kind="product", product_id="E008", attribute="净重77kg"))
    receipt["result"] = json.dumps(dict(documents=[dict(document_id="E008", content="净重77kg")]))
    state["state"]["receipts"] = [receipt]
    assert evidence_present(spec, state, [receipt], {})
    receipt["result"] = json.dumps(dict(documents=[dict(document_id="E007", content="净重77kg")]))
    assert not evidence_present(spec, state, [receipt], {})
    candidate = dict(evidence=dict(kind="exact_candidates", ids=["E003"],
                                   arguments=dict(category="燃气热水器", max_price=1000)))
    receipt = dict(name="list_products", arguments='{"view":"overview"}', error=None,
        result=json.dumps(dict(contract=CATALOG_CONTRACT, total_count=2, has_more=False,
            products=[dict(product_id="E003", category="燃气热水器", current_price=939),
                      dict(product_id="E001", category="电热水器", current_price=1999)])))
    assert evidence_present(candidate, state, [receipt], {})
    receipt["arguments"] = '{"max_price":950}'
    assert not evidence_present(candidate, state, [receipt], {})


def test_live_requires_new_budget_before_any_credentials(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    import eval_shop_mainline as pilot

    plan = pilot.proposal()
    assert plan["authorized"] is False and plan["max_planned_reservation_cny"] == "4.718592"
    with pytest.raises(ValueError, match="Fresh explicit"):
        pilot.execute(Namespace(mode="live", authorization=None, out=tmp_path / "blocked"))
    assert not (tmp_path / "blocked").exists()


@pytest.mark.parametrize("field,value", [("max_requests", 33), ("cap_cny", 6),
    ("body_bytes", 65536), ("task_ids", ["SX08"]), ("environment_manifest_sha256", "wrong")])
def test_paid_scope_cannot_grow(tmp_path, monkeypatch, field, value):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "scripts"))
    import eval_shop_mainline as pilot

    auth = pilot.proposal() | dict(authorized=True, source_sha="current", run_id="unit",
        user_instruction_reference="unit-only", output_dir=str(tmp_path / "out"),
        expires_at=(datetime.now(UTC)+timedelta(hours=1)).isoformat())
    auth[field] = value
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(auth))
    with pytest.raises(ValueError, match="Authorization"):
        pilot.permit(path, tmp_path / "out", "current")
