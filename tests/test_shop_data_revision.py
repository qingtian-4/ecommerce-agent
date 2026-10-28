"""Data defects reproduce on v1.1; revised fixtures must satisfy the same checks."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings
from acs.return_rules import policy_scope
from acs.shop_demo_data import REVISED_VERSION, build_dataset, load_dataset, materialize

DATASET = Path(os.environ.get("ACS_DATA_AUDIT_DATASET", "data/demo/shop_realism_v1_2"))


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("revised") / "runtime"
    materialize(root, DATASET)
    return root


def test_unstated_anc_is_unknown_not_negative(fixture):
    with closing(sqlite3.connect(fixture / "business.sqlite")) as db:
        for series in range(1, 6):
            for suffix in (2, 4):
                values = db.execute("SELECT anc FROM product_skus WHERE product_id=?",
                                    (f"B{series}0{suffix}",)).fetchall()
                assert values and all(v[0] is None for v in values)
        assert db.execute("SELECT anc FROM product_skus WHERE product_id='B101'").fetchone()[0] == 1
        assert db.execute("SELECT anc FROM product_skus WHERE product_id='B103'").fetchone()[0] == 0


def test_logistics_are_varied_and_temporally_valid(fixture):
    clock = datetime.fromisoformat(json.loads((fixture / "demo_data.json").read_text())["clock"])
    routes, intervals = set(), set()
    with closing(sqlite3.connect(fixture / "business.sqlite")) as db:
        for status, shipped, delivered, raw in db.execute(
            "SELECT o.status,o.shipped_at,o.delivered_at,l.trace_json "
            "FROM orders o LEFT JOIN logistics l USING(order_id)"
        ):
            if status in {"pending", "cancelled"}:
                assert raw is None
                continue
            trace = json.loads(raw)
            times = [datetime.fromisoformat(e["time"]) for e in trace]
            assert all(t.tzinfo is not None for t in times)
            assert times == sorted(set(times)) and times[-1] <= clock
            assert times[0].date().isoformat() == shipped
            assert (trace[-1]["status"] == "已签收") == (status == "delivered")
            if delivered:
                assert times[-1].date().isoformat() == delivered
            routes.add(tuple(e["location"] for e in trace))
            intervals.add(tuple((b-a).total_seconds() for a, b in
                               zip(times, times[1:], strict=False)))
    assert len(routes) >= 4 and len(intervals) > 50


def test_every_product_has_an_order_and_policy_stays_bounded(fixture):
    with closing(sqlite3.connect(fixture / "business.sqlite")) as db:
        assert not db.execute("SELECT product_id FROM products EXCEPT "
                              "SELECT product_id FROM order_items").fetchall()
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        with pytest.raises(sqlite3.Error):
            db.execute("UPDATE logistics SET status='delivered'")
    settings = fixture_settings(fixture, kind=FORMAL_FIXTURE_KIND)
    assert policy_scope(settings, "无线耳机")["return"]
    assert not any(policy_scope(settings, "燃气热水器").values())
    assert not (fixture / "docs/appliance_policy_draft.md").exists()


def test_revised_bytes_rebuild_and_tampering_is_rejected(tmp_path):
    rebuilt = tmp_path / "rebuild"
    build_dataset(rebuilt, revision=REVISED_VERSION)
    assert {str(p.relative_to(rebuilt)): p.read_bytes() for p in rebuilt.rglob("*")
            if p.is_file()} == {str(p.relative_to(DATASET)): p.read_bytes()
                               for p in DATASET.rglob("*") if p.is_file()}
    load_dataset(rebuilt)
    (rebuilt / "drafts/appliance_policy.md").write_text("unauthorized activation")
    with pytest.raises(ValueError, match="fingerprint"):
        load_dataset(rebuilt)


def test_sql_specs_prices_and_sku_facts_match_the_frozen_documents(fixture):
    from acs.cs_tools import ToolRegistry

    data = json.loads((DATASET / "business.json").read_text())
    registry = ToolRegistry(fixture_settings(fixture, kind=FORMAL_FIXTURE_KIND))
    all_docs = "\n".join(p.read_text() for p in (DATASET / "docs").glob("*.md"))
    for product in data["products"]:
        actual = registry.db.get_product(product["id"])
        assert actual["current_price"] == product["current_price"]
        assert actual["name"] == product["name"]
        assert all(a in actual["attributes"] and a in all_docs for a in product["attributes"])
    assert registry.db.get_order("O72031", "USER001")["found"] is False


def test_purchase_price_matches_recorded_price_on_order_date(fixture):
    # This fixture has no coupon or promotion table: a discount cannot be invented.
    with closing(sqlite3.connect(fixture / "business.sqlite")) as db:
        for oid, pid, purchased, created in db.execute(
            "SELECT o.order_id,i.product_id,i.purchase_price,o.created_at "
            "FROM orders o JOIN order_items i USING(order_id)"
        ).fetchall():
            price = db.execute("SELECT price FROM price_history WHERE product_id=? "
                "AND changed_at<=? ORDER BY changed_at DESC LIMIT 1", (pid, created)).fetchone()
            assert price and price[0] == purchased, (oid, pid, purchased, price)
