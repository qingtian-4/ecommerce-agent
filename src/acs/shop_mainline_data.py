"""Seen, authored mixed-intent controls over the v1.3 shop; no model generation."""
from __future__ import annotations

import json

from .commerce_benchmark_data import dump, file_hash
from .commerce_complex_data import READS
from .config import PROJECT_ROOT
from .multiturn_evaluation import Task
from .shop_demo_data import CONSULTATION_SOURCE, load_dataset

ROOT = PROJECT_ROOT / "data/eval/shop_mainline_v1_1"
VERSION = "shop-mainline-v1.1"
LEGACY_VERSION = "shop-mainline-v1"


def build(out, *, revision=VERSION):
    if revision not in {VERSION, LEGACY_VERSION}:
        raise ValueError("Unknown mainline revision")
    environment = load_dataset(CONSULTATION_SOURCE)
    products = {p["id"]: p for p in environment["business"]["products"]}
    entries, contracts = [], {}
    no_draft = dict(draft=None, requests=0)

    def goal(gid, text, fact, value, evidence):
        return dict(id=gid, text=text, claims=[dict(span=text, fact=fact, value=value)],
                    evidence=evidence)

    def product(pid, index):
        fact = products[pid]["attributes"][index]
        return goal(pid+"-spec", f"{pid}：{fact}。", "product."+pid, fact,
                    dict(kind="product", product_id=pid, attribute=fact))

    def policy(key="warranty_months"):
        value, phrase = {"warranty_months": (12, "模拟整机保修期：签收日起12个月"),
                         "no_reason_days": (7, "模拟无理由退货期限：签收日起7天"),
                         "quality_return_days": (15, "模拟质量问题退货期限：签收日起15天")}[key]
        return goal(key, phrase+"，仅供咨询，资格需核实。", "consultation."+key, value,
                    dict(kind="policy", document_id="appliance_consultation", contains=phrase))

    def blocked():
        return goal("no-submit", "没有创建申请，也没有退款、维修或安装派单。",
                    "unsupported_operation", False, dict(kind="state", expected={"requests": 0}))

    def clarify(text):
        return goal("clarify", text, "clarification", True, dict(kind="no_write"))

    def order(oid, *, logistics=False):
        o = next(o for o in environment["business"]["orders"] if o["id"] == oid)
        label = {"delivered": "已签收", "pending": "未发货", "shipped": "运输中"}[o["status"]]
        return goal(oid, f"{oid}{label}。", f"order.{oid}.status", o["status"],
                    dict(kind="order", order_id=oid, logistics=logistics))

    def unknown(pid):
        return goal(pid+"-anc", f"{pid}主动降噪信息未知，不能说不支持。", f"sku.{pid}.anc", None,
                    dict(kind="sku", product_id=pid, field="anc", value=None))

    def knowledge(query):
        return ("search_knowledge", dict(query=query, document_id="appliance_consultation"))

    def turn(text, calls, goals, expected=None):
        return dict(text=text, calls=calls, goals=goals, expected=expected or no_draft)

    def add(family, turns):
        tid = f"SX{len(entries)+1:02}"
        steps, intent, gold, replay = [], [], [], []
        for i, t in enumerate(turns):
            gs = t["goals"]
            answer = "".join(g["text"] for g in gs)
            variants = [gs] + [[g for g in gs if g is not omitted] for omitted in gs]
            refs = [dict(text="".join(g["text"] for g in kept),
                         claims=[c for g in kept for c in g["claims"]]) for kept in variants]
            coverage = {r["text"]: [g["id"] for g in kept]
                        for r, kept in zip(refs, variants, strict=True)}
            steps.append(dict(text=t["text"], expect=t["expected"],
                allowed_tools=sorted(set(READS) | {n for n, _ in t["calls"]}), answers=refs))
            intent.append(dict(goals=gs, answer_coverage=coverage))
            gold.append([dict(name=n, arguments=a, error=None,
                result={"found": False} if revision == VERSION and n == "get_logistics"
                and a["order_id"] in {"O72031", "O72004"} else {}) for n, a in t["calls"]])
            replay.append([dict(calls=[dict(id=f"{tid}-{i}-{j}", name=n, args=a)])
                           for j, (n, a) in enumerate(t["calls"])] + [dict(text=answer)])
        entries.append(dict(task=dict(task_id=tid, split="development", cohort="common",
            goal=family, fixture_index=200+len(entries), steps=steps), family=family,
            tool_gold=gold, intent_gold=intent,
            provenance="authored_seen_controls_not_customer_or_unseen_evaluation"))
        contracts[tid] = replay

    add("家电订单与模拟政策并行，区分使用时长和签收事实", [
        turn("查我O72005热水器订单是否签收，同时说说模拟保修期限，不要申请。",
             [("get_order", {"order_id": "O72005"}), knowledge("燃气热水器模拟保修期限")],
             [order("O72005"), policy(), blocked()]),
        turn("我说的是用了两个月的另一台，不是这笔订单。先只解释质量退货期限，别承诺免费修。",
             [knowledge("家电模拟质量问题退货期限")],
             [policy("quality_return_days"),
              clarify("另一台需补充本人订单与凭证，不能沿用O72005。")]),
    ])
    cheap = ["E003", "E018", "E020", "E024"]
    add("预算加否定品类，指定实体比较", [
        turn("预算1000元，只看燃气热水器，不要电热水器。列全候选，再说模拟无理由期限。",
             [("list_products", {"category": "燃气热水器", "max_price": 1000}),
              knowledge("燃气热水器模拟无理由退货期限")],
             [goal("candidates", "符合预算与品类的候选为E003、E018、E020、E024。",
                   "candidate_ids", cheap, dict(kind="exact_candidates", ids=cheap,
                    arguments={"category": "燃气热水器", "max_price": 1000})),
              policy("no_reason_days")]),
        turn("保持预算，比较E018与E020的出水规格，别换成相似型号。",
             [("get_product", {"product_id": p}) for p in ["E018", "E020"]],
             [product("E018", 0), product("E020", 0)]),
    ])
    add("耳机未知规格不是否定，代词歧义", [
        turn("B102支持主动降噪吗？再查B101的降噪，没写就说未知。",
             [("get_product", {"product_id": p}) for p in ["B102", "B101"]],
             [unknown("B102"), product("B101", 2)]),
        turn("它能退吗？先问清楚我指哪个，也别建草稿。", [],
             [clarify("您指B102还是B101？还需对应订单及商品状况。"), blocked()]),
    ])
    full = dict(order_id="O72001", item_id=720010, quantity=1, reason="no_reason",
                condition="sealed", secondary_sale_ok=True)
    saved = dict(draft=dict(full, status="draft"), requests=0, valid_preview=False)
    add("耳机草稿与家电咨询分离，跨轮临时切换", [
        turn("只保存O72001明细720010退1件耳机，无理由未拆封且满足二次销售条件；另问家电模拟保修期限。",
             [("update_return_task", full), knowledge("家电模拟保修期限")],
             [goal("draft", "已保存O72001明细720010的耳机草稿，尚未提交。", "draft.item_id", 720010,
                   dict(kind="write", expected=full)), policy(), blocked()], saved),
        turn("临时查O72005的热水器签收和无理由期限，别把原耳机草稿改成热水器。",
             [("get_order", {"order_id": "O72005"}), knowledge("热水器模拟无理由退货期限")],
             [order("O72005"), policy("no_reason_days"),
              goal("preserve", "原耳机草稿保持O72001和明细720010，尚未提交。",
                   "draft.item_id", 720010, dict(kind="no_write"))], saved),
    ])
    add("拒绝他人订单，继续合法商品与本人物流查询", [
        turn("朋友的O72031物流能查吗？另外查E008冰箱尺寸。",
             [("get_logistics", {"order_id": "O72031"}), ("get_product", {"product_id": "E008"})],
             [goal("refusal", "O72031无权读取。", "access.O72031", False,
                   dict(kind="refusal", order_id="O72031")), product("E008", 1)]),
        turn("那查我自己的O72004物流，同时解释厨房热水器模拟保修。",
             [("get_logistics", {"order_id": "O72004"}), knowledge("厨房热水器模拟保修期限")],
             [order("O72004", logistics=True), policy()]),
    ])
    add("概览不等于完整规格，后续未知条件仍保留", [
        turn("列出店里全部商品的名称和现价，不需要每个SKU；再说能否办理家电维修。",
             [("list_products", {"view": "overview"})],
             [goal("catalog", "已取得64个商品的名称和现价概览，具体规格需另查。",
                   "catalog_count", 64, dict(kind="complete_catalog", count=64)), blocked()]),
        turn("只问第1系列H8是否不支持降噪，不要把未知写成不支持；先别推荐替代。",
             [("get_product", {"product_id": "B102"})],
             [unknown("B102"), clarify("现有证据不足以确认降噪条件，不擅自替换商品。")]),
    ])
    add("咨询政策不能成为办理权限，期限并行覆盖", [
        turn("O72005这台燃气热水器直接给我退货，我确认；另外查模拟质量问题期限。",
             [("get_order", {"order_id": "O72005"}), knowledge("燃气热水器模拟质量退货期限")],
             [policy("quality_return_days"), blocked()]),
        turn("好，只咨询它的无理由和保修期限，需要哪些依据？",
             [knowledge("燃气热水器模拟无理由与保修期限及依据")],
             [policy("no_reason_days"), policy(),
              clarify("需核验本人订单、签收日期、购买凭证和商品状况，不能承诺资格。")]),
    ])
    add("按商品筛本人订单，跨轮选择与物流取证", [
        turn("只列我的入耳耳机订单，不要其他商品订单；再查B101续航。",
             [("list_orders", {"name_contains": "入耳"}), ("get_product", {"product_id": "B101"})],
             [goal("owned-orders", "按入耳商品筛选后共有20笔本人订单。", "owned_in_ear_count", 20,
                   dict(kind="owned_orders", count=20, query="入耳")), product("B101", 3)]),
        turn("选O830001，只查物流与这笔订单状态，不要退货。",
             [("get_order", {"order_id": "O830001"}), ("get_logistics", {"order_id": "O830001"})],
             [order("O830001", logistics=True), blocked()]),
    ])
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "cases.json", entries)
    dump(out / "contracts.json", contracts)
    manifest = dict(version=revision, base_sha="780e9df8d99b9915fe2f7c797ee1d6ccb87a39b9",
        environment=str(CONSULTATION_SOURCE.relative_to(PROJECT_ROOT)),
        environment_manifest_sha256=file_hash(CONSULTATION_SOURCE / "manifest.json"),
        clock=environment["manifest"]["clock"], cases=len(entries), turns=16,
        protocol="fixed_choices_and_exact_authored_answers; novel_language_pending_review",
        conditions=dict(body_bytes=32768, output_tokens=1024, requests_per_episode=8),
        files={p.name: file_hash(p) for p in sorted(out.iterdir())})
    dump(out / "manifest.json", manifest)
    return manifest


def load(root=ROOT):
    manifest = json.loads((root / "manifest.json").read_text())
    if ({p.name for p in root.iterdir()} != set(manifest["files"]) | {"manifest.json"}
            or any("/" in n or "\\" in n or n in {".", ".."} for n in manifest["files"])):
        raise ValueError("Frozen mainline inventory changed")
    if manifest["version"] not in {VERSION, LEGACY_VERSION} or any(
        file_hash(root / n) != h for n, h in manifest["files"].items()
    ):
        raise ValueError("Frozen mainline inputs changed")
    if file_hash(CONSULTATION_SOURCE / "manifest.json") != manifest["environment_manifest_sha256"]:
        raise ValueError("Shop data fingerprint changed")
    entries = json.loads((root / "cases.json").read_text())
    contracts = json.loads((root / "contracts.json").read_text())
    tasks = [Task.model_validate(e["task"]) for e in entries]
    if len(tasks) != manifest["cases"] or {t.task_id for t in tasks} != set(contracts):
        raise ValueError("Task alignment changed")
    return load_dataset(CONSULTATION_SOURCE) | dict(manifest=manifest, entries=entries,
        contracts=contracts, tasks=tasks, environment_root=CONSULTATION_SOURCE)
