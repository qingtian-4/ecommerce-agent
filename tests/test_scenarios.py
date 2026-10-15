"""Scenario growth must not overwrite history or manufacture SKU facts."""

import json
import sqlite3
from contextlib import closing

import pytest

from acs.database import BusinessDB
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings
from acs.sessions import SessionStore


def test_v2_is_reproducible_isolated_and_business_facts_match(settings, tmp_path):
    before = settings.db_path.read_bytes()
    a, b = tmp_path / "a", tmp_path / "b"
    first, second = build_scenario(a), build_scenario(b)
    assert first["tables_sha256"] == second["tables_sha256"]
    assert first["files_sha256"] == second["files_sha256"]
    assert first["counts"]["products"] == 12
    assert first["counts"]["product_skus"] == 24
    assert first["counts"]["orders"] == 60
    assert first["initial_pending_example"]["reserved"] == 1
    assert settings.db_path.read_bytes() == before
    profile = scenario_settings(settings, a)
    assert profile.knowledge_mode == "documents"
    assert profile.reference_date.isoformat() == "2026-10-15"
    db = BusinessDB(profile)
    assert len(db.list_orders("USER001")) == 20
    assert db.get_product("V206")["skus"][1]["availability"] == "unavailable"
    assert db.get_product("V211")["skus"][1]["availability"] == "unknown"
    with closing(sqlite3.connect(profile.db_path)) as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        for oid, uid, created, delivered in conn.execute(
            "SELECT order_id,user_id,created_at,delivered_at FROM orders",
        ):
            order = db.get_order(oid, uid)
            assert all(i["sku"]["product_id"] == i["product_id"] for i in order["items"])
            assert sum(i["quantity"] * i["purchase_price"] for i in order["items"]) == order[
                "total_amount"
            ]
            for item in order["items"]:
                price = conn.execute("SELECT price FROM price_history WHERE product_id=? "
                                     "AND changed_at<=? ORDER BY changed_at DESC LIMIT 1",
                                     (item["product_id"], created)).fetchone()[0]
                assert item["purchase_price"] == price
            if delivered:
                assert created <= delivered <= order["today"]
                assert db.get_logistics(oid, uid)["updated_at"] == delivered
        ages = {r[0] for r in conn.execute("SELECT CAST(julianday('2026-10-15')-"
                                           "julianday(delivered_at) AS INTEGER) FROM orders "
                                           "WHERE delivered_at IS NOT NULL")}
        assert {7, 8, 15, 16} <= ages
    with pytest.raises(FileExistsError):
        build_scenario(a)
    original = data_fingerprint(profile.db_path)
    store = SessionStore(profile.db_path)
    store.create("USER001")
    assert scenario_settings(settings, a).db_path == profile.db_path
    with closing(sqlite3.connect(profile.db_path)) as conn, conn:
        conn.execute("UPDATE product_skus SET color='被改写' WHERE sku_id='V201-1'")
    assert original != data_fingerprint(profile.db_path)
    with pytest.raises(ValueError, match="冻结清单"):
        scenario_settings(settings, a)


def test_legacy_database_stays_unknown_and_migration_preserves_conversations(settings):
    db = BusinessDB(settings)
    assert db.get_product("P001")["skus"] == []
    assert db.get_order("O10024", "USER001")["items"][0]["sku"] is None
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    with closing(sqlite3.connect(settings.db_path)) as conn:
        before = conn.execute("SELECT * FROM orders").fetchall()
    SessionStore(settings.db_path)
    assert store.list("USER001")[0]["session_id"] == sid
    with closing(sqlite3.connect(settings.db_path)) as conn:
        assert conn.execute("SELECT * FROM orders").fetchall() == before
        assert conn.execute("SELECT max(version) FROM acs_migrations").fetchone()[0] == 5
    assert not db.get_product("P001")["skus"]


def test_profile_rejects_changed_knowledge_before_serving(settings, tmp_path):
    root = tmp_path / "snapshot"
    build_scenario(root)
    (root / "docs/V201.md").write_text("错误正文")
    with pytest.raises(ValueError, match="知识或用例"):
        scenario_settings(settings, root)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["synthetic"] is True
