"""Small authored benchmark inputs, generated without a model or a business database."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, timedelta

from .config import PROJECT_ROOT, Settings
from .rag import CHUNKER_VERSION, KnowledgeBase

VERSION = "local-commerce-v1"
LATEST_VERSION = "local-commerce-v1.1"
SEED = 20261026
CLOCK = "2026-11-06T04:00:00+00:00"
DEFAULT_SUITE = PROJECT_ROOT / "data/eval/commerce_benchmark_v1_1"


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def business_data(seed=SEED):
    """Reuse the business schema, not historical customers or seeded order facts."""
    rows = [
        ("B101", "晴岚 A6 入耳耳机", 219, 199, ["入耳式", "蓝牙5.4", "主动降噪", "单次续航8小时"]),
        (
            "B102",
            "晴岚 H8 头戴耳机",
            499,
            459,
            ["头戴式", "蓝牙5.3", "单次续航48小时", "3.5mm有线"],
        ),
        (
            "B103",
            "溪行 O6 开放挂耳耳机",
            279,
            259,
            ["开放式挂耳", "蓝牙5.4", "单次续航11小时", "不支持主动降噪"],
        ),
        ("B104", "溪行 C6 耳夹耳机", 349, 329, ["开放式耳夹", "单次续航9小时", "双设备连接"]),
        ("B105", "栖云 C80 充电器", 159, 139, ["80W", "双USB-C口", "支持PD3.1", "不附赠充电线"]),
        ("B106", "栖云 C35 充电器", 89, 79, ["35W", "单USB-C口", "支持PD3.0", "附赠1米充电线"]),
        ("B107", "岚舟 P12 充电宝", 189, 169, ["12000mAh", "30W有线输出", "不支持无线充电"]),
        ("B108", "岚舟 P24 充电宝", 299, 269, ["24000mAh", "65W有线输出", "不支持无线充电"]),
    ]
    products = [
        dict(
            id=i,
            name="合成 " + n,
            price=p,
            current_price=c,
            attributes=a,
            category="无线耳机" if index < 4 else "充电配件",
        )
        for index, (i, n, p, c, a) in enumerate(rows)
    ]
    rng, today, orders = random.Random(seed), date.fromisoformat(CLOCK[:10]), []
    for n in range(1, 19):
        status = {
            1: "delivered",
            2: "delivered",
            3: "shipped",
            4: "pending",
            5: "delivered",
            6: "delivered",
        }.get(n, ("delivered", "shipped", "cancelled")[n % 3])
        age = {1: 2, 2: 3, 5: 8, 6: 7}.get(n, 2 + n % 5)
        created_age = age + 3 if status == "delivered" else 1 + n % 3
        product = {1: "B101", 2: "B101", 3: "B102", 4: "B105", 5: "B103", 6: "B101"}.get(
            n
        ) or rng.choice(products)["id"]
        ids = [product, "B107"] if n == 1 else [product]
        lines = []
        for offset, pid in enumerate(ids):
            p = next(p for p in products if p["id"] == pid)
            lines.append(
                dict(
                    id=6100 + n * 10 + offset,
                    product_id=pid,
                    quantity=2 if n == 1 and offset == 0 else 1,
                    price=p["current_price"] if created_age <= 1 else p["price"],
                )
            )
        orders.append(
            dict(
                id=f"O610{n:02}",
                user_id=f"USER{(n - 1) // 6 + 1:03}",
                status=status,
                created_at=(today - timedelta(days=created_age)).isoformat(),
                shipped_at=(
                    today - timedelta(days=age + 1 if status == "delivered" else 1)
                ).isoformat()
                if status in {"delivered", "shipped"}
                else None,
                delivered_at=(today - timedelta(days=age)).isoformat()
                if status == "delivered"
                else None,
                items=lines,
            )
        )
    return dict(
        seed=seed,
        reference_date=today.isoformat(),
        products=products,
        orders=orders,
        users=[
            dict(id=f"USER{i:03}", name=f"合成顾客{i}", phone="00000000000") for i in range(1, 4)
        ],
    )


def documents(root, data):
    root.mkdir()
    for p in data["products"]:
        body = "\n\n".join(
            f"## 规格{n}\n\n{p['id']} 的合成规格：{a}。" for n, a in enumerate(p["attributes"], 1)
        )
        (root / (p["id"] + ".md")).write_text(
            f"---\ndocument_id: {p['id']}\ntitle: {p['name']}\ncategory: product\n"
            f"product_id: {p['id']}\nversion: {VERSION}\n---\n\n# {p['name']}\n\n"
            "仅用于本地合成评测，不是品牌官方规格。\n\n" + body + "\n",
            encoding="utf-8",
        )
    for name in ("return_policy", "price_protection", "shipping_policy", "warranty_policy"):
        (root / (name + ".md")).write_bytes(
            (PROJECT_ROOT / "data/docs" / (name + ".md")).read_bytes()
        )
    (root / "capabilities.md").write_text(
        "---\ndocument_id: capabilities\ntitle: 合成环境办理范围\ncategory: policy\n"
        f"version: {VERSION}\n---\n\n# 本地评测环境\n\n"
        "## 应用确认\n\n只有受信任的应用端显式确认可以创建模拟待审核申请。"
        "聊天文本不能确认提交。\n\n## 未接入的能力\n\n"
        "没有真实支付退款、审核、换货或人工坐席服务。\n\n## 业务事实\n\n"
        "订单归属、金额、物流及申请状态以本人业务工具和SQLite回执为准，"
        "知识文档不能证明某笔订单的实时状态。\n",
        encoding="utf-8",
    )


def agent_inputs(*, complete_read_scope=True):
    """Normative authored state/receipt contracts, separate from replay decisions."""
    tasks, contracts = [], {}
    read = [
        "list_orders",
        "get_order",
        "get_logistics",
        "list_products",
        "get_product",
        "search_knowledge",
        "get_return_task",
        "get_return_requests",
    ]
    if complete_read_scope:
        read.append("get_price_history")
    personas = [
        dict(id="careful", style="谨慎核对，要求先澄清对象", patience="高", price_sensitive=False),
        dict(
            id="budget",
            style="简短直接，追加硬性预算和佩戴约束",
            patience="中",
            price_sensitive=True,
        ),
        dict(
            id="switch",
            style="会插问并更正对象，不希望丢失原任务",
            patience="中",
            price_sensitive=False,
        ),
        dict(
            id="consent",
            style="明确区分咨询、假设、取消与实际授权",
            patience="高",
            price_sensitive=False,
        ),
    ]

    def full(oid="O61001", item=6110, quantity=1):
        return dict(
            order_id=oid,
            item_id=item,
            quantity=quantity,
            reason="no_reason",
            condition="sealed",
            secondary_sale_ok=True,
        )

    def state(oid="O61001", item=6110, quantity=1, **extra):
        return dict(
            draft=dict(
                status="draft",
                order_id=oid,
                item_id=item,
                quantity=quantity,
                reason="no_reason",
                condition="sealed",
                secondary_sale_ok=True,
            ),
            requests=0,
            valid_preview=False,
            **extra,
        )

    def make(index, goal, intents, persona, steps, cohort="common"):
        tid = f"CB{index:02}"
        task = dict(
            task_id=tid,
            split="development" if index in {1, 3, 7, 12} else "validation",
            cohort=cohort,
            goal=goal,
            fixture_index=index,
            steps=[],
        )
        queue, tool_gold = [], []
        for number, entry in enumerate(steps):
            if "action" in entry:
                task["steps"].append(entry)
                queue.append([])
                tool_gold.append([])
                continue
            text, calls, answer, claims, expected = (
                entry["text"],
                entry["calls"],
                entry["answer"],
                entry["claims"],
                entry["expect"],
            )
            names = [c[0] for c in calls]
            allowed = sorted(set(read + names))
            # A wrong cancel is NOT a valid execution merely because the business guard rejects it.
            # The reference trajectory is not the only correct tool path. SQL/evidence
            # and allowed effects decide success; don't repeat M8's path-sensitive reward.
            required = []
            task["steps"].append(
                dict(
                    text=text,
                    expect=expected,
                    allowed_tools=allowed,
                    required_tools=required,
                    answers=[
                        dict(
                            text=answer,
                            claims=[dict(span=answer, fact=f, value=v) for f, v in claims],
                        )
                    ],
                )
            )
            queue.append(
                [
                    {"calls": [dict(id=f"{tid}-{number}-{j}", name=n, args=a)]}
                    for j, (n, a) in enumerate(calls)
                ]
                + [{"text": answer}]
            )
            tool_gold.append(
                [
                    dict(name=n, arguments=a, result=entry.get("result", {}), error=None)
                    for n, a in calls
                ]
            )
        tasks.append(
            dict(
                task=task,
                persona=personas[persona],
                intents=intents,
                tool_gold=tool_gold,
                source="new_authored_synthetic",
                user_driver="bounded_authored_multiturn_script",
            )
        )
        contracts[tid] = queue

    def chat(text, calls, answer, claims, expect=None, **extra):
        return dict(
            text=text,
            calls=calls,
            answer=answer,
            claims=claims,
            expect=expect or dict(draft=None, requests=0),
            **extra,
        )

    save = "我要退订单O61001的明细6110，数量1，无理由，未拆封且满足全部二次销售条件，先存草稿。"
    saved = chat(
        save,
        [("update_return_task", full())],
        "已保存1件商品的草稿，尚未申请。",
        [("draft.quantity", 1), ("submission", False)],
        state(),
    )
    preview = chat(
        save + "请生成预览。",
        [("update_return_task", full()), ("preview_return", {})],
        "已生成有效预览，尚未申请。",
        [("valid_preview", True), ("submission", False)],
        dict(state(), preview_count=1, valid_preview=True),
    )
    make(
        1,
        "预算与佩戴约束、代词追问规格",
        ["商品候选", "规格追加"],
        1,
        [
            chat(
                "通勤不想入耳，预算300元以内，请给开放挂耳候选，别改成头戴。",
                [("list_products", {})],
                "B103是开放式挂耳，当前259元。",
                [("product.B103", "开放式挂耳"), ("price.B103", 259)],
            ),
            chat(
                "它支持主动降噪吗？单次能听多久？",
                [("get_product", {"product_id": "B103"})],
                "B103不支持主动降噪，单次续航11小时。",
                [("product.B103", "不支持主动降噪"), ("product.B103", "单次续航11小时")],
            ),
        ],
    )
    make(
        2,
        "多候选本人订单澄清后查询物流",
        ["候选澄清", "物流"],
        0,
        [
            chat(
                "两笔A6耳机订单我记混了，先列候选，不要自动选退货对象。",
                [("list_orders", {})],
                "O61001和O61002均已签收，请先选定一笔。",
                [("order.O61001.status", "delivered"), ("order.O61002.status", "delivered")],
            ),
            chat(
                "选O61001，看看它的物流。",
                [("get_logistics", {"order_id": "O61001"})],
                "O61001物流已签收。",
                [("order.O61001.status", "delivered")],
            ),
        ],
    )
    make(
        3,
        "混合商品咨询与部分草稿写入",
        ["订单草稿", "充电接口", "补齐"],
        2,
        [
            chat(
                "我要退O61001，先只保存订单；顺便问B105有几个接口？",
                [
                    ("update_return_task", {"order_id": "O61001"}),
                    ("get_product", {"product_id": "B105"}),
                ],
                "已保存O61001草稿；B105为双USB-C口。",
                [("draft.order_id", "O61001"), ("product.B105", "双USB-C口")],
                dict(draft=dict(order_id="O61001", item_id=None, quantity=None), requests=0),
            ),
            chat(
                "选明细6110退1件，无理由，未拆封，满足全部二次销售条件。",
                [("update_return_task", full())],
                "草稿已补齐为1件，尚未申请。",
                [("draft.quantity", 1), ("submission", False)],
                state(),
            ),
        ],
    )
    make(
        4,
        "插问政策后更正商品并预览",
        ["草稿", "政策", "商品更正"],
        2,
        [
            saved,
            chat(
                "先插问：无理由退货运费由谁承担？保留草稿。",
                [("search_knowledge", {"query": "无理由退货运费承担"})],
                "无理由退货运费由买家承担。",
                [("shipping", "买家")],
                state(),
            ),
            chat(
                "改成同单明细6111的充电宝，退1件，无理由未拆封，满足二次销售条件，生成预览。",
                [("update_return_task", full(item=6111)), ("preview_return", {})],
                "已改为明细6111并生成有效预览，数量1。",
                [("draft.item_id", 6111), ("draft.quantity", 1), ("valid_preview", True)],
                dict(state(item=6111), preview_count=1, valid_preview=True),
            ),
        ],
    )
    make(
        5,
        "更正订单时清除旧商品与数量",
        ["草稿", "订单更正", "重新补齐"],
        2,
        [
            saved,
            chat(
                "不是这单，改成O61002，只确定订单，商品数量重新选择。",
                [("update_return_task", {"order_id": "O61002"})],
                "订单已改为O61002，商品和数量等待重新补充。",
                [("draft.order_id", "O61002"), ("draft.item_id", None), ("draft.quantity", None)],
                dict(
                    draft=dict(
                        order_id="O61002",
                        item_id=None,
                        quantity=None,
                        reason=None,
                        condition=None,
                        secondary_sale_ok=None,
                    ),
                    requests=0,
                ),
            ),
            chat(
                "选明细6120退1件，无理由未拆封，满足全部二次销售条件。",
                [("update_return_task", full("O61002", 6120))],
                "已保存O61002的1件草稿，尚未申请。",
                [("draft.order_id", "O61002"), ("draft.quantity", 1), ("submission", False)],
                state("O61002", 6120),
            ),
        ],
    )
    make(
        6,
        "否定、引用和假设取消同时咨询商品",
        ["保留草稿", "取消反例", "规格"],
        3,
        [
            saved,
            chat(
                "我不是要取消，‘取消退货草稿’只是引用；如果以后取消会怎样？现在保留，再查B106功率。",
                [("get_product", {"product_id": "B106"})],
                "B106为35W，现有草稿保留。",
                [("product.B106", "35W"), ("draft.status", "draft")],
                state(),
            ),
        ],
    )
    make(
        7,
        "明确取消草稿不产生申请",
        ["草稿", "明确取消"],
        3,
        [
            saved,
            chat(
                "取消退货草稿",
                [("cancel_return_task", {})],
                "已取消草稿，没有创建申请。",
                [("draft.status", "cancelled"), ("submission", False)],
                dict(draft=dict(status="cancelled"), requests=0, valid_preview=False),
            ),
        ],
    )
    make(
        8,
        "第7天边界及第8天只读咨询",
        ["期限边界", "预览", "保留当前对象"],
        0,
        [
            chat(
                "O61006明细6160退1件，无理由未拆封，满足二次销售条件，第7天请生成预览。",
                [("update_return_task", full("O61006", 6160)), ("preview_return", {})],
                "O61006在第7天，已生成有效预览，尚未申请。",
                [("order.O61006.age", 7), ("valid_preview", True), ("submission", False)],
                dict(state("O61006", 6160), preview_count=1, valid_preview=True),
            ),
            chat(
                "先不确认。只看O61005签收第8天还能无理由退吗，不要改原草稿。",
                [("get_order", {"order_id": "O61005"})],
                "O61005签收8天，已超无理由期限；原草稿仍是O61006。",
                [("order.O61005.age", 8), ("draft.order_id", "O61006")],
                dict(state("O61006", 6160), preview_count=1, valid_preview=True),
            ),
        ],
    )
    make(
        9,
        "跨用户拒绝与本人未发货物流",
        ["权限", "无物流"],
        0,
        [
            chat(
                "朋友订单O61007我想看一下，能读取吗？",
                [("get_order", {"order_id": "O61007"})],
                "该订单不属于当前用户，无法读取。",
                [("access.O61007", False)],
                refusal=True,
                result=dict(found=False),
            ),
            chat(
                "那只查我自己的O61004物流。",
                [("get_logistics", {"order_id": "O61004"})],
                "O61004未发货，暂无物流。",
                [("order.O61004.status", "pending")],
                refusal=True,
                result=dict(found=False),
            ),
        ],
    )
    for number, action, question in [
        (10, "expire", "预览过期了，只核对现状，不要重做。"),
        (11, "policy_change", "政策修订后旧预览还有效吗？只读核对。"),
    ]:
        invalid = dict(state(), preview_count=1, valid_preview=False)
        make(
            number,
            "预览过期核验" if action == "expire" else "政策hash变更使预览失效",
            ["有效预览", action, "只读核对"],
            0,
            [
                preview,
                dict(action=action, expect=invalid),
                chat(
                    question,
                    [("get_return_task", {})],
                    "旧预览已失效，没有创建申请。",
                    [("valid_preview", False), ("history_exists", True), ("submission", False)],
                    invalid,
                ),
            ],
        )
    for number, actions, goal in [
        (12, ["accept", "accept"], "应用确认及重复点击只创建一份模拟申请"),
        (13, ["reject"], "应用拒绝保留草稿且不提交"),
        (14, ["lost_commit_receipt", "restart_lookup"], "提交回执丢失后重启只读核验"),
    ]:
        submitted = number != 13
        final = (
            dict(
                draft=dict(status="submitted"),
                requests=1,
                valid_preview=False,
                confirmation=dict(status="accepted", decision="accept", consumptions=1),
            )
            if submitted
            else dict(
                state(),
                preview_count=1,
                valid_preview=True,
                confirmation=dict(status="rejected", decision="reject", consumptions=1),
            )
        )
        message = (
            "已有1份模拟待审核申请，尚未退款。"
            if submitted
            else "本次确认被拒绝，草稿与有效预览保留，没有申请。"
        )
        claims = [("submission", submitted), ("unsupported_operation", False)]
        if submitted:
            claims.append(("request_status", "pending_review"))
        else:
            claims.extend([("draft.status", "draft"), ("valid_preview", True)])
        make(
            number,
            goal,
            ["预览", "可信应用事件", "结果核验"],
            3,
            [
                preview,
                dict(action="open", expect=dict(state(), preview_count=1, valid_preview=True)),
                *[dict(action=a, expect=final) for a in actions],
                chat(
                    "请只读核对结果，不要重复创建。",
                    [("get_return_requests", {})],
                    message,
                    claims,
                    final,
                ),
            ],
            cohort="v3_application",
        )
    return tasks, contracts


def rag_inputs(root, data, *, table_alternatives=True):
    kb = KnowledgeBase(
        Settings(_env_file=None, docs_dir=root / "docs", chunk_size=700, chunk_overlap=80)
    )
    chunks, cases = kb.load_documents(), []
    questions = [
        ["晴岚A6(B101)是什么佩戴方式？", "B101采用哪个蓝牙版本？", "A6具备主动降噪吗？"],
        ["晴岚H8(B102)是入耳还是头戴？", "B102的蓝牙版本是什么？", "H8单次续航多久？"],
        ["溪行O6(B103)怎样佩戴？", "B103使用哪个蓝牙版本？", "O6单次能用多少小时？"],
        ["溪行C6(B104)是什么佩戴形式？", "B104的单次续航？", "C6支持同时连接两台设备吗？"],
        ["栖云C80(B105)最高功率？", "B105有几个USB-C接口？", "C80支持哪个PD版本？"],
        ["栖云C35(B106)最高功率多少？", "B106有几个USB-C口？", "C35使用哪一版PD协议？"],
        ["岚舟P12(B107)容量多大？", "B107有线输出多少W？", "P12能无线充电吗？"],
        ["岚舟P24(B108)电池容量？", "B108有线输出最高功率？", "P24支持无线充电吗？"],
    ]

    def add(query, spans, split, category):
        groups = []
        for doc, span in spans:
            alternatives = span if isinstance(span, list) else [span]
            ids = [
                c.chunk_id
                for c in chunks
                if c.document_id == doc and any(s in c.content for s in alternatives)
            ]
            if not ids:
                raise ValueError(f"Gold span missing from source: {doc}: {span}")
            groups.append(ids)
        cases.append(
            dict(
                id=f"RQ{len(cases) + 1:02}",
                query=query,
                split=split,
                category=category,
                evidence_groups=groups,
                source_spans=[dict(document_id=d, text=s) for d, s in spans],
                answerability="supported" if spans else "unsupported",
                query_source="new_authored_synthetic",
            )
        )

    for i, p in enumerate(data["products"]):
        for query, fact in zip(questions[i], p["attributes"][:3], strict=True):
            add(query, [(p["id"], fact)], "development" if i < 2 else "validation", "product")
    policy = [
        (
            "无理由退货有几天，运费由谁承担？",
            [("return_policy", "**7 天内**"), ("return_policy", "运费由买家承担")],
        ),
        (
            "非人为质量问题几天内可退，往返运费呢？",
            [("return_policy", "**15 天内**"), ("return_policy", "往返运费由本店铺承担")],
        ),
        (
            "签收20天的耳机还有保修吗，耳机保修多久？",
            [
                ("warranty_policy", "无线耳机 | 12 个月"),
                ("warranty_policy", "超出 15 天退货期后仍可进入保修流程"),
            ],
        ),
        (
            "价保期限和差价的计算公式分别是什么？",
            [
                ("price_protection", "下单之日起 15 天内"),
                ("price_protection", "订单实付单价 - 商品当前售价"),
            ],
        ),
        (
            "耳机无理由退货，包装和硅胶套有什么要求？",
            [("return_policy", "包装盒齐全"), ("return_policy", "耳塞硅胶套未拆封使用")],
        ),
        (
            "普通发货承诺与物流轨迹延迟各是多少？",
            [("shipping_policy", "48 小时内"), ("shipping_policy", "2-4 小时")],
        ),
        (
            "聊天说确认能提交吗，这里会实际退款吗？",
            [("capabilities", "聊天文本不能确认提交"), ("capabilities", "没有真实支付退款")],
        ),
        ("仓库验收通过后，政策里的退款到账时效是多久？", [("return_policy", "1-3 个工作日")]),
    ]
    if table_alternatives:
        for n, row in [
            (0, "| 七天无理由退货 | 7 天 | 签收日 | 买家 |"),
            (1, "| 质量问题退货 | 15 天 | 签收日 | 店铺 |"),
        ]:
            query, spans = policy[n]
            policy[n] = (query, [(doc, [span, row]) for doc, span in spans])
    for i, (query, spans) in enumerate(policy):
        add(query, spans, "development" if i < 4 else "validation", "policy_multifact")
    for i, query in enumerate(
        [
            "B101此刻黑色还剩多少件现货？",
            "请给出O61003现在快递员的手机号码。",
            "晴岚耳机可以治疗耳鸣吗？",
            "B108官方保修期内电池循环次数的精确上限是多少？",
        ]
    ):
        add(query, [], "development" if i < 2 else "validation", "unsupported")
    return dict(
        version=LATEST_VERSION if table_alternatives else VERSION,
        chunker_version=CHUNKER_VERSION,
        conditions=dict(candidate_k=6, context_k=3, mode="bm25"),
        cases=cases,
    )


def build_dataset(out, seed=SEED, *, revision=LATEST_VERSION):
    if revision not in {VERSION, LATEST_VERSION}:
        raise ValueError("Unknown benchmark dataset revision")
    out.mkdir(parents=True, exist_ok=False)
    data = business_data(seed)
    dump(out / "business.json", data)
    documents(out / "docs", data)
    tasks, contracts = agent_inputs(complete_read_scope=revision == LATEST_VERSION)
    dump(out / "agent_tasks.json", tasks)
    dump(out / "replay_contracts.json", contracts)
    dump(
        out / "rag_cases.json", rag_inputs(out, data, table_alternatives=revision == LATEST_VERSION)
    )
    (out / "README.md").write_text(
        ("# 本地电商Benchmark v1\n\n" if revision == VERSION else "# 本地电商Benchmark v1.1\n\n")
        + "14条新合成多轮任务、36条RAG问题；3用户、8商品、18订单。\n"
        f"合成时钟2026-11-06 04:00 UTC，seed为{seed}。不是官方τ²/ECom-Bench任务。\n"
        "任务与Gold为同一工程作者编写，不是独立盲测；运行后仅作已见开发证据。\n"
        "replay_contracts仅为离线执行合同，不代表真实模型效果。\n",
        encoding="utf-8",
    )
    manifest = dict(
        version=revision,
        seed=seed,
        clock=CLOCK,
        agent_tasks=len(tasks),
        rag_cases=36,
        files={
            str(p.relative_to(out)): file_hash(p) for p in sorted(out.rglob("*")) if p.is_file()
        },
    )
    dump(out / "manifest.json", manifest)
    return manifest
