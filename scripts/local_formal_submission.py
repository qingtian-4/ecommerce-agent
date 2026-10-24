"""Offline synthetic M6 application CLI. No paid/model/network client is constructed."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from acs.local_return_tools import fixture_settings  # noqa: E402
from acs.local_submission import (  # noqa: E402
    KIND,
    FormalReturnPoc,
    init_formal_fixture,
    read_submission_receipt,
)
from acs.sessions import SessionStore  # noqa: E402

# Exact M4/M5 inputs; fixed model is protocol evidence, never model-quality evidence.
STAGES = [
    "我要退O10024，先保存这个订单的退货草稿；顺便说明七天无理由退货的运费规则。",
    (
        "退这单的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件。"
        "请保存草稿，先不要预览。"
    ),
    "先插问P003充电器有几个接口？之前退货草稿保留，不用修改。",
    "回到退货，把同订单商品更正为磁吸充电宝；数量1件，无理由，未拆封且满足全部二次销售条件。请生成预览，不能正式申请。",
    "刚才无理由退货的期限和运费是什么？不要取消或修改草稿。",
    None,
    "取消退货草稿",
    (
        "重新退O10024的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件，"
        "请生成最新预览，不能正式申请。"
    ),
]


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def decision(name, args, cid):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def stage_messages(stage):
    full = dict(
        order_id="O10024",
        item_id=4,
        quantity=1,
        reason="no_reason",
        condition="sealed",
        secondary_sale_ok=True,
    )
    calls = {
        1: [
            ("get_order", {"order_id": "O10024"}),
            ("search_knowledge", {"query": "七天无理由退货运费"}),
            ("update_return_task", {"order_id": "O10024"}),
        ],
        2: [("update_return_task", full)],
        3: [("get_product", {"product_id": "P003"})],
        4: [("update_return_task", full | {"item_id": 5}), ("preview_return", {})],
        5: [("search_knowledge", {"query": "七天无理由退货期限运费"})],
        6: [],
        7: [("cancel_return_task", {})],
        8: [("update_return_task", full), ("preview_return", {})],
    }[stage]
    messages = [decision(n, args, f"s{stage}-{i}") for i, (n, args) in enumerate(calls)]
    if stage != 6:
        messages.append(
            AIMessage(content="固定脚本业务阶段完成；状态以数据库回执为准，未提交申请。")
        )
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("new")
    stage = sub.add_parser("stage")
    stage.add_argument("session")
    stage.add_argument("index", type=int, choices=range(1, 9))
    for name in ("open", "inspect"):
        sub.add_parser(name).add_argument("session")
    for name in ("decide", "resume", "lookup"):
        p = sub.add_parser(name)
        p.add_argument("session")
        p.add_argument("wait_id")
        if name == "decide":
            p.add_argument("decision", choices=("accept", "reject", "cancel"))
    args = parser.parse_args()

    def offline(*a, **kw):
        raise RuntimeError("M6 CLI forbids network access")

    socket.socket.connect = offline
    if args.command == "init":
        result = init_formal_fixture(args.state_dir)
    elif args.command == "lookup":
        result = read_submission_receipt(args.state_dir, args.session, "USER001", args.wait_id)
    elif args.command == "new":
        settings = fixture_settings(args.state_dir, kind=KIND)
        result = {"session_id": SessionStore(settings.db_path).create("USER001")}
    else:
        model = OfflineModel(
            responses=stage_messages(args.index) if args.command == "stage" else []
        )
        # USER001 is fixed synthetic application identity, never a model argument.
        with FormalReturnPoc(args.state_dir, "USER001", model=model) as poc:
            if args.command == "stage":
                done = list(poc.stream(args.session, STAGES[args.index - 1]))[-1]
                result = {"stage": args.index, "type": done.type, "result": done.detail}
            elif args.command == "inspect":
                result = {
                    "business": poc.gateway.state(args.session, "USER001"),
                    "requests": poc.gateway.service.list_requests("USER001"),
                }
            else:
                result = poc.submission(
                    args.session,
                    args.command,
                    wait_id=getattr(args, "wait_id", None),
                    decision=getattr(args, "decision", None),
                )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
