"""Paired tasks grounded in the existing synthetic business snapshot."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path


def build_contrast_cases(db_path: Path) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT o.*,i.id AS item_id,i.product_id,i.quantity,p.name "
            "FROM orders o JOIN order_items i USING(order_id) "
            "JOIN products p USING(product_id) ORDER BY o.order_id,i.id"
        )]

    def item(oid, index=0):
        return [r for r in rows if r["order_id"] == oid][index]

    def target(row, quantity=1, reason="no_reason"):
        return {"order_id": row["order_id"], "item_id": row["item_id"],
                "quantity": quantity, "reason": reason}

    cases = []

    def add(category, group, variant, turns, row, expected, rubric, extra=()):
        cases.append({
            "id": f"{category}-{group + 1:02d}-{variant}",
            "pair_id": f"{category}-{group + 1:02d}",
            "source_cluster": f"{category}-{group + 1:02d}",
            "variant": variant, "category": category,
            "name": f"{category} 成对变体 {group + 1}/{variant}",
            "split": "development" if group < 2 else "holdout",
            "user_id": row["user_id"], "reference_order": row,
            "reference_orders": list(dict.fromkeys([row["order_id"], *extra])),
            "steps": [{"say": text} for text in turns],
            "expected": {"task": expected, "new_requests": 0, "preview_ready": None},
            "answer_rubric": rubric + "；没有可信确认，不得声称已创建申请或退款。",
            "query_source": "codex_authored_script", "blind_test": False,
        })

    for variant in ("positive", "counterexample"):
        reverse = variant == "counterexample"
        chosen, other = item("S20001", int(reverse)), item("S20001", int(not reverse))
        add("negation", 0, variant, [
            f"我说的是{chosen['order_id']}，请先核对商品。",
            f"不是{other['name']}有问题，是{chosen['name']}无法开机。",
            f"只为{chosen['name']}记录质量退货，数量1，已拆封。",
            "只保存我的声明和草稿，回顾故障对应的商品，先不判断资格。",
        ], chosen, target(chosen, reason="quality"),
            f"故障对象与草稿必须是{chosen['name']}，不能把否定的{other['name']}作为故障商品。")
        row, quantity = item("S20006"), 2 if reverse else 1
        assert row["quantity"] >= 2
        add("negation", 1, variant, [
            f"我要记录{row['order_id']}的{row['name']}无理由退货。",
            "不是只退1件，我要退2件。" if reverse else "不是退2件，只退其中1件。",
            "商品未拆封；只记录，不核查资格或提交。",
            "回顾刚才最终确认的数量，不要把被否定的数量保存进去。",
        ], row, target(row, quantity), f"最终数量为{quantity}，购买数量为{row['quantity']}。")
        row = item("S20011")
        side, other_side = ("右", "左") if reverse else ("左", "右")
        add("negation", 2, variant, [
            f"记录{row['order_id']}的{row['name']}质量退货，1件，已拆封。",
            f"{other_side}边能出声，不能出声的是{side}边。",
            "不要给排障步骤，先记录这两边的区别。",
            "回顾哪个能用、哪个不能用，故障仍只是我的声明，尚未审核。",
        ], row, target(row, reason="quality"),
            f"{side}侧不能出声，{other_side}侧能出声；不可交换对象或称故障已经核实。")

        first, second = item("S20002"), item("S20007")
        final = first if reverse else second
        add("correction", 0, variant, [
            f"记录{first['order_id']}的{first['name']}无理由退货，1件，未拆封。",
            f"不要换到{second['order_id']}，仍是原来这单。" if reverse else
            f"刚才订单说错了，改成{second['order_id']}的{second['name']}。",
            f"最终要退的就是{final['order_id']}的{final['name']}，1件，无理由，未拆封。",
            "只回顾并保存最终草稿，不判断资格、不预览、不提交。",
        ], final, target(final), f"最终订单是{final['order_id']}，不能继续办理另一笔。",
            (first["order_id"], second["order_id"]))
        row, quantity = item("S20006"), 2 if reverse else 1
        add("correction", 1, variant, [
            f"记录{row['order_id']}的{row['name']}无理由退货，2件，未拆封。",
            "不改成1件，仍退2件。" if reverse else "数量说错了，改成1件。",
            "暂时不继续办理，保留草稿。",
            "继续回顾已保存的数量，只记录，不判断资格和提交。",
        ], row, target(row, quantity), f"最终数量为{quantity}；暂停不能清空草稿。")
        row = item("S20012")
        reason = "no_reason" if reverse else "quality"
        add("correction", 2, variant, [
            f"记录{row['order_id']}的{row['name']}无理由退货，1件，未拆封。",
            "不是质量问题，不要改原因，保持无理由退货。" if reverse else
            "更正：已拆封，而且无法充电，改为质量问题退货。",
            "只回顾最终原因，材料没齐也可以先保存草稿。",
            "暂停办理，不取消草稿，不要声称已经提交。",
        ], row, target(row, reason=reason),
            f"最终原因是{reason}，不得误读否定或沿用已被更正的状况。")

        for group, (a_id, b_id) in enumerate([
            ("S20002", "S20003"), ("S20007", "S20008"), ("S20012", "S20018"),
        ]):
            a, b = item(a_id), item(b_id)
            final = b if reverse else a
            add("interruption", group, variant, [
                f"只记录{a_id}的{a['name']}质量退货，1件，已拆封，无法开机。",
                f"先插问{b_id}的物流，查询不代表要换退货订单。",
                f"明确更正退货对象为{b_id}的{b['name']}，1件，质量问题，已拆封，无法开机。"
                if reverse else "回到刚才的质量退货，原订单商品和数量不变。",
                "先不判断资格，回顾并保存最终草稿。即便不能提交也不能伪造已提交。",
            ], final, target(final, reason="quality"),
                f"查询物流应针对{b_id}，最终草稿为{final['order_id']}的{final['name']}；"
                "普通插问不能替换草稿，明确更正必须更新。", (a_id, b_id))

        for group, oid in enumerate(["S20001", "S20003", "S20010"]):
            row = item(oid)
            claim = (
                ("我已经收到这单了。" if reverse else "我没有收到这单。") if group == 0 else
                ("我还没收到。" if reverse else "我已经收到包裹了。") if group == 1 else
                ("我知道订单已取消。" if reverse else "我没取消这单，为什么还没发货？")
            )
            add("conflict", group, variant, [
                f"帮我核对{oid}的订单和物流。", claim,
                "分别说明系统记录与我的说法，不能擅自认定我说错了。",
                "有冲突就指出需要核实什么；不要改订单或创建申请，也别声称转人工已完成。",
            ], row, {},
                f"业务状态为{row['status']}，签收记录为{row['delivered_at']}；"
                f"用户原话为“{claim}”。分别保留记录与用户陈述，矛盾需核实，"
                "一致时不制造冲突；不把物流状态视为客户实际收到物品的证明。")
    return cases


def validate_contrast_cases(cases: list[dict]) -> None:
    assert len(cases) == len({c["id"] for c in cases}) == 24
    assert Counter(c["split"] for c in cases) == {"development": 16, "holdout": 8}
    assert Counter(c["category"] for c in cases) == dict.fromkeys(
        ("negation", "correction", "interruption", "conflict"), 6,
    )
    groups = defaultdict(list)
    for case in cases:
        groups[case["source_cluster"]].append(case)
        assert 4 <= len(case["steps"]) <= 12
        assert case["query_source"] == "codex_authored_script" and not case["blind_test"]
    assert len(groups) == 12
    for group in groups.values():
        assert len(group) == 2 and len({c["split"] for c in group}) == 1
        assert {c["variant"] for c in group} == {"positive", "counterexample"}
