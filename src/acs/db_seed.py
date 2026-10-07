"""SQLite mock business database for the MVP.

Scope: only the tables the five read-only tools actually need.
    users, products, orders, order_items, logistics, price_history

Deliberately NOT here: refunds, coupons, invoices, after_sales. The MVP has no
mutating tools, so those tables would have no reader.

The seed data is internally consistent on purpose, because two demo cases depend
on date/price arithmetic agreeing:

* `purchase_price` can differ from the current price -- that gap IS the
  price-protection case, so it must be a real price history, not a constant.
* The most recent `price_history` row is the product's *current* price, so the
  answer is derived in one place instead of stored twice.
* An order created N days ago must not reference a price that only took effect
  after it was placed.

Run:
    uv run python scripts/init_db.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import get_settings

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #

USERS = [
    ("USER001", "张伟", "13800138001"),
    ("USER002", "李娜", "13900139002"),
    ("USER003", "王芳", "13700137003"),
]

# product_id, name, category, brand, list price, currency, attributes
PRODUCTS = [
    (
        "P001",
        "SoundCore 声阔 Life P3 无线降噪耳机",
        "无线耳机",
        "SoundCore",
        "499.00",
        "CNY",
        "蓝牙5.2；主动降噪；单次续航10小时；总续航50小时；入耳式；IPX5防水；USB-C充电",
    ),
    (
        "P002",
        "SoundCore 声阔 Space Q45 头戴式耳机",
        "无线耳机",
        "SoundCore",
        "899.00",
        "CNY",
        "蓝牙5.3；自适应降噪；单次续航50小时；头戴式；支持LDAC；可折叠；3.5mm有线模式",
    ),
    (
        "P003",
        "Anker 安克 65W GaN 氮化镓充电器",
        "充电配件",
        "Anker",
        "129.00",
        "CNY",
        "65W；2C1A三口；GaN氮化镓；支持PD3.0/QC4+；折叠插脚；体积比传统充电器小40%",
    ),
    (
        "P004",
        "Anker 安克 10000mAh 磁吸无线充电宝",
        "充电配件",
        "Anker",
        "199.00",
        "CNY",
        "10000mAh；磁吸无线充电；15W无线+20W有线；支持边充边放；兼容MagSafe",
    ),
]

# (order_id, user_id, items[(product_id, qty, purchase_price)], status, paid, days_ago)
ORDER_SEED = [
    # ---- USER001 ----
    ("O10021", "USER001", [("P003", 1, "129.00")], "delivered", "129.00", 40),
    ("O10022", "USER001", [("P001", 1, "499.00")], "cancelled", "0.00", 20),
    # Case 2: in transit, has a logistics row
    ("O10023", "USER001", [("P002", 1, "899.00")], "shipped", "899.00", 3),
    # Case 3: multi-item. Created 3 days ago, so delivered 2 days ago --
    # deliberately NOT "yesterday", and deliberately not "today": the demo prompt
    # says the item was received yesterday, and an earlier `days_ago=2` put the
    # delivery date on today, which made the agent contradict the user. Two days
    # back is unambiguously inside the 7-day window.
    ("O10024", "USER001", [("P001", 1, "529.00"), ("P004", 1, "199.00")], "delivered", "728.00", 3),
    # Delivered long ago -> outside the return window, still inside warranty
    ("O10025", "USER001", [("P002", 1, "949.00")], "delivered", "949.00", 60),
    ("O10026", "USER001", [("P003", 1, "129.00")], "pending", "129.00", 1),
    # Price protection: created 10 days ago (inside 15), delivered 8 days ago
    # (outside the 7-day return window). P001's current price is 469.
    ("O10027", "USER001", [("P001", 1, "499.00")], "delivered", "499.00", 10),
    ("O10028", "USER001", [("P004", 1, "199.00")], "shipped", "199.00", 5),
    # ---- USER002 ----
    ("O10029", "USER002", [("P001", 1, "519.00")], "delivered", "519.00", 12),
    # Case 4: belongs to USER002 -- USER001 must NOT be able to read it
    ("O10030", "USER002", [("P002", 1, "899.00")], "shipped", "899.00", 4),
    ("O10031", "USER002", [("P003", 1, "119.00")], "delivered", "119.00", 30),
    ("O10032", "USER002", [("P004", 1, "199.00")], "cancelled", "0.00", 25),
    ("O10033", "USER002", [("P001", 1, "499.00")], "pending", "499.00", 1),
    ("O10034", "USER002", [("P002", 1, "899.00")], "delivered", "899.00", 90),
    # ---- USER003 ----
    ("O10035", "USER003", [("P003", 2, "129.00")], "delivered", "258.00", 45),
    ("O10036", "USER003", [("P001", 1, "459.00")], "shipped", "459.00", 6),
    ("O10037", "USER003", [("P004", 1, "189.00")], "delivered", "189.00", 70),
    ("O10038", "USER003", [("P002", 1, "869.00")], "delivered", "869.00", 22),
    ("O10039", "USER003", [("P001", 1, "499.00")], "cancelled", "0.00", 18),
    # no logistics row on purpose: exercises the "not shipped yet" path
    ("O10040", "USER003", [("P003", 1, "129.00")], "pending", "129.00", 2),
    ("O10041", "USER001", [("P002", 1, "899.00")], "shipped", "899.00", 7),
    ("O10042", "USER001", [("P003", 1, "129.00")], "delivered", "129.00", 100),
    ("O10043", "USER002", [("P004", 1, "199.00")], "shipped", "199.00", 8),
    ("O10044", "USER002", [("P002", 1, "929.00")], "delivered", "929.00", 5),
    ("O10045", "USER003", [("P004", 1, "199.00")], "pending", "199.00", 1),
]

CARRIERS = ["顺丰速运", "中通快递", "圆通速递", "京东物流"]
CITIES = ["上海", "杭州", "苏州", "南京", "北京", "广州"]

# (product_id, price, days_ago). The newest row is the CURRENT price.
# Every "days_ago" is larger than the age of the newest order for that product,
# so no order can reference a price that did not exist yet.
PRICE_HISTORY = [
    ("P001", "499.00", 120),  # list price
    ("P001", "529.00", 25),   # temporary rise
    ("P001", "469.00", 12),   # current price, dropped before O10027 was placed
    ("P002", "949.00", 150),
    ("P002", "899.00", 20),
    ("P002", "879.00", 3),
    ("P003", "119.00", 140),
    ("P003", "129.00", 10),
    ("P004", "189.00", 110),
    ("P004", "199.00", 15),
    ("P004", "179.00", 4),
]

# order_id -> (last trace index, logistics status)
LOGISTICS_PLAN = {
    "O10023": (2, "in_transit"),
    "O10024": (4, "delivered"),
    "O10025": (4, "delivered"),
    "O10026": (0, "pending_pickup"),
    "O10027": (4, "delivered"),
    "O10028": (1, "in_transit"),
    "O10029": (4, "delivered"),
    "O10030": (1, "in_transit"),
    "O10031": (4, "delivered"),
    "O10033": (0, "pending_pickup"),
    "O10034": (4, "delivered"),
    "O10035": (4, "delivered"),
    "O10036": (2, "in_transit"),
    "O10037": (4, "delivered"),
    "O10038": (4, "delivered"),
    "O10041": (1, "in_transit"),
    "O10042": (4, "delivered"),
    "O10043": (2, "in_transit"),
    "O10044": (4, "delivered"),
}

TRACE_PATTERNS = [
    "商家已发货，等待揽收",
    "已揽收，发往{city}分拨中心",
    "到达【{city}分拨中心】，正在分拣",
    "已到达【{city}营业点】，正在派送",
    "已签收，签收人：本人。感谢使用{carrier}",
]

SCHEMA = """
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS logistics;
DROP TABLE IF EXISTS price_history;

CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    phone   TEXT NOT NULL
);

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    brand      TEXT NOT NULL,
    price      REAL NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'CNY',
    attributes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE orders (
    order_id     TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    status       TEXT NOT NULL,
    total_amount REAL NOT NULL,
    paid_amount  REAL NOT NULL,
    created_at   TEXT NOT NULL,
    shipped_at   TEXT,
    delivered_at TEXT,
    cancelled_at TEXT
);

CREATE TABLE order_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       TEXT NOT NULL REFERENCES orders(order_id),
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    quantity       INTEGER NOT NULL,
    purchase_price REAL NOT NULL
);

CREATE TABLE logistics (
    order_id     TEXT PRIMARY KEY REFERENCES orders(order_id),
    carrier      TEXT NOT NULL,
    tracking_no  TEXT NOT NULL,
    status       TEXT NOT NULL,
    current_city TEXT,
    updated_at   TEXT NOT NULL,
    trace_json   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    price      REAL NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_price_product ON price_history(product_id, changed_at);
"""


def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def build(db_path: Path) -> dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO users VALUES (?,?,?)", USERS)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", PRODUCTS)

        for order_id, user_id, items, status, paid, days_ago in ORDER_SEED:
            created = _iso(days_ago)
            shipped = _iso(days_ago - 1) if status in ("shipped", "delivered") else None
            delivered = _iso(days_ago - 2) if status == "delivered" else None
            cancelled = _iso(days_ago) if status == "cancelled" else None
            total = sum(float(price) * qty for _, qty, price in items)

            conn.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                (order_id, user_id, status, round(total, 2), float(paid),
                 created, shipped, delivered, cancelled),
            )
            for product_id, qty, price in items:
                conn.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity, purchase_price)"
                    " VALUES (?,?,?,?)",
                    (order_id, product_id, qty, float(price)),
                )

        seed_by_id = {row[0]: row for row in ORDER_SEED}
        for order_id, (last_index, status) in LOGISTICS_PLAN.items():
            days_ago = seed_by_id[order_id][5]
            carrier = CARRIERS[int(order_id[-2:]) % len(CARRIERS)]
            city = CITIES[int(order_id[-2:]) % len(CITIES)]

            trace = [
                {
                    "time": _iso(max(days_ago - 1 - i, 0)),
                    "text": TRACE_PATTERNS[i].format(city=city, carrier=carrier),
                }
                for i in range(last_index + 1)
            ]
            conn.execute(
                "INSERT INTO logistics VALUES (?,?,?,?,?,?,?)",
                (
                    order_id,
                    carrier,
                    f"SF{order_id[1:]}CN",
                    status,
                    None if status == "delivered" else city,
                    trace[-1]["time"],
                    json.dumps(trace, ensure_ascii=False),
                ),
            )

        for product_id, price, days_ago in PRICE_HISTORY:
            conn.execute(
                "INSERT INTO price_history (product_id, price, changed_at) VALUES (?,?,?)",
                (product_id, float(price), _iso(days_ago)),
            )

        conn.commit()

        counts = {}
        for table in ("users", "products", "orders", "order_items", "logistics", "price_history"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        conn.close()


def main() -> int:
    settings = get_settings()
    counts = build(settings.db_path)
    print(f"built {settings.db_path}")
    for table, n in counts.items():
        print(f"  {table:<15} {n:>4} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
