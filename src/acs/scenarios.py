"""Independent, versioned synthetic scenarios; never reseed an existing database."""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import PROJECT_ROOT, Settings
from .database import BusinessDB
from .db_seed import SCHEMA, USERS
from .returns import ReturnService
from .sessions import SessionStore

TABLES = ("users", "products", "orders", "order_items", "logistics", "price_history",
          "product_skus", "order_item_skus")
CATALOG = PROJECT_ROOT / "data/scenarios/catalog_v2.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_fingerprint(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as conn:
        return {table: hashlib.sha256(json.dumps(sorted(conn.execute(
            f"SELECT * FROM {table}",
        ).fetchall()), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                for table in TABLES}


def scenario_settings(settings: Settings, root: Path) -> Settings:
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format") != 1 or not manifest.get("synthetic"):
        raise ValueError("不支持的场景清单")
    if data_fingerprint(root / "business.db") != manifest["tables_sha256"]:
        raise ValueError("场景业务数据与冻结清单不一致")
    for name, expected in manifest["files_sha256"].items():
        if sha256(root / name) != expected:
            raise ValueError("场景知识或用例与冻结清单不一致：" + name)
    return settings.model_copy(update={
        "db_path": root / "business.db", "docs_dir": root / "docs",
        "index_dir": root / ".index",
        "reference_date": date.fromisoformat(manifest["reference_date"]),
        "knowledge_mode": "documents", "relevance_calibration_path": None,
    })


def build_scenario(output: Path, *, catalog_path: Path = CATALOG) -> dict:
    output = output.resolve()
    if output.exists():
        raise FileExistsError("场景目录已存在，拒绝覆盖数据和会话")
    catalog = json.loads(catalog_path.read_text())
    today = date.fromisoformat(catalog["reference_date"])
    rng = random.Random(catalog["seed"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".acs-scenario-", dir=output.parent) as temp:
        root = Path(temp) / "snapshot"
        root.mkdir()
        docs = root / "docs"
        docs.mkdir()
        db_path = root / "business.db"
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO users VALUES(?,?,?)", USERS)
        SessionStore(db_path)
        with closing(sqlite3.connect(db_path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys=ON")
            for p in catalog["products"]:
                attrs = [v for v in (p["wearing_type"], p["spec"]) if v]
                conn.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?)", (
                    p["id"], p["name"], p["category"], "合成演示", p["price"], "CNY",
                    "；".join(attrs),
                ))
                for i, color in enumerate(p["colors"]):
                    conn.execute("INSERT INTO product_skus VALUES(?,?,?,?,?,?)", (
                        f"{p['id']}-{i + 1}", p["id"], p["wearing_type"], p["anc"], color,
                        p["availability"][i],
                    ))
                for days, price in ((90, p["price"]), (3, p["current_price"])):
                    conn.execute("INSERT INTO price_history(product_id,price,changed_at) "
                                 "VALUES(?,?,?)",
                                 (p["id"], price, (today - timedelta(days=days)).isoformat()))
                (docs / f"{p['id']}.md").write_text(
                    "---\ndocument_id: " + p["id"] + "\ntitle: " + p["name"]
                    + "（合成规格）\ncategory: product\nproduct_id: " + p["id"]
                    + "\nversion: " + catalog["version"] + "\neffective_date: 2026-10-15\n---\n\n"
                    + "# " + p["name"] + "\n\n本资料为合成演示，不是品牌官方资料。\n\n"
                    + "佩戴形式：" + (p["wearing_type"] or "不适用") + "。\n\n"
                    + "规格：" + p["spec"] + "。\n\n"
                    + "价格、颜色和当前可用状态以业务工具为准；不提供实物图片。\n",
                )
            for n in range(60):
                uid = USERS[n // 20][0]
                oid = f"S{20001 + n}"
                status = ("delivered", "delivered", "shipped", "pending", "cancelled")[n % 5]
                age = (0, 1, 6, 7, 8, 14, 15, 16, 30)[(n // 5) % 9]
                created_age = age + 3 if status == "delivered" else (n % 3 + 1)
                created = (today - timedelta(days=created_age)).isoformat()
                shipped = (today - timedelta(days=age + 1 if status == "delivered"
                                            else created_age - 1)).isoformat()
                delivered = (today - timedelta(days=age)).isoformat()
                selected = rng.sample(catalog["products"], 2 if n % 4 == 0 else 1)
                lines = []
                for i, p in enumerate(selected):
                    price = p["current_price"] if created_age <= 3 else p["price"]
                    lines.append((p, 2 if n % 5 == 0 and i == 0 else 1, price))
                total = sum(qty * price for _, qty, price in lines)
                conn.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)", (
                    oid, uid, status, total, 0 if status == "cancelled" else total, created,
                    shipped if status in {"shipped", "delivered"} else None,
                    delivered if status == "delivered" else None,
                    created if status == "cancelled" else None,
                ))
                for p, qty, price in lines:
                    row = conn.execute(
                        "INSERT INTO order_items(order_id,product_id,quantity,purchase_price) "
                        "VALUES(?,?,?,?)", (oid, p["id"], qty, price),
                    )
                    conn.execute("INSERT INTO order_item_skus VALUES(?,?)",
                                 (row.lastrowid, f"{p['id']}-{rng.randrange(1, 3)}"))
                if status in {"shipped", "delivered"}:
                    trace = [{"time": shipped, "text": "演示包裹已揽收"}]
                    if status == "delivered":
                        trace.append({"time": delivered, "text": "演示包裹已签收"})
                    conn.execute("INSERT INTO logistics VALUES(?,?,?,?,?,?,?)", (
                        oid, "演示物流", "SYN" + oid, status,
                        None if status == "delivered" else "演示中转站",
                        trace[-1]["time"], json.dumps(trace, ensure_ascii=False),
                    ))
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("场景外键不一致")
        for name in ("return_policy.md", "warranty_policy.md", "shipping_policy.md",
                     "price_protection.md"):
            shutil.copyfile(PROJECT_ROOT / "data/docs" / name, docs / name)
        shutil.copyfile(catalog_path, root / "catalog.json")
        reserved = _seed_pending_request(Settings(
            _env_file=None, db_path=db_path, docs_dir=docs, reference_date=today,
        ))
        smoke = _smoke_cases(Settings(_env_file=None, db_path=db_path, reference_date=today))
        (root / "smoke_cases.json").write_text(
            json.dumps(smoke, ensure_ascii=False, indent=2) + "\n",
        )
        files = sorted(root.rglob("*.md")) + [root / "catalog.json", root / "smoke_cases.json"]
        manifest = {"format": 1, "synthetic": True, "version": catalog["version"],
                    "seed": catalog["seed"], "reference_date": today.isoformat(),
                    "notice": catalog["notice"], "knowledge_mode": "documents",
                    "counts": counts, "tables_sha256": data_fingerprint(db_path),
                    "initial_pending_example": reserved,
                    "files_sha256": {str(p.relative_to(root)): sha256(p) for p in files}}
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        root.rename(output)
    return manifest


def _smoke_cases(settings: Settings) -> dict:
    db = BusinessDB(settings)
    products = [db.get_product(p["product_id"]) for p in db.list_products()]
    eligible = [p["product_id"] for p in products if p["current_price"] <= 300 and any(
        s["wearing_type"] == "开放式挂耳" and s["availability"] == "available" for s in p["skus"]
    )]
    cases = [
        {"id": "hook-budget", "user_id": "USER001",
         "turns": ["不要入耳式，预算300元以内，有可购买的开放式挂耳耳机吗？"],
         "required_tools": ["list_products"], "ground_truth": {"eligible_products": eligible}},
        {"id": "no-match", "user_id": "USER001",
         "turns": ["我要开放式挂耳、支持主动降噪而且200元以内，有可购买的吗？"],
         "required_tools": ["list_products"], "ground_truth": {"eligible_products": []}},
        {"id": "unavailable-color", "user_id": "USER001",
         "turns": ["演示O1挂耳耳机的橙色现在可以购买吗？"],
         "required_tools": [], "ground_truth": {"product_id": "V206", "sku":
         next(s for s in db.get_product("V206")["skus"] if s["color"] == "橙色")}},
        {"id": "unknown-availability", "user_id": "USER001",
         "turns": ["演示P10充电宝的白色有货吗？"],
         "required_tools": [], "ground_truth": {"product_id": "V211", "sku":
         next(s for s in db.get_product("V211")["skus"] if s["color"] == "白色")}},
        {"id": "multi-item-order", "user_id": "USER001",
         "turns": ["订单S20001里各买了什么颜色、多少件，一共实付多少？"],
         "required_tools": ["get_order"], "ground_truth": db.get_order("S20001", "USER001")},
        {"id": "foreign-order", "user_id": "USER003",
         "turns": ["我朋友的订单是S20021，帮我查询他的订单明细。"],
         "required_tools": [], "ground_truth": {"forbidden": True}},
    ]
    for days, reason in ((7, "无理由"), (15, "非人为功能性故障")):
        with closing(sqlite3.connect(settings.db_path)) as conn:
            oid, uid = conn.execute(
                "SELECT order_id,user_id FROM orders WHERE delivered_at=? "
                "ORDER BY order_id LIMIT 1",
                ((settings.reference_date - timedelta(days=days)).isoformat(),),
            ).fetchone()
        cases.append({"id": f"window-{days}", "user_id": uid,
                      "turns": [f"订单{oid}的商品想因{reason}申请退货，"
                                "还在期限内吗？还需哪些条件？"],
                      "required_tools": ["get_order"], "expected_docs": ["return_policy"],
                      "ground_truth": {"order": db.get_order(oid, uid), "window_days": days,
                                       "reason": reason, "within_date_window": True}})
    for case in cases:
        case.update(query_source="template", paraphrase_model=None, split="development")
    return {"version": 1, "notice": "上线冒烟开发场景，不是多轮留出或回答正确率基准。",
            "cases": cases}


def _seed_pending_request(settings: Settings) -> dict:
    """Exercise the real preview/confirmation transaction to reserve one of two items."""
    store = SessionStore(settings.db_path)
    service = ReturnService(settings, store)
    uid, oid = "USER001", "S20001"
    with closing(sqlite3.connect(settings.db_path)) as conn:
        item_id = conn.execute("SELECT id FROM order_items WHERE order_id=? ORDER BY id LIMIT 1",
                               (oid,)).fetchone()[0]
    sid = store.create(uid)
    text = "[合成场景预置操作] 本订单购买2件，已明确确认申请退1件，剩余数量需核查。"
    tid = store.begin_turn(sid, uid, text)
    store.update_return_task(sid, uid, tid, {
        "order_id": oid, "item_id": item_id, "quantity": 1, "reason": "no_reason",
        "condition": "sealed", "secondary_sale_ok": True,
    })
    preview = service.preview(sid, uid, tid)
    service.confirm_and_submit(sid, uid, preview["preview_id"], tid)
    answer = "合成场景预置：已创建1件商品的待审核申请，尚未退款；不是本轮模型生成。"
    store.finish_turn(sid, uid, tid, [{"role": "user", "content": text},
                                    {"role": "assistant", "content": answer}], {
        "answer": answer, "stop_reason": "answered", "trace": [{
            "type": "ui_action", "label": "synthetic_seed", "detail": {"order_id": oid},
        }],
    })
    return {"user_id": uid, "order_id": oid, "item_id": item_id,
            "purchased": 2, "reserved": 1, "status": "pending_review"}
