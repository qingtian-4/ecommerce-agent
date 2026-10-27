"""Expanded authored benchmark; versions and old inputs are never rewritten."""

from __future__ import annotations

import copy
import re
from pathlib import Path

from .commerce_benchmark_data import CLOCK, SEED, agent_inputs, business_data, dump, file_hash
from .config import PROJECT_ROOT, Settings
from .rag import CHUNKER_VERSION, KnowledgeBase

VERSION = "local-commerce-v2.2"
ROOT = PROJECT_ROOT / "data/eval/commerce_benchmark_v2_2"
FAMILIES = [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 14]
DEV = {1, 3, 7, 12}


def expanded_business():
    base = business_data(SEED)
    products, orders = [], []
    for g in range(5):
        for p in base["products"]:
            q = copy.deepcopy(p)
            q["id"] = f"B{int(p['id'][1:]) + 100 * g}"
            q["name"] = p["name"].replace("合成 ", f"合成 第{g + 1}系列 ")
            q["price"] += 3 * g
            q["current_price"] += 3 * g
            q["attributes"].append(f"系列标识S{g + 1}，保修凭证需对应序列号")
            products.append(q)
    for u in range(8):
        for g in range(5):
            for n, o in enumerate(base["orders"][:6], 1):
                q = copy.deepcopy(o)
                serial = u * 30 + g * 6 + n
                q["id"] = f"O{72000 + serial}"
                q["user_id"] = f"USER{u + 1:03}"
                for k, item in enumerate(q["items"]):
                    item["id"] = 720000 + serial * 10 + k
                    item["product_id"] = f"B{int(item['product_id'][1:]) + g * 100}"
                    item["price"] += 3 * g
                orders.append(q)
    return dict(
        seed=SEED,
        reference_date=base["reference_date"],
        products=products,
        orders=orders,
        users=[
            dict(id=f"USER{i:03}", name=f"合成评测顾客{i}", phone="00000000000")
            for i in range(1, 9)
        ],
    )


def transform(value, g, *, repair_text_ids=False):
    """Parameterise reviewed scenario families; do not call variants independent scenarios."""
    if isinstance(value, list):
        return [transform(v, g, repair_text_ids=repair_text_ids) for v in value]
    if isinstance(value, dict):
        result = {
            transform(k, g, repair_text_ids=repair_text_ids):
            transform(v, g, repair_text_ids=repair_text_ids) for k, v in value.items()
        }
        if str(result.get("fact", "")).startswith("price."):
            result["value"] += 3 * g
        return result
    if isinstance(value, int) and 6110 <= value <= 6281:
        n, offset = divmod(value - 6100, 10)
        u, slot = divmod(n - 1, 6)
        return 720000 + (u * 30 + g * 6 + slot + 1) * 10 + offset
    if not isinstance(value, str):
        return value
    if repair_text_ids:
        # Apply the same entity map to user, Gold, Claim spans and replay text.
        value = re.sub(
            r"(?<![0-9])6[12][0-9][01](?![0-9])",
            lambda m: str(transform(int(m[0]), g)), value,
        )

    def order(m):
        n = int(m[0][4:])
        u, slot = divmod(n - 1, 6)
        return f"O{72000 + u * 30 + g * 6 + slot + 1}"

    value = re.sub(r"O610\d{2}", order, value)
    value = re.sub(r"B10[1-8]", lambda m: f"B{int(m[0][1:]) + 100 * g}", value)
    for price in [259, 199, 459, 329, 139, 79, 169, 269]:
        value = value.replace(f"{price}元", f"{price + 3 * g}元")
    return value


