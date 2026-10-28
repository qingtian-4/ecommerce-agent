"""Offline SDK transport for UI contracts, never a real-model quality result."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import contextmanager
from uuid import uuid4

import httpx

from .config import Settings
from .episode_budget import EpisodeLedger, EpisodeLimits
from .episode_transport import EpisodeCallback, EpisodeSyncTransport
from .langgraph_poc import deepseek_model
from .response_evidence import ResponseEvidence


def decide(messages):
    user_index = max(i for i, m in enumerate(messages) if m["role"] == "user")
    text = messages[user_index]["content"]
    calls = [m for m in messages[user_index + 1 :] if m["role"] == "assistant"]
    offset = len(calls)
    tool = next((m for m in reversed(messages[user_index + 1 :]) if m["role"] == "tool"), None)
    result = json.loads(tool["content"]).get("result", {}) if tool else {}
    if "保存退货草稿并生成预览" in text:
        oid = re.search(r"O\d+", text).group()
        item = int(re.search(r"明细\s*(\d+)", text).group(1))
        quantity = int(re.search(r"数量\s*(\d+)", text).group(1))
        args = dict(
            order_id=oid,
            item_id=item,
            quantity=quantity,
            reason="quality" if "质量问题" in text else "no_reason",
            condition="sealed" if "未拆封" in text else "opened",
        )
        if args["reason"] == "no_reason":
            args["secondary_sale_ok"] = True
        else:
            args.update(quality_non_user_damage=True, problem_description=text)
        sequence = [
            ("get_order", {"order_id": oid}),
            ("update_return_task", args),
            ("preview_return", {}),
        ]
        if offset < len(sequence):
            return sequence[offset], ""
        return None, (
            "预览已生成。请在下方核对商品、数量和原因，然后通过确认按钮操作。"
            if result.get("ready")
            else "预览未就绪，请根据真实工具回执补充信息。"
        )
    if text.strip() == "取消退货草稿":
        return (
            (("cancel_return_task", {}), "")
            if not offset
            else (None, "已处理草稿取消请求，请以当前业务卡片状态为准。")
        )
    if not offset:
        if "政策" in text:
            return ("search_knowledge", {"query": text}), ""
        if "商品" in text or "店里" in text:
            return ("list_products", {}), ""
        if "订单" in text or "物流" in text or "退货" in text:
            oid = re.search(r"O\d+", text)
            if "物流" in text and oid:
                return ("get_logistics", {"order_id": oid.group()}), ""
            return ("list_orders", {}), ""
    if tool:
        if "orders" in result and "测试订单表格" in text:
            table = "| 订单号 | 状态 |\n|---|---|\n" + "\n".join(
                f"| {o['order_id']} | {o['status']} |" for o in result["orders"]
            )
            return (
                None,
                "已查询本人订单。\n\n" + table + "\n\n多候选需要明确选择，咨询内容仍然保留。",
            )
        if "orders" in result:
            return None, (
                "已查询本人订单，请先从卡片中选择需要处理的一笔。\n\n"
                "有多个候选时，我不会自动确定退货对象。"
            )
        if "products" in result:
            return None, "已查询实际合成商品目录，商品规格与价格见下方卡片。"
        return None, "查询已返回，请查看实际回执；未创建任何正式申请。"
    return None, "当前为离线SDK交互验收，可以查询订单、商品或按卡片准备退货。"


def offline_factory(root):
    ledger = EpisodeLedger(root / "offline-fees.jsonl", cap_cny=5, max_requests=48)
    ledger.begin("shop-offline", EpisodeLimits(requests=48, tools=128, episode_timeout_s=86400))
    ledger.evidence = ResponseEvidence(root / ".private-evidence")

    def frame(delta=None, finish=None, usage=None):
        data = dict(
            id="offline-shop",
            object="chat.completion.chunk",
            created=1,
            model="deepseek-flash",
            choices=[],
        )
        if delta is not None:
            data["choices"] = [dict(index=0, delta=delta, finish_reason=finish)]
        if usage:
            data["usage"] = usage
        return b"data: " + json.dumps(data).encode() + b"\n\n"

    class Stream(httpx.AsyncByteStream):
        def __init__(self, request):
            self.body = json.loads(request.content)

        async def __aiter__(self):
            call, text = decide(self.body["messages"])
            if call:
                name, args = call
                yield frame(
                    {
                        "tool_calls": [
                            dict(
                                index=0,
                                id=uuid4().hex,
                                type="function",
                                function=dict(name=name, arguments=json.dumps(args)),
                            )
                        ]
                    },
                    "tool_calls",
                )
            else:
                for start in range(0, len(text), 12):
                    yield frame({"content": text[start : start + 12]})
                    await asyncio.sleep(0.04)
                await asyncio.sleep(
                    2
                    if any(
                        "测试等待状态" in str(m.get("content"))
                        for m in self.body["messages"]
                        if m["role"] == "user"
                    )
                    else 0.5
                )
                bad = any(
                    "触发离线协议错误" in str(m.get("content"))
                    for m in self.body["messages"]
                    if m["role"] == "user"
                )
                yield frame({"content": ""}, "length" if bad else "stop")
            yield frame(usage=dict(prompt_tokens=1000, completion_tokens=200))
            yield b"data: [DONE]\n\n"

    @contextmanager
    def factory():
        with httpx.Client(
            transport=EpisodeSyncTransport(
                ledger,
                "shop-offline",
                "agent",
                lambda: httpx.MockTransport(
                    lambda r: httpx.Response(
                        200, headers={"content-type": "text/event-stream"}, stream=Stream(r)
                    )
                ),
                streaming=True,
                max_body_bytes=32768,
            )
        ) as client:
            yield deepseek_model(
                Settings(
                    _env_file=None,
                    llm_api_key="offline-test",
                    llm_model="deepseek-flash",
                    llm_thinking=False,
                    llm_max_output_tokens=1024,
                ),
                http_client=client,
                callbacks=[EpisodeCallback(ledger, "shop-offline")],
            ).model_copy(update={"streaming": True})

    return factory
