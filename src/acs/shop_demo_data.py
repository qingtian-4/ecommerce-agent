"""Versioned demo adaptation, never a replacement for frozen benchmark inputs."""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from . import commerce_benchmark as benchmark
from . import multiturn_evaluation as m8
from .commerce_benchmark_data import dump, file_hash
from .config import PROJECT_ROOT
from .local_submission import init_formal_fixture
from .shop_data_revision import CONSULTATION_VERSION, enable_consultation, revise
from .shop_data_revision import VERSION as REVISED_VERSION

VERSION = "shop-realism-v1.1"
LEGACY_VERSION = "shop-realism-v1"
SOURCE = PROJECT_ROOT / "data/demo/shop_realism_v1_1"
REVISED_SOURCE = PROJECT_ROOT / "data/demo/shop_realism_v1_2"
CONSULTATION_SOURCE = PROJECT_ROOT / "data/demo/shop_realism_v1_3"
CURATED_SOURCE = PROJECT_ROOT / "data/demo/shop_realism_v1"
BASE = PROJECT_ROOT / "data/eval/commerce_benchmark_v2_2"


def build_dataset(out: Path, source=CURATED_SOURCE, *, revision=VERSION):
    """Rebuild bytes from curated public sources and unchanged v2.2 business data."""
    sources = json.loads((source / "sources.json").read_text())
    assert sources["version"] == LEGACY_VERSION
    if revision not in {VERSION, LEGACY_VERSION, REVISED_VERSION, CONSULTATION_VERSION}:
        raise ValueError("Unknown shop demo revision")
    out.mkdir(parents=True, exist_ok=False)
    for name in ("sources.json", "LICENSE"):
        shutil.copyfile(source / name, out / name)
    data = copy.deepcopy(json.loads((BASE / "business.json").read_text()))
    products = sources["products"]
    for p in products:
        data["products"].append({k: p[k] for k in ("id", "name", "category", "attributes")}
                                | {"price": p["price"], "current_price": p["price"]})
    # Retain 240 orders / 8 synthetic identities. Change only this new demo copy.
    # Keep in-ear multi-item orders; diversify three of every six order slots.
    for index, order in enumerate(data["orders"]):
        slot = index % 6
        if slot in (2, 3, 4):
            product = products[(index // 6 * 3 + slot - 2) % len(products)]
            item = order["items"][0]
            item.update(product_id=product["id"], price=product["price"])
            if slot == 4 and index // 6 % 2 == 0:
                today = datetime.fromisoformat(data["reference_date"])
                order.update(created_at=(today-timedelta(days=7)).date().isoformat(),
                             shipped_at=(today-timedelta(days=5)).date().isoformat(),
                             delivered_at=(today-timedelta(days=3)).date().isoformat())
    dump(out / "business.json", data)
    shutil.copytree(BASE / "docs", out / "docs")
    for p in products:
        body = "\n\n".join(p["attributes"])
        (out / "docs" / (p["id"] + ".md")).write_text(
            f"---\ndocument_id: {p['id']}\ntitle: {p['name']}\ncategory: product\n"
            f"product_id: {p['id']}\nversion: {revision}\n---\n\n# {p['name']}\n\n"
            f"用于本地演示。来源 ECom-Bench 商品 {p['source_product_id']} 的历史规格摘录，"
            "不是当前官方销售承诺。价格和订单请查业务工具。\n\n" + body + "\n",
            encoding="utf-8",
        )
    if revision != LEGACY_VERSION:
        for name in ("return_policy", "warranty_policy"):
            path = out / "docs" / (name + ".md")
            text = path.read_text().replace("product_type: 通用",
                                            "product_type: 无线耳机与充电配件")
            text = text.replace("version: v1\n", f"version: {revision}\n")
            text += "\n## 家电政策边界\n\n本文件不提供家电退货、保修、安装或换货规则。" \
                    "热水器、冰箱、洗碗机等家电不能套用上述期限；详见家电政策边界说明。\n"
            path.write_text(text)
        (out / "docs/appliance_policy_boundary.md").write_text(
            "---\ndocument_id: appliance_policy_boundary\ntitle: 家电售后政策边界\n"
            f"category: policy\nproduct_type: 家电\nversion: {revision}\n---\n\n"
            "# 家电售后政策边界\n\n"
            "电热水器、厨房小厨宝、燃气热水器、洗碗机、油烟机、净饮机、燃气灶、冰箱，"
            "目前只导入了历史规格和演示价格、本人订单、物流。\n\n"
            "本知识库没有适用于这些家电的退货、保修期限、安装、维修或换货政策，"
            "不能套用耳机12个月、充电器18个月或充电宝12个月的保修规则。"
            "燃气热水器用了两个月、点火有问题，仍不能据此判断是否在保修期。\n\n"
            "这表示缺少适用依据，不表示消费者没有售后权利。应核实该商品对应的"
            "保修凭证或补充经过确认的适用政策；本系统不能承诺保修、安装、换货，"
            "也不能创建家电售后预览或申请。商品规格、价格、订单和物流仍可正常查询。\n"
        )
    if revision in {REVISED_VERSION, CONSULTATION_VERSION}:
        data, sources = revise(out)
    if revision == CONSULTATION_VERSION:
        enable_consultation(out)
    dump(out / "manifest.json", dict(
        version=revision, clock=data["reference_date"]+"T04:00:00+00:00",
        source_commit=sources["upstream"]["commit"],
        base_version="local-commerce-v2.2", base_business_sha256=file_hash(BASE / "business.json"),
        products=len(data["products"]), orders=len(data["orders"]), users=len(data["users"]),
        documents=len(list((out / "docs").glob("*.md"))),
        kind="derived_demo_not_official_benchmark_or_real_customer_data",
        files={str(p.relative_to(out)): file_hash(p)
               for p in sorted(out.rglob("*")) if p.is_file()},
    ))


def load_dataset(root):
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["version"] not in {VERSION, LEGACY_VERSION, REVISED_VERSION, CONSULTATION_VERSION}:
        raise ValueError("Unknown shop demo data version")
    actual = {str(p.relative_to(root)): file_hash(p) for p in root.rglob("*")
              if p.is_file() and p.name != "manifest.json"}
    if actual != manifest["files"]:
        raise ValueError("Shop demo data fingerprint differs")
    return dict(root=root, manifest=manifest,
                business=json.loads((root / "business.json").read_text()),
                sources=json.loads((root / "sources.json").read_text()))


def materialize(root, dataset=SOURCE):
    suite = load_dataset(dataset)
    data, sources = suite["business"], suite["sources"]

    def seed(path, **kwargs):
        benchmark.seed_database(path, data, **kwargs)
        # Run BEFORE the standard initializer installs its immutable business
        # barriers. No migration, trigger removal, or edit to an existing fixture.
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys=ON")
            external = {p["id"] for p in sources["products"]}
            for pid in external:
                conn.execute("DELETE FROM order_item_skus WHERE item_id IN "
                             "(SELECT id FROM order_items WHERE product_id=?)", (pid,))
                conn.execute("DELETE FROM product_skus WHERE product_id=?", (pid,))
                conn.execute("INSERT INTO product_skus VALUES(?,?,NULL,NULL,NULL,'unknown')",
                             (pid + "-1", pid))
                conn.execute("INSERT INTO order_item_skus SELECT id,? FROM order_items "
                             "WHERE product_id=?", (pid + "-1", pid))
                conn.execute("UPDATE products SET brand='美的' WHERE product_id=?", (pid,))
            revised = suite["manifest"]["version"] in {REVISED_VERSION, CONSULTATION_VERSION}
            if revised:
                for row in json.loads((dataset / "sku_facts.json").read_text()):
                    for sku in row["skus"]:
                        conn.execute("UPDATE product_skus SET wearing_type=?,anc=?,color=?,"
                                     "availability=? WHERE sku_id=?", (
                                         sku["wearing_style"], sku["anc"], sku["color"],
                                         sku["availability"], sku["sku_id"]))
                for row in json.loads((dataset / "logistics.json").read_text()):
                    trace = row["trace"]
                    conn.execute("UPDATE logistics SET carrier=?,current_city=?,updated_at=?,"
                                 "trace_json=? WHERE order_id=?", (
                                     row["carrier"], trace[-1]["location"], trace[-1]["time"],
                                     json.dumps(trace, ensure_ascii=False), row["order_id"]))
            for order in data["orders"] if not revised else []:
                if not order["shipped_at"]:
                    continue
                route = sources["logistics_route"]["nodes"]
                route = route if order["delivered_at"] else route[:6]
                start = datetime.fromisoformat(order["shipped_at"]) + timedelta(hours=8)
                end = (datetime.fromisoformat(order["delivered_at"]) + timedelta(hours=16)
                       if order["delivered_at"] else start + timedelta(hours=12))
                trace = [dict(time=(start + (end-start)*i/(len(route)-1)).isoformat(
                    timespec="seconds"), text=f"{node['status']} · {node['location']} · "
                    + node["description"], location=node["location"], status=node["status"])
                    for i, node in enumerate(route)]
                conn.execute("UPDATE logistics SET carrier=?,current_city=?,updated_at=?,"
                             "trace_json=? WHERE order_id=?", (
                                 "京东快递（历史路线模拟）", route[-1]["location"],
                                 trace[-1]["time"], json.dumps(trace, ensure_ascii=False),
                                 order["id"],
                             ))
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Invalid demo references")

    with (patch.object(m8, "NOW", datetime.fromisoformat(suite["manifest"]["clock"])),
          m8.business_clock(), patch("acs.local_return_tools.build", seed)):
        init_formal_fixture(root)
    shutil.rmtree(root / "docs")
    shutil.copytree(dataset / "docs", root / "docs")
    dump(root / "demo_data.json", suite["manifest"])
    return suite["manifest"]