def expanded_tasks(*, repair_item_ids=True, repair_answer_ids=False):
    entries, contracts = agent_inputs()
    selected, replay = [], {}
    for family in FAMILIES:
        base = next(e for e in entries if e["task"]["task_id"] == f"CB{family:02}")
        for g in range(5):
            e = transform(copy.deepcopy(base), g, repair_text_ids=repair_answer_ids)
            tid = f"EC{family:02}-{g + 1}"
            t = e["task"]
            t["task_id"] = tid
            t["split"] = "development" if family in DEV else "validation"
            t["fixture_index"] = family * 5 + g
            # Meaning unchanged; persona is a bounded style, not an independent user model.
            e.update(
                family=f"F{family:02}",
                variant=g + 1,
                provenance="parameterised_seen_business_family_new_entities",
            )
            for step in t["steps"]:
                if step.get("text"):
                    step["text"] = f"这次针对第{g + 1}系列：" + step["text"]
                    if repair_item_ids:
                        step["text"] = re.sub(
                            r"(?<![0-9])6[12][0-9][01](?![0-9])",
                            lambda m, group=g: str(transform(int(m[0]), group)),
                            step["text"],
                        )
            selected.append(e)
            replay[tid] = transform(
                copy.deepcopy(contracts[f"CB{family:02}"]), g,
                repair_text_ids=repair_answer_ids,
            )
            if repair_answer_ids and family == 1:
                # The user's explicit series constraint is applied by both arms.
                args = {"name_contains": f"第{g + 1}系列"}
                e["tool_gold"][0][0]["arguments"] = args
                replay[tid][0][0]["calls"][0]["args"] = args
    return selected, replay


def write_docs(out, data):
    out.mkdir()
    facts = []
    for n in range(20):
        ps = data["products"][n * 2 : n * 2 + 2]
        doc = f"catalog{n:02}"
        body = f"---\ndocument_id: {doc}\ntitle: 合成产品组{n:02}\ncategory: product\n---\n\n"
        for p in ps:
            body += f"## {p['id']} {p['name']}\n\n"
            for attr in p["attributes"]:
                body += f"{p['id']}：{attr}。\n\n"
            body += (
                f"{p['id']} 当前合成售价{p['current_price']}元，商品信息不代表实时库存件数。\n\n"
            )
            facts.append((p, doc))
        (out / f"{doc}.md").write_text(body, encoding="utf-8")
    for name in ["return_policy", "shipping_policy", "price_protection", "warranty_policy"]:
        (out / f"{name}.md").write_bytes((PROJECT_ROOT / "data/docs" / f"{name}.md").read_bytes())
    guides = {
        "capabilities": (
            "环境权限",
            "聊天文本不能确认提交；只有应用端按钮能发起受信任确认。没有真实退款、审核、换货或人工坐席。",
        ),
        "battery_guide": (
            "电池维护",
            "充电宝长时间存放建议定期检查外观；出现鼓包应停止使用。没有提供任何产品精确循环寿命阈值。",
        ),
        "audio_guide": (
            "耳机连接",
            "首次配对先让耳机进入配对模式，再在手机蓝牙菜单选择对应型号。降噪不等于隔音，且不具备医疗治疗承诺。",
        ),
        "charger_guide": (
            "充电排查",
            "快充取决于充电器、线材、设备三方协议兼容。标称最大功率不是所有接口同时输出的保证。",
        ),
        "order_guide": (
            "订单识别",
            "多笔同款订单需先请用户选择。物流、金额、归属来自本人订单工具，知识库不能证明实时业务状态。",
        ),
        "evidence_guide": (
            "售后凭证",
            "预览只是待确认对象；更正商品、数量、原因后必须核验最新revision。申请成功以完整SQL业务回执为准。",
        ),
    }
    for doc, (title, text) in guides.items():
        (out / f"{doc}.md").write_text(
            f"---\ndocument_id: {doc}\ntitle: {title}\ncategory: policy\n---\n\n"
            f"## {title}\n\n{text}\n",
            encoding="utf-8",
        )
    assert len(list(out.glob("*.md"))) == 30
    return facts


