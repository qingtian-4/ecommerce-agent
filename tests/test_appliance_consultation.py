"""Consultation activation must never authorize an appliance submission."""
import json
import sqlite3

import pytest

from acs.cs_tools import ToolRegistry
from acs.database import BusinessDB
from acs.local_return_tools import FORMAL_FIXTURE_KIND, LocalKnowledge, fixture_settings
from acs.return_rules import consultation_policy
from acs.returns import ReturnService
from acs.sessions import SessionStore
from acs.shop_demo_data import CONSULTATION_SOURCE, CONSULTATION_VERSION, build_dataset, materialize


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "shop"
    materialize(root, CONSULTATION_SOURCE)
    return root, fixture_settings(root, kind=FORMAL_FIXTURE_KIND)


def test_consultation_is_available_without_operational_eligibility(fixture):
    _, settings = fixture
    db = BusinessDB(settings)
    product = db.get_product("E003")
    assert product["policy_scope"].get("consultation") is True
    assert product["policy_scope"]["return"] is False
    assert "仅支持模拟政策咨询" in product["policy_notice"]
    order = db.get_order("O72005", "USER001")
    assert order["within_return_window"] is None
    assert "仅支持模拟政策咨询" in order["policy_notice"]


def test_existing_preview_and_confirmation_cannot_enable_appliances(fixture):
    root, settings = fixture
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "直接给热水器退货，我确认")
    store.update_return_task(sid, "USER001", tid, dict(order_id="O72005", item_id=720050,
        quantity=1, reason="no_reason", condition="sealed", secondary_sale_ok=True))
    preview = ReturnService(settings, store).preview(sid, "USER001", tid)
    assert preview["ready"] is False
    with sqlite3.connect(root / "business.sqlite") as db:
        for table in ("return_previews", "return_confirmations", "return_requests"):
            assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.parametrize("category", ["电热水器", "厨房热水器", "燃气热水器", "洗碗机",
                                     "油烟机", "净饮机", "燃气灶", "冰箱"])
def test_scoped_consultation_evidence_is_retrievable(fixture, category):
    _, settings = fixture
    registry = ToolRegistry(settings, kb=LocalKnowledge(settings))
    result = registry._search_knowledge(current_user_id="USER001",
        query=f"{category}模拟保修期限和退货条件", document_id="appliance_consultation")
    assert result["found"] and result["retrieval_mode"] == "bm25_scoped"
    assert all(c["document_id"] == "appliance_consultation" for c in result["documents"])
    assert any("12个月" in c["content"] and "不支持家电" in c["content"]
               for c in result["documents"])


def test_bad_source_does_not_fallback_to_unrelated_policy(fixture):
    root, settings = fixture
    registry = ToolRegistry(settings, kb=LocalKnowledge(settings))
    result = registry._search_knowledge(current_user_id="USER001", query="保修12个月",
                                        document_id="../../drafts/appliance_policy")
    assert not result["found"] and result["documents"] == []
    assert consultation_policy(settings, "燃气热水器")
    assert consultation_policy(settings, "无线耳机") is None
    path = root / "docs/appliance_consultation.json"
    value = json.loads(path.read_text())
    value["application_enabled"] = True
    path.write_text(json.dumps(value))
    assert consultation_policy(settings, "燃气热水器") is None


def test_policy_version_rebuild_preserves_business_data(tmp_path):
    out = tmp_path / "rebuilt"
    build_dataset(out, revision=CONSULTATION_VERSION)
    assert {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()} == {
        str(p.relative_to(CONSULTATION_SOURCE)): p.read_bytes()
        for p in CONSULTATION_SOURCE.rglob("*") if p.is_file()}
    policy = json.loads((out / "docs/appliance_consultation.json").read_text())
    assert policy["application_enabled"] is False
