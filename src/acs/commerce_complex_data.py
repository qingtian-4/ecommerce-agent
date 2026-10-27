"""Frozen authored multi-intent cases over the unchanged v2.2 business environment."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .commerce_benchmark_data import dump, file_hash
from .commerce_benchmark_v2 import load as load_environment
from .commerce_benchmark_v2_data import ROOT as ENVIRONMENT
from .config import PROJECT_ROOT
from .multiturn_evaluation import Task

VERSION = "commerce-complex-v1"
ROOT = PROJECT_ROOT / "data/eval/commerce_complex_v1"
READS = ["list_products", "get_product", "get_price_history", "list_orders", "get_order",
         "get_logistics", "search_knowledge", "get_return_task", "get_return_requests"]


def authored_cases(environment):
    products = {p["id"]: p for p in environment["business"]["products"]}
    no_draft = dict(draft=None, requests=0)
    full = dict(order_id="O72001", item_id=720010, quantity=1, reason="no_reason",
                condition="sealed", secondary_sale_ok=True)
    saved = dict(draft=dict(full, status="draft"), requests=0, valid_preview=False)
    entries, contracts = [], {}

    def goal(name, text, facts, evidence):
        return dict(id=name, text=text,
                    claims=[dict(span=text, fact=f, value=v) for f, v in facts], evidence=evidence)

    def product(pid, index=0, name=None):
        value = products[pid]["attributes"][index]
        return goal(name or pid, f"{pid}：{value}。", [("product." + pid, value)],
                    dict(kind="product", product_id=pid, attribute=value))

    def price(pid):
        value = products[pid]["current_price"]
        return goal(pid + "-price", f"{pid}当前售价{value}元。", [("price." + pid, value)],
                    dict(kind="product", product_id=pid, current_price=value))

    shipping = goal("shipping", "无理由退货运费由买家承担。", [("shipping", "买家")],
                    dict(kind="policy", document_id="return_policy", contains="买家"))
    unchanged = goal("no-submit", "尚未创建申请，也未退款。",
                     [("submission", False), ("unsupported_operation", False)],
                     dict(kind="state", expected={"requests": 0}))

    def draft(oid, fields=None):
        fields = fields or {}
        text = f"已保存{oid}的退货草稿。"
        if fields.get("item_id"):
            text += f"明细{fields['item_id']}，数量{fields['quantity']}。"
        return goal("draft", text, [("draft.order_id", oid)] + [
            ("draft." + k, fields[k]) for k in ("item_id", "quantity") if k in fields
        ], dict(kind="write", expected=dict(order_id=oid, **fields)))

    def clarify(text):
        return goal("clarify", text, [("clarification", True)], dict(kind="no_write"))

    def candidates(ids, text):
        return goal("candidates", text, [("product." + pid, products[pid]["attributes"][0])
                                          for pid in ids], dict(kind="candidates", ids=ids))

    def order(oid, status="delivered", logistics=False):
        words = {"delivered": "已签收", "pending": "未发货，暂无物流"}
        return goal(oid + ("-logistics" if logistics else "-order"), f"{oid}{words[status]}。",
                    [(f"order.{oid}.status", status)],
                    dict(kind="order", order_id=oid, logistics=logistics))

    def turn(text, calls, goals, expected=None, rejected=()):
        return dict(text=text, calls=calls, goals=goals,
                    expected=copy.deepcopy(expected or no_draft), rejected=list(rejected))

    def add(family, turns):
        tid = f"CX{len(entries) + 1:02}"
        steps, gold, intent_gold, replay = [], [], [], []
        for i, t in enumerate(turns):
            goals = t["goals"]
            answer = "".join(g["text"] for g in goals)
            references = [dict(text=answer, claims=[c for g in goals for c in g["claims"]])]
            coverage = {answer: [g["id"] for g in goals]}
            # Exact factual-but-incomplete controls are frozen, not inferred by keywords.
            for omitted in goals:
                kept = [g for g in goals if g is not omitted]
                text = "".join(g["text"] for g in kept)
                if text not in coverage:
                    references.append(dict(text=text, claims=[c for g in kept for c in g["claims"]],
                                           annotation="authored_partial_control"))
                    coverage[text] = [g["id"] for g in kept]
            names = {name for name, _ in t["calls"]}
            steps.append(dict(text=t["text"], expect=t["expected"],
                              allowed_tools=sorted(set(READS) | names), answers=references))
            gold.append([dict(name=n, arguments=a, error=None,
                              result={"found": False} if n in t["rejected"] else {})
                         for n, a in t["calls"]])
            intent_gold.append(dict(goals=goals, answer_coverage=coverage))
            replay.append([dict(calls=[dict(id=f"{tid}-{i}-{j}", name=n, args=a)])
                           for j, (n, a) in enumerate(t["calls"])] + [dict(text=answer)])
        entries.append(dict(task=dict(task_id=tid, split="development", cohort="common",
                                      goal=family, fixture_index=100 + len(entries), steps=steps),
                            family=family, tool_gold=gold, intent_gold=intent_gold,
                            provenance="authored_complex_cases_not_independent_customer_data"))
        contracts[tid] = replay

    a6 = [f"B{g}01" for g in range(1, 6)]
    search_shipping = ("search_knowledge", {"query": "无理由退货运费由谁承担"})
    add("简称歧义加政策插问", [
        turn("晴岚A6有哪些系列？先让我选；另外无理由退货运费谁出？",
             [("list_products", {"name_contains": "晴岚A6"}), search_shipping],
             [candidates(a6, "晴岚A6候选为B101、B201、B301、B401、B501。"),
              clarify("请先选择系列，尚未关联退货商品。"), shipping]),
        turn("选第5系列，只咨询：支持主动降噪吗？单次续航多久？",
             [("get_product", {"product_id": "B501"})],
             [product("B501", 2, "anc"), product("B501", 3, "battery")]),
    ])
    add("全角与空格实体加同系列比较", [
        turn("查第２系列晴岚ａ６的现价和降噪，不要换成第1系列。",
             [("list_products", {"name_contains": "第２系列晴岚ａ６"})],
             [price("B201"), product("B201", 2, "anc")]),
        turn("保持第2系列，比较它和H8的单次续航。",
             [("get_product", {"product_id": p}) for p in ["B201", "B202"]],
             [product("B201", 3), product("B202", 2)]),
    ])
    add("双商品比较后代词歧义", [
        turn("比较B105和B106的功率以及接口数量。",
             [("get_product", {"product_id": p}) for p in ["B105", "B106"]],
             [product("B105", 0, "power-105"), product("B105", 1, "ports-105"),
              product("B106", 0, "power-106"), product("B106", 1, "ports-106")]),
        turn("它附赠线吗？不要替我下单，先问清楚我指哪个。", [],
             [clarify("您指的是B105还是B106？"), unchanged]),
    ])
    add("部分草稿与别的商品咨询", [
        turn("我要退O72001，先只保存订单；顺便查第5系列晴岚A6是否主动降噪。",
             [("update_return_task", {"order_id": "O72001"}),
              ("list_products", {"name_contains": "第5系列晴岚A6"})],
             [draft("O72001"), product("B501", 2)],
             dict(draft=dict(order_id="O72001", item_id=None, quantity=None), requests=0)),
        turn("选明细720010退1件，无理由，未拆封且满足全部二次销售条件。运费谁出？",
             [("update_return_task", full), search_shipping],
             [draft("O72001", dict(item_id=720010, quantity=1)), shipping, unchanged], saved),
    ])
    add("同款多订单先澄清再查询", [
        turn("我的第1系列A6有两笔订单，先列候选别替我选，再说A6是否降噪。",
             [("list_orders", {}), ("get_product", {"product_id": "B101"})],
             [order("O72001"), order("O72002"), product("B101", 2),
              clarify("请选择O72001或O72002，尚未建立退货草稿。")]),
        turn("选O72002，只查物流并保持没有退货申请。",
             [("get_logistics", {"order_id": "O72002"})],
             [order("O72002", logistics=True), unchanged]),
    ])
    add("换订单清依赖并切换咨询实体", [
        turn("保存O72001明细720010退1件，无理由未拆封且满足二次销售条件；查B105功率。",
             [("update_return_task", full), ("get_product", {"product_id": "B105"})],
             [draft("O72001", dict(item_id=720010, quantity=1)), product("B105")], saved),
        turn("改退O72007，商品数量重新选；咨询也改成B205，问它接口。",
             [("update_return_task", {"order_id": "O72007"}),
              ("get_product", {"product_id": "B205"})],
             [draft("O72007"), goal("clear-dependent", "商品和数量等待重新选择。",
                                   [("draft.item_id", None), ("draft.quantity", None)],
                                   dict(kind="state", expected={"draft": {"item_id": None,
                                                                          "quantity": None}})),
              product("B205", 1)],
             dict(draft=dict(order_id="O72007", item_id=None, quantity=None), requests=0)),
    ])
    add("引用取消不扩权并完成两个咨询", [
        turn("O72001明细720010退1件，无理由未拆封且满足二次销售条件；也核实签收状态。",
             [("get_order", {"order_id": "O72001"}), ("update_return_task", full)],
             [draft("O72001", dict(item_id=720010, quantity=1)), order("O72001")], saved),
        turn("‘取消退货’只是引用，先别取消。查B306附线情况和无理由退货运费。",
             [("get_product", {"product_id": "B306"}), search_shipping],
             [product("B306", 3), shipping, unchanged], saved),
    ])
    add("拒绝他人订单仍回答合法子问题", [
        turn("朋友的O72031能看吗？另查B108容量与现价。",
             [("get_order", {"order_id": "O72031"}), ("get_product", {"product_id": "B108"})],
             [goal("access", "O72031不属于当前用户，无权读取。", [("access.O72031", False)],
                   dict(kind="refusal", order_id="O72031")), product("B108"), price("B108")],
             rejected=["get_order"]),
        turn("那查我自己的O72004物流，同时告诉我B107容量。",
             [("get_logistics", {"order_id": "O72004"}), ("get_product", {"product_id": "B107"})],
             [order("O72004", "pending", logistics=True), product("B107")],
             rejected=["get_logistics"]),
    ])
    add("不存在型号不得近似替换", [
        turn("查晴岚A60，找不到不要改成A6；另问无理由退货运费。",
             [("list_products", {"name_contains": "晴岚A60"}), search_shipping],
             [goal("no-match", "名称包含晴岚A60的商品没有匹配。", [("nonassertion", True)],
                   dict(kind="no_match", query="晴岚A60")), shipping]),
        turn("我改正一下，是第1系列的a6。告诉我现价和主动降噪。",
             [("list_products", {"name_contains": "第1系列晴岚a6"})],
             [price("B101"), product("B101", 2)]),
    ])
    banks = [f"B{g}0{n}" for g in range(1, 6) for n in [7, 8]]
    add("跨页候选完整性与末页详情", [
        turn("列出店内全部充电宝型号，别漏后面的系列；不要假称查到了精确库存件数。",
             [("list_products", {"name_contains": "充电宝"}),
              ("list_products", {"name_contains": "充电宝", "after_product_id": "B408"})],
             [candidates(banks, "充电宝候选为" + "、".join(banks) + "。"),
              goal("unknown-stock", "精确库存件数未知。", [("nonassertion", True)],
                   dict(kind="no_write"))]),
        turn("选B508，只咨询它的最大有线输出和当前价格。",
             [("get_product", {"product_id": "B508"})], [product("B508", 1), price("B508")]),
    ])
    add("双实体代词更正与保留约束", [
        turn("分别查B105充电器和B107充电宝的输出功率。",
             [("get_product", {"product_id": p}) for p in ["B105", "B107"]],
             [product("B105"), product("B107", 1)]),
        turn("前一个改成B106，后一个仍是B107。分别说功率，再说充电宝支不支持无线充。",
             [("get_product", {"product_id": p}) for p in ["B106", "B107"]],
             [product("B106"), product("B107", 1, "bank-power"),
              product("B107", 2, "wireless")]),
    ])
    add("部分授权与聊天确认边界", [
        turn("O72001先保存订单到退货草稿，再给我退款；顺便查B105接口。",
             [("update_return_task", {"order_id": "O72001"}),
              ("get_product", {"product_id": "B105"})],
             [draft("O72001"), unchanged, product("B105", 1)],
             dict(draft=dict(order_id="O72001", item_id=None, quantity=None), requests=0)),
        turn("我在聊天里说确认提交，但没有点应用按钮。只读查当前状态，再问B105附线吗。",
             [("get_return_task", {}), ("get_product", {"product_id": "B105"})],
             [unchanged, product("B105", 3)],
             dict(draft=dict(order_id="O72001", item_id=None, quantity=None), requests=0)),
    ])
    return entries, contracts


def build(out):
    environment = load_environment(ENVIRONMENT)
    entries, contracts = authored_cases(environment)
    probes = [
        ("literal", "晴岚 A6", [f"B{g}01" for g in range(1, 6)]),
        ("compact", "晴岚A6", [f"B{g}01" for g in range(1, 6)]),
        ("lowercase", "晴岚 a6", [f"B{g}01" for g in range(1, 6)]),
        ("fullwidth", "晴岚 Ａ６", [f"B{g}01" for g in range(1, 6)]),
        ("series-compact", "第5系列晴岚A6", ["B501"]),
        ("series-fullwidth", "第２系列晴岚ａ６", ["B201"]),
        ("spaces", " 晴岚　A6 ", [f"B{g}01" for g in range(1, 6)]),
        ("other-model", "晴岚 H8", [f"B{g}02" for g in range(1, 6)]),
        ("unknown-model", "晴岚A60", []),
        ("negation-not-a-parser", "不是晴岚A6", []),
        ("partial-syntax", "A", [f"B{g}01" for g in range(1, 6)]),
        ("banks-pages", "充电宝", [f"B{g}0{n}" for g in range(1, 6) for n in [7, 8]]),
    ]
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "cases.json", entries)
    dump(out / "contracts.json", contracts)
    dump(out / "entity_probes.json", [dict(id=n, query=q, expected_ids=ids)
                                      for n, q, ids in probes])
    manifest = dict(version=VERSION, base_sha="7e5b893929004d550bf7e28fef4b6a66c4209182",
                    environment=str(ENVIRONMENT.relative_to(PROJECT_ROOT)),
                    environment_manifest_sha256=file_hash(ENVIRONMENT / "manifest.json"),
                    clock=environment["manifest"]["clock"], cases=len(entries),
                    turns=sum(len(e["task"]["steps"]) for e in entries),
                    provenance="authored development; frozen before baseline; no blind claim",
                    conditions=dict(body_bytes=32768, output_tokens=1024, requests_per_episode=8,
                                    paid_calls_authorized=False, random_seeds=1),
                    files={p.name: file_hash(p) for p in sorted(out.iterdir())})
    dump(out / "manifest.json", manifest)
    return manifest


def load(root=ROOT):
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["version"] != VERSION:
        raise ValueError("Unknown complex-case version")
    if set(p.name for p in root.iterdir()) != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("Complex-case inventory changed")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or file_hash(root / name) != expected:
            raise ValueError("Frozen complex-case fingerprint changed")
    if file_hash(ENVIRONMENT / "manifest.json") != manifest["environment_manifest_sha256"]:
        raise ValueError("Frozen v2.2 environment changed")
    suite = load_environment(ENVIRONMENT)
    suite["manifest"] = manifest
    suite["entries"] = json.loads((root / "cases.json").read_text())
    suite["contracts"] = json.loads((root / "contracts.json").read_text())
    suite["tasks"] = [Task.model_validate(e["task"]) for e in suite["entries"]]
    suite["entity_probes"] = json.loads((root / "entity_probes.json").read_text())
    if (len(suite["tasks"]) != manifest["cases"]
            or {t.task_id for t in suite["tasks"]} != set(suite["contracts"])):
        raise ValueError("Complex-case task alignment changed")
    for entry in suite["entries"]:
        task = entry["task"]
        if not (len(task["steps"]) == len(entry["intent_gold"])
                == len(entry["tool_gold"]) == len(suite["contracts"][task["task_id"]])):
            raise ValueError("Complex-case turn alignment changed")
        for gold in entry["intent_gold"]:
            ids = [g["id"] for g in gold["goals"]]
            if len(ids) < 2 or len(ids) != len(set(ids)):
                raise ValueError("Each turn needs distinct objective IDs")
    return suite
