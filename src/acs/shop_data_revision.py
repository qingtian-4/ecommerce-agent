"""Offline v1.2 data revision; synthetic facts stay separate from source claims."""
from __future__ import annotations

import copy
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone

from .commerce_benchmark_data import dump, file_hash
from .config import PROJECT_ROOT

INPUTS = PROJECT_ROOT / "data/demo_revision_v1"
VERSION = "shop-realism-v1.2"
CONSULTATION_VERSION = "shop-realism-v1.3"
AFTER_SALES_VERSION = "shop-realism-v1.4"


def enable_consultation(out):
    """Activate only consultation; operational return_policy stays unchanged."""
    draft = json.loads((out / "drafts/appliance_policy.json").read_text())
    policy = dict(version="appliance-consultation-v1", mode="consultation_only",
        simulation_only=True, consultation_enabled=True, application_enabled=False,
        categories=draft["categories"], no_reason_days=draft["proposed_no_reason_days"],
        quality_return_days=draft["proposed_quality_return_days"],
        warranty_months=draft["proposed_warranty_months"], starting_point="签收日",
        boundary_status="month_end_and_component_terms_require_review")
    body = ("---\ndocument_id: appliance_consultation\ntitle: 家电模拟政策咨询\n"
            "category: policy\nversion: appliance-consultation-v1\n---\n\n"
            "# 家电模拟政策咨询（仅咨询）\n\n"
            "仅用于本地合成演示，不代表任何品牌、平台或法律承诺。适用品类："
            + "、".join(policy["categories"]) + "。\n\n"
            f"模拟无理由退货期限：签收日起{policy['no_reason_days']}天；"
            "需未拆封、配件包装齐全且不影响二次销售。\n\n"
            f"模拟质量问题退货期限：签收日起{policy['quality_return_days']}天；"
            "需非人为功能故障并核验凭证。\n\n"
            f"模拟整机保修期：签收日起{policy['warranty_months']}个月；"
            "需核验本人订单、签收日期、购买凭证和故障描述。用户自述不等于故障已确认。"
            "月末边界、部件差异和特殊情况尚未确定，不能自动判定资格。\n\n"
            "用户只说用了两个月时，可以解释模拟期限，但不能把使用时长当成已核验签收日期。"
            "多商品订单必须逐明细区分耳机政策与家电模拟政策。\n\n"
            "当前只支持模拟政策咨询，不支持家电退货预览、申请提交、退款、安装预约、维修"
            "派单或换货发货；即使用户确认也不能办理。上游历史安装维修资料不是本店政策。\n")
    path = out / "docs/appliance_consultation.md"
    path.write_text(body)
    policy["document_sha256"] = file_hash(path)
    dump(out / "docs/appliance_consultation.json", policy)
    (out / "docs/appliance_policy_boundary.md").write_text(
        "---\ndocument_id: appliance_policy_boundary\ntitle: 家电办理边界\ncategory: policy\n"
        f"version: {CONSULTATION_VERSION}\n---\n\n# 家电办理边界\n\n"
        "已启用家电模拟政策咨询，规则见appliance_consultation。耳机和充电配件条款"
        "不能套用于家电。家电预览、申请、退款、安装、维修、换货办理仍未启用；"
        "聊天或页面确认均不能扩大权限。模拟咨询不代表真实售后资格。\n")
    scope = out / "docs/demo_data_scope.md"
    scope.write_text(scope.read_text().replace(
        "家电政策草案不在当前知识库中，不得承诺家电保修或售后办理。",
        "家电模拟政策已启用咨询，详见appliance_consultation；仍不能承诺真实保修或办理售后。"))
    for name in ("return_policy", "warranty_policy"):
        path = out / "docs" / (name+".md")
        path.write_text(path.read_text().replace(
            "详见家电政策边界说明。",
            "另有家电模拟政策仅供咨询，详见appliance_consultation和家电办理边界。"))


