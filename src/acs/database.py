"""Read-only access to the mock business database.

Design rule for this whole project, enforced here rather than in the prompt:

    business facts (order status, logistics location, prices, dates) come from
    SQL, never from the model's memory or its own date arithmetic.

Three consequences that are deliberate:

* Every business tool takes `current_user_id` and checks ownership in SQL.
  Prompt instructions alone cannot prevent cross-user reads; a WHERE clause can.
* A missing row returns a structured `found: False` payload instead of raising,
  so the agent gets a fact it can reason about ("no such order") rather than an
  exception, and the trace shows what actually happened.
* Derived values are computed here, not by the model. This was not the first
  design: the original version returned raw `created_at` / `delivered_at` plus a
  day count, and the agent then *misreported the delivery date* in a demo run
  (it said the parcel arrived on the wrong day while correctly saying "within 7
  days"). Clients are built on a local clock and international time zones, so
  asking an LLM to convert a date into "yesterday" is a needless error surface.
  `today`, `*_date` and `*_days_ago` are now supplied explicitly.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings, get_settings
from .return_rules import delivery_days, read_return_policy, within_window

DAYS_IN_PRICE_PROTECTION = 15


def normalized_name(value: str) -> str:
    """Typography only; never infer a synonym, intention or selected entity."""
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def business_today(settings: Settings) -> date:
    return settings.reference_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def item_sku(conn: sqlite3.Connection, item_id: int) -> dict | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='order_item_skus'").fetchone():
        return None
    row = conn.execute("SELECT s.* FROM order_item_skus i JOIN product_skus s USING(sku_id) "
                       "JOIN order_items o ON o.id=i.item_id AND o.product_id=s.product_id "
                       "WHERE i.item_id=?", (item_id,)).fetchone()
    return dict(row) if row else None


def _days_since(iso_date: str | None, today: date) -> int | None:
    if not iso_date:
        return None
    try:
        return (today - date.fromisoformat(iso_date[:10])).days
    except ValueError:
        return None


class BusinessDB:
    """Thin, explicit data-access layer. No ORM, no query builder."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.db_path.exists():
            raise FileNotFoundError(
                f"database not found: {self.settings.db_path}\n"
                "run: uv run python scripts/init_db.py"
            )

    # ------------------------------------------------------------------ #
    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with closing(connect(self.settings.db_path)) as conn:
            return conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ #
    def get_product(self, product_id: str) -> dict[str, Any]:
        rows = self._query("SELECT * FROM products WHERE product_id = ?", (product_id,))
        if not rows:
            return {"found": False, "product_id": product_id, "reason": "商品不存在"}
        product = dict(rows[0])
        product["found"] = True
        product["current_price"] = self._current_price(product_id)
        product["price_changed_at"] = self._price_changed_at(product_id)
        product["attributes"] = [
            a for a in str(product.get("attributes", "")).split("；") if a
        ]
        with closing(connect(self.settings.db_path)) as conn:
            has_skus = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='product_skus'",
            ).fetchone()
            product["skus"] = [dict(row) for row in conn.execute(
                "SELECT * FROM product_skus WHERE product_id=? ORDER BY sku_id", (product_id,),
            )] if has_skus else []
        product["sku_information"] = "provided" if product["skus"] else "unknown"
        return product

    def list_products(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM products ORDER BY product_id")]

    def counts(self) -> dict[str, int]:
        return {
            table: self._query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
            for table in (
                "users", "products", "orders", "order_items", "logistics", "price_history",
            )
        }

    def list_orders(self, current_user_id: str, *, name_contains: str = "",
                    product_id: str = "") -> list[dict[str, Any]]:
        with closing(connect(self.settings.db_path)) as conn:
            conn.create_function("normalized_name", 1, normalized_name, deterministic=True)
            return [dict(row) for row in conn.execute(
                "SELECT order_id,status,total_amount,paid_amount,created_at,shipped_at,"
                "delivered_at FROM orders o WHERE user_id=? AND (?='' AND ?='' OR EXISTS("
                "SELECT 1 FROM order_items i JOIN products p USING(product_id) "
                "WHERE i.order_id=o.order_id AND instr(normalized_name(p.name),?)>0 "
                "AND (?='' OR p.product_id=?))) ORDER BY created_at DESC,order_id",
                (current_user_id, name_contains, product_id, normalized_name(name_contains),
                 product_id, product_id),
            )]

    # ------------------------------------------------------------------ #
    def get_order(self, order_id: str, current_user_id: str) -> dict[str, Any]:
        rows = self._query(
            "SELECT o.*, u.name AS user_name FROM orders o"
            " JOIN users u ON u.user_id = o.user_id WHERE o.order_id = ?",
            (order_id,),
        )
        if not rows:
            return {
                "found": False,
                "order_id": order_id,
                "reason": "订单不存在，请确认订单号是否正确",
            }

        order = dict(rows[0])

        # Ownership is decided here, in the data layer. The caller passes the
        # authenticated user; the model never supplies it.
        if order["user_id"] != current_user_id:
            return {
                "found": False,
                "order_id": order_id,
                "reason": "该订单不属于当前用户，无权查询",
                "error_type": "forbidden",
            }

        items = [
            dict(r)
            for r in self._query(
                "SELECT i.*, p.name AS product_name, p.category AS product_category"
                " FROM order_items i JOIN products p ON p.product_id = i.product_id"
                " WHERE i.order_id = ?",
                (order_id,),
            )
        ]
        for item in items:
            with closing(connect(self.settings.db_path)) as conn:
                item["sku"] = item_sku(conn, item["id"])
            item["current_price"] = self._current_price(item["product_id"])
            item["price_delta"] = round(
                float(item["purchase_price"]) - float(item["current_price"] or 0), 2
            )

        order["items"] = items
        order["found"] = True

        # Explicit calendar context: the model must not do date arithmetic.
        today = business_today(self.settings)
        order["today"] = today.isoformat()
        order["created_date"] = order.get("created_at")
        order["delivered_date"] = order.get("delivered_at")
        order["created_days_ago"] = _days_since(order.get("created_at"), today)
        order["delivered_days_ago"] = delivery_days(order.get("delivered_at"), today)

        policy = read_return_policy(self.settings)
        order["return_window_days"] = policy["windows"]["no_reason"]
        order["quality_return_window_days"] = policy["windows"]["quality"]
        order["return_policy_hash"] = policy["hash"]
        order["price_protection_days"] = DAYS_IN_PRICE_PROTECTION

        delivered_days = order["delivered_days_ago"]
        order["within_return_window"] = (
            order["status"] == "delivered"
            and within_window(delivered_days, policy["windows"]["no_reason"])
        )
        order["within_quality_return_window"] = (
            order["status"] == "delivered"
            and within_window(delivered_days, policy["windows"]["quality"])
        )
        created_days = order["created_days_ago"]
        order["within_price_protection"] = (
            created_days is not None and created_days <= DAYS_IN_PRICE_PROTECTION
        )

        order["instruction_for_assistant"] = (
            "日期字段已为你算好：delivered_date 是签收日期，delivered_days_ago 是"
            "距今的天数。请直接使用这些值，不要自行推算或改用其他日期。"
            "within_return_window只表示政策配置的无理由日期窗口，不适用于质量问题，也不代表完整退货资格。"
        )
        return order

    # ------------------------------------------------------------------ #
    def get_logistics(self, order_id: str, current_user_id: str) -> dict[str, Any]:
        order_rows = self._query(
            "SELECT user_id, status FROM orders WHERE order_id = ?", (order_id,)
        )
        if not order_rows:
            return {"found": False, "order_id": order_id, "reason": "订单不存在"}
        if order_rows[0]["user_id"] != current_user_id:
            return {
                "found": False,
                "order_id": order_id,
                "reason": "该订单不属于当前用户，无权查询",
                "error_type": "forbidden",
            }

        rows = self._query("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
        if not rows:
            return {
                "found": False,
                "order_id": order_id,
                "order_status": order_rows[0]["status"],
                "reason": "该订单暂无物流信息（可能尚未发货）",
            }

        data = dict(rows[0])
        data["found"] = True
        data["trace"] = json.loads(data.pop("trace_json") or "[]")
        data["latest"] = data["trace"][-1]["text"] if data["trace"] else None
        data["today"] = business_today(self.settings).isoformat()
        return data

    # ------------------------------------------------------------------ #
    def get_price_history(self, product_id: str) -> dict[str, Any]:
        rows = self._query(
            "SELECT * FROM price_history WHERE product_id = ?"
            " ORDER BY changed_at DESC, id DESC",
            (product_id,),
        )
        if not rows:
            return {"found": False, "product_id": product_id, "reason": "无价格记录"}
        history = [dict(r) for r in rows]
        return {
            "found": True,
            "product_id": product_id,
            "today": business_today(self.settings).isoformat(),
            "current_price": history[0]["price"],
            "records": history,
            "note": "records[0] 为当前售价；价格按时间倒序排列",
        }

    # ------------------------------------------------------------------ #
    def _current_price(self, product_id: str) -> float | None:
        rows = self._query(
            "SELECT price FROM price_history WHERE product_id = ?"
            " ORDER BY changed_at DESC, id DESC LIMIT 1",
            (product_id,),
        )
        return float(rows[0]["price"]) if rows else None

    def _price_changed_at(self, product_id: str) -> str | None:
        rows = self._query(
            "SELECT changed_at FROM price_history WHERE product_id = ?"
            " ORDER BY changed_at DESC, id DESC LIMIT 1",
            (product_id,),
        )
        return rows[0]["changed_at"] if rows else None