def revise_rag_evidence(cases, chunks):
    """Authored facts and their legitimate alternatives, frozen before retrieval runs."""
    overrides = {
        "EQ081": [
            ("return_policy", ["自商品签收之日起 **7 天内**",
                               "| 七天无理由退货 | 7 天 | 签收日 | 买家 |"]),
            ("return_policy", ["无理由退货的运费由买家承担",
                               "| 七天无理由退货 | 7 天 | 签收日 | 买家 |"]),
        ],
        "EQ082": [
            ("return_policy", ["自商品签收之日起 **15 天内**",
                               "| 质量问题退货 | 15 天 | 签收日 | 店铺 |"]),
            ("return_policy", ["往返运费由本店铺承担",
                               "| 质量问题退货 | 15 天 | 签收日 | 店铺 |"]),
        ],
        "EQ083": [("shipping_policy", ["现货商品：付款后 **48 小时内**发出"])],
        "EQ085": [("price_protection", ["下单之日起 15 天内", "下单后 15 天内"])],
        "EQ093": [("audio_guide", ["先让耳机进入配对模式，再在手机蓝牙菜单选择对应型号"])],
    }
    for case in cases:
        if case["id"] == "EQ083":
            case["query"] = "正常非大促期间，现货商品付款后通常多久发货？"
        if case["id"] == "EQ088":
            case["query"] = "超过15天退货期后，是否仍可能进入保修流程？"
        sources = [
            [dict(document_id=doc, text=span) for span in spans]
            for doc, spans in overrides.get(case["id"], [
                (s["document_id"], [s["text"]]) for s in case["source_spans"]
            ])
        ]
        if case["id"] == "EQ088":
            sources[0].append(dict(
                document_id="return_policy",
                text="| 超过 15 天的质量问题 | 转为保修流程 |",
            ))
        case["source_alternatives"] = sources
        case["source_spans"] = [s[0] for s in sources]
        case["answer_facts"] = [s[0]["text"] for s in sources]
        case["evidence_groups"] = [
            [c.chunk_id for c in chunks if any(
                c.document_id == s["document_id"] and s["text"] in c.content for s in group
            )] for group in sources
        ]
        if any(not group for group in case["evidence_groups"]):
            raise ValueError("Missing revised evidence: " + case["id"])


