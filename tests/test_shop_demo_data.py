"""New demo bytes, business consistency and external source facts, all offline."""
import json
import socket
import sqlite3
from contextlib import closing

import pytest

from acs.cs_tools import ToolRegistry
from acs.local_return_tools import FORMAL_FIXTURE_KIND, LocalKnowledge, fixture_settings
from acs.shop_demo_data import SOURCE, build_dataset, load_dataset, materialize


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Demo reconstruction must never use a model or network")
    monkeypatch.setattr(socket.socket, "connect", reject)


def test_demo_byte_rebuild_and_no_historical_inputs_changed(tmp_path):
    out = tmp_path / "rebuilt"
    build_dataset(out)
    assert {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()} == {
        str(p.relative_to(SOURCE)): p.read_bytes() for p in SOURCE.rglob("*") if p.is_file()}
    suite = load_dataset(out)
    assert (suite["manifest"]["products"], suite["manifest"]["orders"],
            suite["manifest"]["documents"]) == (48, 240, 39)
    assert len({p["category"] for p in suite["business"]["products"]}) == 10


def test_demo_tools_logistics_unknown_skus_ownership_and_barriers(tmp_path):
    root = tmp_path / "runtime"
    materialize(root)
    settings = fixture_settings(root, kind=FORMAL_FIXTURE_KIND)
    registry = ToolRegistry(settings, kb=LocalKnowledge(settings))
    with closing(sqlite3.connect(root / "business.sqlite")) as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 240
        for row in conn.execute("SELECT o.status,o.shipped_at,o.delivered_at,l.trace_json "
                                "FROM logistics l JOIN orders o USING(order_id)"):
            trace = json.loads(row[3])
            assert len(trace) == (10 if row[0] == "delivered" else 6)
            assert [e["time"] for e in trace] == sorted(e["time"] for e in trace)
            assert trace[0]["time"][:10] == row[1]
            if row[0] == "delivered":
                assert trace[-1]["time"][:10] == row[2]
                assert trace[-1]["status"] == "已签收"
        with pytest.raises(sqlite3.Error):
            conn.execute("UPDATE logistics SET status='delivered'")
    product = registry.db.get_product("E001")
    assert product["current_price"] == 1999
    assert len(product["skus"]) == 1
    assert product["skus"][0]["color"] is None
    assert product["skus"][0]["anc"] is None
    assert product["skus"][0]["availability"] == "unknown"
    found, _, error = registry.run("list_orders", '{"name_contains":"燃气"}',
                                  current_user_id="USER001")
    assert error is None and found["orders"]
    assert all(any("燃气" in i["product_name"] for i in o["items"]) for o in found["orders"])
    assert registry.db.get_order("O72031", "USER001")["found"] is False
    result, _, error = registry.run("search_knowledge", '{"query":"E001 F6033-UD3 规格"}',
                                   current_user_id="USER001")
    assert error is None and any(d["document_id"] == "E001" for d in result["documents"])
    with pytest.raises(FileExistsError):
        materialize(root)
