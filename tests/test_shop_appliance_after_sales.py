"""v1.4 synthetic appliance after-sales policy is actually usable."""

from __future__ import annotations

import sqlite3

import pytest

from acs.config import Settings
from acs.database import BusinessDB
from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings
from acs.returns import ReturnService
from acs.sessions import SessionStore
from acs.shop_demo_data import (
    AFTER_SALES_SOURCE,
    CONSULTATION_SOURCE,
    load_dataset,
    materialize,
)


@pytest.fixture(scope="module")
def store_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("shop-v14") / "shop"
    materialize(root, AFTER_SALES_SOURCE)
    return root


@pytest.fixture()
def settings(store_root):
    return fixture_settings(store_root, kind=FORMAL_FIXTURE_KIND)


def test_v14_manifest_and_all_appliance_categories_are_covered(settings):
    assert load_dataset(AFTER_SALES_SOURCE)["manifest"]["version"] == "shop-realism-v1.4"
    from acs.return_rules import policy_scope

    for category in (
        "电热水器",
        "燃气热水器",
        "厨房热水器",
        "洗碗机",
        "油烟机",
        "净饮机",
        "燃气灶",
        "冰箱",
    ):
        scope = policy_scope(settings, category)
        assert scope["return"] is True, category
        assert scope["warranty"] is True, category


def test_v13_remains_consultation_only():
    from acs.return_rules import policy_scope

    old = Settings(_env_file=None, docs_dir=CONSULTATION_SOURCE / "docs")
    scope = policy_scope(old, "电热水器")
    assert scope["return"] is False
    assert scope["warranty"] is False
    assert scope["consultation"] is True


def test_appliance_can_get_a_preview_not_an_unsupported_notice(store_root, settings):
    store = SessionStore(settings.db_path)
    with sqlite3.connect(store_root / "business.sqlite") as conn:
        row = conn.execute(
            "SELECT o.order_id, i.id FROM orders o JOIN order_items i USING(order_id) "
            "JOIN products p ON p.product_id=i.product_id "
            "WHERE o.user_id='USER001' AND p.category='电热水器' "
            "AND o.delivered_at IS NOT NULL ORDER BY o.delivered_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    oid, iid = row
    order = BusinessDB(settings).get_order(oid, "USER001")
    assert order["within_return_window"] is True
    assert order["return_window_days"] == 7

    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "为这件家电申请无理由退货")
    store.update_return_task(
        sid,
        "USER001",
        tid,
        dict(
            order_id=oid,
            item_id=iid,
            quantity=1,
            reason="no_reason",
            condition="sealed",
            secondary_sale_ok=True,
        ),
    )
    preview = ReturnService(settings, store).preview(sid, "USER001", tid)
    assert preview["ready"] is True
    assert not any("缺少适用" in reason for reason in preview.get("reasons", []))
