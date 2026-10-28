"""Policies for headphones cannot authorize appliance after-sales operations."""
import sqlite3
from unittest.mock import patch

import pytest

from acs.cs_tools import ToolRegistry
from acs.database import BusinessDB
from acs.local_return_tools import FORMAL_FIXTURE_KIND, LocalKnowledge, fixture_settings
from acs.returns import ReturnService, return_policy
from acs.sessions import SessionStore, TaskValidationError
from acs.shop_demo_data import CURATED_SOURCE, LEGACY_VERSION, build_dataset, materialize


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "shop"
    materialize(root)
    settings = fixture_settings(root, kind=FORMAL_FIXTURE_KIND)
    return root, settings, SessionStore(settings.db_path)


def appliance_draft(fixture):
    root, settings, store = fixture
    with sqlite3.connect(root / "business.sqlite") as conn:
        oid, iid = conn.execute("SELECT o.order_id,i.id FROM orders o JOIN order_items i "
            "USING(order_id) WHERE o.user_id='USER001' AND i.product_id LIKE 'E%' "
            "AND o.delivered_at='2026-11-03' ORDER BY o.order_id LIMIT 1").fetchone()
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "为这件家电申请无理由退货")
    store.update_return_task(sid, "USER001", tid, dict(order_id=oid, item_id=iid, quantity=1,
                            reason="no_reason", condition="sealed", secondary_sale_ok=True))
    return settings, store, sid, tid, oid


def test_uncovered_appliance_cannot_get_return_preview(fixture):
    settings, store, sid, tid, _ = appliance_draft(fixture)
    result = ReturnService(settings, store).preview(sid, "USER001", tid)
    assert result["ready"] is False
    assert "缺少适用于此商品类别的售后政策" in " ".join(result["reasons"])
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM return_previews").fetchone()[0] == 0


def test_uncovered_order_dates_cannot_be_return_eligibility(fixture):
    settings, _, _, _, oid = appliance_draft(fixture)
    order = BusinessDB(settings).get_order(oid, "USER001")
    assert order["within_return_window"] is None
    assert order["return_window_days"] is None


def test_appliance_product_exposes_unknown_policy_scope(fixture):
    _, settings, _ = fixture
    product = BusinessDB(settings).get_product("E003")
    assert product["policy_scope"]["return"] is False
    assert product["policy_scope"]["warranty"] is False
    assert product["policy_scope"]["installation"] is False


def test_legacy_preview_cannot_be_reused_after_scope_validation(fixture):
    settings, store, sid, tid, oid = appliance_draft(fixture)
    service = ReturnService(settings, store)
    policy = return_policy(settings)
    category = BusinessDB(settings).get_order(oid, "USER001")["items"][0]["product_category"]
    # Simulate the former bug with the SAME source hash, not a changed policy file.
    with patch("acs.returns.return_policy", return_value=policy | {
        "applicable_categories": [*policy["applicable_categories"], category],
    }):
        old = service.preview(sid, "USER001", tid)
        assert old["ready"] is True
    assert service.latest_preview(sid, "USER001") is None
    with pytest.raises(TaskValidationError, match="缺少适用"):
        service.confirm_and_submit(sid, "USER001", old["preview_id"], tid)
    with store._connect() as conn:
        for table in ("return_confirmations", "return_requests", "return_audit"):
            assert conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0


def test_covered_earphone_still_has_policy_and_missing_scope_fails_closed(fixture):
    root, settings, _ = fixture
    db = BusinessDB(settings)
    assert db.get_order("O72001", "USER001")["within_return_window"] is True
    path = root / "docs/return_policy.md"
    scope = "本政策适用于本店铺全部无线耳机与充电配件类商品。"
    path.write_text(path.read_text().replace(scope, ""))
    assert db.get_order("O72001", "USER001")["within_return_window"] is None


def test_warranty_question_retrieves_applicable_boundary_not_an_invented_term(fixture):
    _, settings, _ = fixture
    registry = ToolRegistry(settings, kb=LocalKnowledge(settings))
    result = registry._search_knowledge(current_user_id="USER001",
        query="燃气热水器用了两个月现在点火有问题，还在保修期吗")
    boundary = [d for d in result["documents"] if d["document_id"] == "appliance_policy_boundary"]
    assert boundary and "不能套用耳机12个月" in boundary[0]["content"]


def test_legacy_demo_remains_byte_rebuildable(tmp_path):
    out = tmp_path / "legacy"
    build_dataset(out, revision=LEGACY_VERSION)
    assert {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()} == {
        str(p.relative_to(CURATED_SOURCE)): p.read_bytes()
        for p in CURATED_SOURCE.rglob("*") if p.is_file()}


def test_native_tool_receipt_and_current_task_agree_on_policy_denial(fixture):
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    from acs.local_submission import FormalReturnPoc

    from .test_langgraph_poc import ScriptedModel, call

    root, _, store = fixture
    with sqlite3.connect(root / "business.sqlite") as conn:
        oid, iid = conn.execute("SELECT o.order_id,i.id FROM orders o JOIN order_items i "
            "USING(order_id) WHERE o.user_id='USER001' AND i.product_id LIKE 'E%' "
            "AND o.delivered_at='2026-11-03' ORDER BY o.order_id LIMIT 1").fetchone()
    args = dict(order_id=oid, item_id=iid, quantity=1, reason="no_reason",
                condition="sealed", secondary_sale_ok=True)
    model = ScriptedModel(responses=[call("update_return_task", args),
                                    AIMessage(content="缺少适用政策，不能生成家电预览。")])
    sid = store.create("USER001")
    with FormalReturnPoc(root, "USER001", model=model) as poc:
        list(poc.stream(sid, "申请这件家电的无理由退货"))
    tool = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
    receipt = json.loads(tool.content)
    assert receipt["result"]["can_submit"] is False
    assert receipt["current_task"]["can_submit"] is False
    assert receipt["current_task"]["policy"]["applicable"] is False
