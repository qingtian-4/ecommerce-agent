"""Business data layer: correctness and, most importantly, authorisation.

The cross-user test is the one that matters. Prompt instructions cannot enforce
ownership, so this asserts the SQL layer does.
"""

from __future__ import annotations

from acs.database import BusinessDB


def test_order_list_is_scoped_to_the_selected_user(settings) -> None:
    db = BusinessDB(settings)
    first = {row["order_id"] for row in db.list_orders("USER001")}
    second = {row["order_id"] for row in db.list_orders("USER002")}
    assert "O10024" in first
    assert "O10030" in second
    assert first.isdisjoint(second)
    assert first | second < {row["order_id"] for uid in ("USER001", "USER002", "USER003")
                             for row in db.list_orders(uid)}
    assert db.list_orders("USER001' OR 1=1 --") == []


def test_database_counts_include_line_items_and_price_records(settings) -> None:
    assert BusinessDB(settings).counts() == {
        "users": 3, "products": 4, "orders": 25, "order_items": 26,
        "logistics": 19, "price_history": 37,
    }


def test_get_order_returns_real_facts(settings) -> None:
    db = BusinessDB(settings)
    order = db.get_order("O10023", "USER001")

    assert order["found"] is True
    assert order["user_id"] == "USER001"
    assert order["status"] == "shipped"
    # Facts must come from the row, not be derived by the caller.
    assert order["delivered_date"] is None
    assert order["created_days_ago"] is not None and order["created_days_ago"] >= 0
    assert order["items"], "order must expose its line items"


def test_get_order_unknown_id_is_a_fact_not_an_exception(settings) -> None:
    db = BusinessDB(settings)
    order = db.get_order("NOPE999", "USER001")
    assert order["found"] is False
    assert "不存在" in order["reason"]


def test_cross_user_order_read_is_denied(settings) -> None:
    """O10030 belongs to USER002. USER001 must not be able to read it."""
    db = BusinessDB(settings)
    order = db.get_order("O10030", "USER001")

    assert order["found"] is False
    assert order["error_type"] == "forbidden"
    # And no order data may leak alongside the refusal.
    for leaked in ("status", "items", "paid_amount", "delivered_at", "user_name"):
        assert leaked not in order, f"refusal leaked field {leaked!r}"


def test_cross_user_logistics_read_is_denied(settings) -> None:
    db = BusinessDB(settings)
    logistics = db.get_logistics("O10030", "USER001")
    assert logistics["found"] is False
    assert logistics["error_type"] == "forbidden"
    assert "trace" not in logistics


def test_owner_can_read_own_logistics(settings) -> None:
    db = BusinessDB(settings)
    logistics = db.get_logistics("O10023", "USER001")
    assert logistics["found"] is True
    assert logistics["carrier"]
    assert logistics["trace"], "expected a logistics trace"
    assert logistics["latest"] == logistics["trace"][-1]["text"]


def test_order_without_logistics_row_reports_that(settings) -> None:
    """O10040 is pending and deliberately has no logistics row."""
    db = BusinessDB(settings)
    logistics = db.get_logistics("O10040", "USER003")
    assert logistics["found"] is False
    assert "物流" in logistics["reason"]


def test_delivered_order_exposes_derived_return_window(settings) -> None:
    """O10024 was delivered 2 days ago, so it must be inside the 7-day window."""
    db = BusinessDB(settings)
    order = db.get_order("O10024", "USER001")

    assert order["found"] is True
    assert order["status"] == "delivered"
    assert order["delivered_date"], "a delivered order must expose its date"
    assert order["delivered_days_ago"] <= order["return_window_days"]
    assert order["within_return_window"] is True
    assert len(order["items"]) >= 2, "O10024 has two line items"


def test_old_delivered_order_is_outside_return_window_but_inside_price_protection(
    settings,
) -> None:
    """O10027: delivered 8 days ago (outside 7), created 10 days ago (inside 15).

    These two windows start on different dates on purpose -- the policy says so --
    and this test is what pins that the code agrees with the policy document.
    """
    db = BusinessDB(settings)
    order = db.get_order("O10027", "USER001")

    assert order["within_return_window"] is False
    assert order["within_price_protection"] is True


def test_purchase_price_differs_from_current_price_for_price_protection(
    settings,
) -> None:
    """The price-protection case needs a real gap, not two identical numbers."""
    db = BusinessDB(settings)
    order = db.get_order("O10027", "USER001")
    item = order["items"][0]

    assert item["purchase_price"] == 499.0
    assert item["current_price"] == 469.0
    assert item["price_delta"] == 30.0


def test_price_history_is_newest_first(settings) -> None:
    db = BusinessDB(settings)
    history = db.get_price_history("P001")
    assert history["found"] is True
    dates = [r["changed_at"] for r in history["records"]]
    assert dates == sorted(dates, reverse=True)
    assert history["current_price"] == history["records"][0]["price"]


def test_get_product_reports_current_price(settings) -> None:
    db = BusinessDB(settings)
    product = db.get_product("P003")
    assert product["found"] is True
    assert product["current_price"] == 129.0
    assert product["attributes"], "attributes should be split into a list"


def test_get_product_unknown_id(settings) -> None:
    db = BusinessDB(settings)
    assert db.get_product("P999")["found"] is False