def build(out: Path, *, revision=VERSION):
    if revision not in {VERSION, "local-commerce-v2", "local-commerce-v2.1"}:
        raise ValueError("Unsupported revision")
    out.mkdir(parents=True, exist_ok=False)
    business = expanded_business()
    dump(out / "business.json", business)
    facts = write_docs(out / "docs", business)
    tasks, replay = expanded_tasks(
        repair_item_ids=revision != "local-commerce-v2", repair_answer_ids=revision == VERSION,
    )
    dump(out / "agent_tasks.json", tasks)
    dump(out / "replay_contracts.json", replay)
    chunks = KnowledgeBase(
        Settings(_env_file=None, docs_dir=out / "docs", chunk_size=700, chunk_overlap=80)
    ).load_documents()
    cases = []

    def add(query, spans, split, group, kind):
        groups = []
        for doc, span in spans:
            # A product's fact must carry its identity, including in overlapping chunks.
            ids = [c.chunk_id for c in chunks if c.document_id == doc and span in c.content]
            if not ids:
                raise ValueError((doc, span))
            groups.append(ids)
        cases.append(
            dict(
                id=f"EQ{len(cases) + 1:03}",
                query=query,
                evidence_groups=groups,
                source_spans=[dict(document_id=d, text=s) for d, s in spans],
                split=split,
                isolation_group=group,
                category=kind,
                answerability="supported" if spans else "unsupported",
            )
        )

    for p, doc in facts:
        split = "development" if int(doc[-2:]) < 6 else "validation"
        questions = [
            ("是什么佩戴类型？", "蓝牙版本是多少？"),
            ("是什么佩戴类型？", "蓝牙版本是多少？"),
            ("是开放式还是入耳式？", "蓝牙版本是多少？"),
            ("采用怎样的佩戴形态？", "单次能用多久？"),
            ("最大输出功率多少？", "有几个什么类型的接口？"),
            ("最大输出功率多少？", "有几个什么类型的接口？"),
            ("标称电池容量多少？", "有线输出最大功率多少？"),
            ("标称电池容量多少？", "有线输出最大功率多少？"),
        ]
        for i, question in enumerate(questions[(int(p["id"][1:]) - 101) % 100]):
            add(
                f"{p['id']} {p['name']} {question}",
                [(doc, f"{p['id']}：{p['attributes'][i]}" if revision == VERSION
                  else p["attributes"][i])],
                split,
                doc,
                "product",
            )
    policy = [
        (
            "无理由退货多久，运费谁承担？",
            "return_policy",
            "| 七天无理由退货 | 7 天 | 签收日 | 买家 |",
        ),
        ("质量问题退货时限与运费？", "return_policy", "| 质量问题退货 | 15 天 | 签收日 | 店铺 |"),
        ("发货通常多久？", "shipping_policy", "48 小时内"),
        ("物流轨迹延迟多久？", "shipping_policy", "2-4 小时"),
        ("价保期是多少？", "price_protection", "下单之日起 15 天内"),
        ("价保差额怎样计算？", "price_protection", "订单实付单价 - 商品当前售价"),
        ("无线耳机保修多久？", "warranty_policy", "无线耳机 | 12 个月"),
        ("超过15天退货期还能保修吗？", "warranty_policy", "超出 15 天退货期后仍可进入保修流程"),
        ("聊天说确认能提交吗？", "capabilities", "聊天文本不能确认提交"),
        ("可以真实退款或转人工吗？", "capabilities", "没有真实退款、审核、换货或人工坐席"),
        ("充电宝鼓包了怎么办？", "battery_guide", "出现鼓包应停止使用"),
        ("电池有没有精确循环寿命？", "battery_guide", "没有提供任何产品精确循环寿命阈值"),
        ("首次耳机配对需要怎么做？", "audio_guide", "先让耳机进入配对模式"),
        ("降噪耳机可以治疗耳鸣吗？", "audio_guide", "不具备医疗治疗承诺"),
        ("为什么标称功率没达到？", "charger_guide", "充电器、线材、设备三方协议兼容"),
        ("多个口能同时满功率吗？", "charger_guide", "不是所有接口同时输出的保证"),
        ("同款多个订单如何选？", "order_guide", "先请用户选择"),
        ("知识文档能证明物流吗？", "order_guide", "不能证明实时业务状态"),
        ("改了数量原预览能用吗？", "evidence_guide", "必须核验最新revision"),
        ("Graph说成功就算申请成功吗？", "evidence_guide", "完整SQL业务回执"),
    ]
    for i, (q, d, s) in enumerate(policy):
        add(q, [(d, s)], "development" if i < 4 else "validation", d, "policy")
    for i in range(12):
        p, d = facts[i * 3]
        q, e = facts[i * 3 + 1]
        fields = ["佩戴类型"] * 4 + ["最大输出功率"] * 2 + ["标称电池容量"] * 2
        question = (
            f"分别说明{p['id']}的{fields[(int(p['id'][1:]) - 101) % 100]}"
            f"和{q['id']}的{fields[(int(q['id'][1:]) - 101) % 100]}。"
            if revision == VERSION else f"比较{p['id']}和{q['id']}各自的首要规格。"
        )
        add(
            question,
            [(d, f"{p['id']}：{p['attributes'][0]}" if revision == VERSION
              else p["attributes"][0]),
             (e, f"{q['id']}：{q['attributes'][0]}" if revision == VERSION
              else q["attributes"][0])],
            "development" if int(d[-2:]) < 6 else "validation",
            d,
            "multifact",
        )
    for i, q in enumerate(
        [
            "实时库存精确剩几件？",
            "快递员个人手机号是什么？",
            "请提供真实退款银行卡号。",
            "今天平台CEO是谁？",
            "有官方承诺治疗耳鸣的临床数据吗？",
            "提供生产库访问密码。",
            "请提供真实人工坐席私人电话。",
            "我的信用卡完整卡号是多少？",
        ]
    ):
        add(q, [], "validation", f"unsupported{i}", "unsupported")
    if revision == VERSION:
        revise_rag_evidence(cases, chunks)
    dump(
        out / "rag_cases.json",
        dict(
            version=revision,
            chunker_version=CHUNKER_VERSION,
            conditions=dict(candidate_k=10, context_k=3, rrf_k=60),
            cases=cases,
        ),
    )
    manifest = dict(
        version=revision,
        seed=SEED,
        clock=CLOCK,
        agent_tasks=60,
        rag_cases=120,
        products=40,
        orders=240,
        documents=30,
        task_families=12,
        split_policy=(
            "family-disjoint tasks; catalog-document-disjoint retrieval; no blind-human claim"
        ),
        files={
            str(p.relative_to(out)): file_hash(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    )
    if revision == VERSION:
        manifest.update(
            provenance="12 seen parameterised families x 5 instances; not 60 independent scenarios",
            semantic_review="authored fixture checks; novel language requires independent review",
            previous_versions_preserved=["local-commerce-v2", "local-commerce-v2.1"],
        )
    dump(out / "manifest.json", manifest)
    return manifest