def logistics_rows(business, spec):
    zone = timezone(timedelta(hours=8))
    snapshot = datetime.combine(date.fromisoformat(business["reference_date"]), time(12), zone)
    rows = []
    for order in business["orders"]:
        if not order["shipped_at"]:
            continue
        rng = random.Random(int(hashlib.sha256(
            f"{spec['seed']}:{order['id']}".encode()).hexdigest(), 16))
        # One synthetic destination per user, independent of which product was bought.
        route = spec["routes"][(int(order["user_id"][-3:])-1) % len(spec["routes"])]
        origin, transit, destination = route["cities"]
        start = datetime.combine(date.fromisoformat(order["shipped_at"]),
                                 time(7+rng.randrange(4), rng.randrange(60)), zone)
        nodes = [(origin+"仓库", "已揽收"), (origin+"分拨中心", "已出库"),
                 (transit+"转运中心", "运输中"), (destination+"分拨中心", "已到达"),
                 (destination+"配送网点", "派送中"), (destination+"配送网点", "已签收")]
        if order["delivered_at"]:
            end = datetime.combine(date.fromisoformat(order["delivered_at"]),
                                   time(15+rng.randrange(5), rng.randrange(60)), zone)
        else:
            nodes = nodes[:rng.randint(2, 4)]
            end = min(snapshot-timedelta(hours=rng.randint(1, 6)),
                      start+timedelta(hours=rng.randint(8, 20), minutes=rng.randrange(60)))
        if not start < end <= snapshot:
            raise ValueError("Order dates cannot support the synthetic route")
        weights = [rng.randint(2, 12) for _ in nodes[1:]]
        elapsed, total, trace = 0, sum(weights), []
        for i, (place, status) in enumerate(nodes):
            if i:
                elapsed += weights[i-1]
            stamp = start+(end-start)*elapsed/total
            trace.append(dict(time=stamp.isoformat(timespec="seconds"), status=status,
                              location=place, text=f"模拟包裹：{place}，{status}"))
        rows.append(dict(order_id=order["id"], route_id=route["id"],
                         destination_city=destination, carrier="合成区域配送", synthetic=True,
                         timestamp_source="derived_from_synthetic_order_dates", trace=trace))
    return rows


def policy_draft(policy):
    rows = ["| 类别 | 无理由 | 质量问题 | 保修 | 证据与缺失处理 |",
            "|---|---|---|---|---|"]
    for category in policy["categories"]:
        rows.append(f"| {category} | {policy['proposed_no_reason_days']}天 | "
                    f"{policy['proposed_quality_return_days']}天 | "
                    f"{policy['proposed_warranty_months']}个月 | "
                    "本人订单、签收日期、购买凭证、故障描述；缺失时无法判断 |")
    return ("---\nstatus: draft_not_active\napplication_enabled: false\n"
            "simulation_only: true\n---\n\n# 模拟家电政策草案（未启用）\n\n"
            "这是一份人工设定的测试假设，不代表品牌、平台或法律规则；"
            "禁止作为当前客服的适用政策或申请授权。\n\n" + "\n".join(rows) +
            "\n\n上述窗口拟从签收日起算，月份按日历月计算；这项语义尚待业务确认。"
            "故障是否非人为需要核验，用户描述不等于已确认的故障事实。"
            "订单和签收日期须由身份绑定的业务工具核验，凭证需对应商品及序列号。\n\n"
            "只有未来另行确认启用后，受信任页面明确确认才最多创建模拟待审核记录；"
            "不退款、不维修派单、不安装预约、不换货发货。当前家电售后仍未启用。\n\n"
            "## 四个草案问答\n\n"
            "问：热水器用了两个月点火异常，能保修吗？\n"
            "答：当前缺少已启用的适用政策，不能承诺；草案期限不构成资格依据。\n\n"
            "问：没有购买凭证怎么办？\n答：记录缺失项，先核验本人订单与购买凭证。\n\n"
            "问：已超过草案期限能申请吗？\n答：不能根据此草案批准，需另核实适用规则。\n\n"
            "问：一张订单同时有耳机和热水器呢？\n"
            "答：逐条绑定商品、数量与适用范围，耳机规则不能迁移到热水器。\n")


