"""Cross-tool facts must agree before the model sees them."""

from datetime import date

from acs.database import BusinessDB, connect


def test_return_example_was_delivered_exactly_two_days_ago(settings):
    order = BusinessDB(settings).get_order("O10024", "USER001")
    assert order["delivered_days_ago"] == 2


def test_delivered_orders_match_their_logistics(settings):
    db = BusinessDB(settings)
    with connect(settings.db_path) as conn:
        orders = conn.execute(
            "SELECT o.* FROM orders o JOIN logistics l ON l.order_id = o.order_id"
            " WHERE o.status = 'delivered'"
        ).fetchall()
    for order in orders:
        shipment = db.get_logistics(order["order_id"], order["user_id"])
        assert shipment["trace"][-1]["time"] == order["delivered_at"], order["order_id"]
        times = [event["time"] for event in shipment["trace"]]
        assert times == sorted(times)
        assert times[0] == order["shipped_at"]


def test_pending_orders_do_not_claim_they_have_shipped(settings):
    shipment = BusinessDB(settings).get_logistics("O10026", "USER001")
    assert shipment["status"] == "pending_pickup"
    assert "已发货" not in shipment["latest"]


def test_purchase_prices_were_effective_when_orders_were_created(settings):
    with connect(settings.db_path) as conn:
        items = conn.execute(
            "SELECT o.order_id, o.created_at, i.product_id, i.purchase_price"
            " FROM orders o JOIN order_items i ON i.order_id = o.order_id"
        ).fetchall()
        for item in items:
            price = conn.execute(
                "SELECT price FROM price_history WHERE product_id = ? AND changed_at <= ?"
                " ORDER BY changed_at DESC, id DESC LIMIT 1",
                (item["product_id"], item["created_at"]),
            ).fetchone()
            assert price is not None
            assert price["price"] == item["purchase_price"], item["order_id"]


def test_reference_date_keeps_evaluation_windows_stable(settings):
    from acs.db_seed import build

    reference = date(2026, 10, 8)
    fixed = settings.model_copy(
        update={"reference_date": reference, "db_path": settings.index_dir.parent / "fixed.db"}
    )
    build(fixed.db_path, reference_date=reference)
    order = BusinessDB(fixed).get_order("O10027", "USER001")
    assert order["today"] == reference.isoformat()
    assert order["created_days_ago"] == 10
    assert order["within_price_protection"] is True