def revise(out):
    """Called only while constructing a new dataset, before its manifest is frozen."""
    spec = json.loads((INPUTS / "inputs.json").read_text())
    generator_spec = json.loads((PROJECT_ROOT / "data/demo_generation_v1/spec.json").read_text())
    business = json.loads((out / "business.json").read_text())
    sources = json.loads((out / "sources.json").read_text())
    sources["products"].extend(copy.deepcopy(spec["products"]))
    sources["revision"] = VERSION
    sources["revision_inputs_sha256"] = file_hash(INPUTS / "inputs.json")
    today = date.fromisoformat(spec["reference_date"])
    offset = today-date.fromisoformat(business["reference_date"])
    for order in business["orders"]:
        for key in ("created_at", "shipped_at", "delivered_at"):
            if order[key]:
                order[key] = (date.fromisoformat(order[key])+offset).isoformat()
    business["reference_date"] = today.isoformat()
    for p in spec["products"]:
        business["products"].append({k: p[k] for k in ("id", "name", "category", "attributes")}
                                    | dict(price=p["price"], current_price=p["price"]))
    # One additional order per catalog entity covers products absent from legacy orders.
    for i, product in enumerate(business["products"]):
        status, age = [("delivered", 2), ("delivered", 20), ("shipped", None),
                       ("pending", None), ("cancelled", None)][i % 5]
        delivered = today-timedelta(days=age) if age else None
        shipped = (delivered-timedelta(days=2) if delivered else today-timedelta(days=1))
        business["orders"].append(dict(
            id=f"O830{i+1:03}", user_id=f"USER{i % 8 + 1:03}", status=status,
            created_at=(shipped-timedelta(days=2)).isoformat(),
            shipped_at=shipped.isoformat() if status in {"shipped", "delivered"} else None,
            delivered_at=delivered.isoformat() if delivered else None,
            items=[dict(id=830000+i, product_id=product["id"], quantity=1,
                        price=product["price"])]))
    for user in business["users"]:
        route = generator_spec["routes"][(int(user["id"][-3:])-1) % len(generator_spec["routes"])]
        user["synthetic_destination_city"] = route["cities"][-1]
    dump(out / "business.json", business)
    dump(out / "sources.json", sources)
    dump(out / "logistics.json", logistics_rows(business, generator_spec))
    sku_facts = []
    for p in business["products"]:
        if p["id"].startswith("E"):
            skus = [dict(sku_id=p["id"]+"-1", wearing_style=None, anc=None,
                         color=None, availability="unknown")]
        else:
            attrs = p["attributes"]
            anc = 1 if "主动降噪" in attrs else 0 if "不支持主动降噪" in attrs else None
            skus = [dict(sku_id=p["id"]+f"-{n}",
                wearing_style=attrs[0] if p["category"] == "无线耳机" else None,
                anc=anc, color=color,
                availability=("unknown" if p["id"] == "B107" and n == 2 else
                              "unavailable" if p["id"] == "B104" and n == 2 else "available"))
                for n, color in enumerate(["黑色", "白色"], 1)]
        sku_facts.append(dict(product_id=p["id"], skus=skus,
            provenance="authored_synthetic_color_availability; ANC only from explicit attributes"))
    dump(out / "sku_facts.json", sku_facts)
    for p in sources["products"]:
        (out / "docs" / (p["id"]+".md")).write_text(
            f"---\ndocument_id: {p['id']}\ntitle: {p['name']}\ncategory: product\n"
            f"product_id: {p['id']}\nversion: {VERSION}\n---\n\n# {p['name']}\n\n"
            f"来源 ECom-Bench {p['source_product_id']} 的历史测试资料，"
            "不代表品牌核实规格、当前价格或服务承诺。金额和订单请查业务工具。\n\n" +
            "\n\n".join(p["attributes"])+"\n")
    drafts = out / "drafts"
    drafts.mkdir()
    policy = copy.deepcopy(generator_spec["policy"])
    policy["pending_decisions"] = ["起算点与月末日历计算需业务确认", "类别与部件差异尚未确认"]
    dump(drafts / "appliance_policy.json", policy)
    (drafts / "appliance_policy.md").write_text(policy_draft(policy))
    # Drafts never enter docs/, the only runtime retrieval source.
    (out / "docs/demo_data_scope.md").write_text(
        f"---\ndocument_id: demo_data_scope\ntitle: 演示数据来源和未知字段\n"
        f"category: policy\nversion: {VERSION}\n---\n\n# 演示数据边界\n\n"
        f"业务快照日期为{today.isoformat()}，不是实时时钟。所有用户、订单、价格和物流"
        "仅用于本地模拟，物流来自固定种子生成，不是承运商回传。耳机和充电配件是"
        "8种参数模板的5个系列；不是40种独立复杂场景。黑白SKU及可售状态为合成事实，"
        "不表示真实库存件数。未注明主动降噪的商品记为未知，不能说不支持。\n\n"
        "家电仅导入经过筛选的历史商品摘录。安装、故障码、赠品和维修资料仍为待审来源，"
        "家电政策草案不在当前知识库中，不得承诺家电保修或售后办理。\n")
    for name in ("return_policy", "warranty_policy", "price_protection", "shipping_policy"):
        path = out / "docs" / (name+".md")
        text = path.read_text().replace("version: shop-realism-v1.1", f"version: {VERSION}")
        text = text.replace("version: v1\n", f"version: {VERSION}\n")
        text = text.replace("---\n\n#", "---\n\n本文件为本地模拟咨询规则，"
            "不是平台承诺；实际系统只提供查询及已支持品类的模拟待审申请，"
            "没有退款、维修、换货、物流催办或安装派单。\n\n#", 1)
        if name == "shipping_policy":
            text = text.replace("物流信息由快递公司回传，通常有 **2-4 小时**的更新延迟。",
                                "本演示物流由固定流程生成，时间为带时区的快照时间，不是实时回传。")
            text = text.replace("核实后我们会联系快递公司催件或安排补发、退款。",
                                "当前只能解释轨迹和记录咨询，不能联系承运商、补发或退款。")
        path.write_text(text)
    cases = []
    for p in business["products"]:
        document = (p["id"] if p["id"].startswith("E") else
                    f"catalog{(int(p['id'][1])-1)*4+(int(p['id'][-1])-1)//2:02}")
        cases.append(dict(id="DATA-"+p["id"], product_id=p["id"],
            query=f"{p['id']} {p['name']} 的规格资料", document_id=document,
            required_facts=[p["attributes"][0], p["attributes"][2]],
            legal_alternative="Any retrieved chunk from the same document containing the fact",
            semantic_status="pending_independent_review"))
    dump(out / "rag_cases.json", dict(version=VERSION+"-retrieval-v1",
        kind="authored_development_evidence_controls_not_unseen_tasks", cases=cases))
    return business, sources
