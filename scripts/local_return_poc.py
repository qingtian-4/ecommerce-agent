"""Explicit draft-only CLI; default customer service and tau adapters are unchanged."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from acs.config import Settings  # noqa: E402
from acs.episode_budget import PRICE, EpisodeLedger, EpisodeLimits  # noqa: E402
from acs.episode_transport import (  # noqa: E402
    EpisodeAsyncTransport,
    EpisodeCallback,
    EpisodeSyncTransport,
)
from acs.langgraph_poc import deepseek_model  # noqa: E402
from acs.local_return_poc import LocalReturnPoc  # noqa: E402
from acs.local_return_tools import fixture_settings, init_fixture  # noqa: E402
from acs.response_evidence import ResponseEvidence  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

LIMITS = EpisodeLimits(requests=32, tools=128, steps=256, episode_timeout_s=3600)
EPISODE = "local-return-development-smoke"


class DemoModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class RequestCapture(httpx.AsyncBaseTransport):
    """Authorized LOCAL input evidence only; never headers, never public Git."""

    def __init__(self, ledger, evidence):
        self.ledger, self.evidence = ledger, evidence
        self.inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        body = await request.aread()
        row = next(r for r in reversed(self.ledger.rows()) if r["type"] == "reserved")
        assert row["body_sha256"] == hashlib.sha256(body).hexdigest()
        self.evidence.save(
            uuid4().hex,
            {
                "kind": "authorized_private_model_request",
                "request_id": row["request_id"],
                "body": json.loads(body),
            },
        )
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def verify_price(root, settings):
    reviewed = json.loads((root / "price_review.json").read_text())
    if reviewed["tariff"] != PRICE:
        raise ValueError("Unverified tariff; no paid request allowed")
    if settings.llm_model != PRICE["model"] or settings.llm_base_url.rstrip("/") not in {
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    }:
        raise ValueError("Only the verified direct DeepSeek model is authorized")
    with httpx.Client(timeout=20) as client:
        for locale, url in [("cny", PRICE["source"]), ("usd", PRICE["usd_source"])]:
            response = client.get(url)
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != reviewed[locale]["sha256"]:
                raise ValueError("Official price changed; stop before paid HTTP")


@contextmanager
def paid_model(root, settings):
    verify_price(root, settings)
    ledger = EpisodeLedger(root / "fees.jsonl", cap_cny=3, max_requests=32)
    ledger.begin(EPISODE, LIMITS)
    ledger.evidence = ResponseEvidence(root / ".private-evidence", secrets=(settings.llm_api_key,))

    def factory():
        return RequestCapture(ledger, ledger.evidence)

    with ExitStack() as stack:
        sync = stack.enter_context(
            httpx.Client(transport=EpisodeSyncTransport(ledger, EPISODE, "agent", factory))
        )
        async_client = httpx.AsyncClient(
            transport=EpisodeAsyncTransport(ledger, EPISODE, "agent", factory())
        )
        stack.callback(lambda: asyncio.run(async_client.aclose()))
        settings = settings.model_copy(
            update={
                "llm_temperature": 0,
                "llm_max_output_tokens": 1024,
                "llm_thinking": False,
                "llm_timeout_s": 60,
            }
        )
        yield (
            deepseek_model(
                settings,
                callbacks=[EpisodeCallback(ledger, EPISODE)],
                http_client=sync,
                http_async_client=async_client,
            ),
            ledger,
        )


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--user-id", default="USER001", help="应用注入模拟身份，不是网络认证")
    parser.add_argument("--env-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("new")
    commands.add_parser("demo", help="离线固定输出演示，不是模型质量证据")
    for name in ["ask", "resume", "inspect"]:
        sub = commands.add_parser(name)
        sub.add_argument("session_id")
        if name == "ask":
            sub.add_argument("text")
    for name in [
        "confirmation-open",
        "confirmation-pending",
        "confirmation-resume",
        "confirmation-decide",
    ]:
        sub = commands.add_parser(name, help="应用端隔离确认，无模型请求或正式提交")
        sub.add_argument("session_id")
        if name not in {"confirmation-open", "confirmation-pending"}:
            sub.add_argument("wait_id")
        if name == "confirmation-decide":
            sub.add_argument("decision", choices=["accept", "reject", "cancel"])
    args = parser.parse_args()
    root = args.state_dir
    if args.command == "init":
        print(json.dumps(init_fixture(root), ensure_ascii=False))
        return
    if args.command == "demo":
        init_fixture(root)
    settings = fixture_settings(root)
    store = SessionStore(settings.db_path)
    if args.command == "new":
        print(store.create(args.user_id))
        return
    if args.command == "inspect":
        from acs.local_return_tools import LocalReturnGateway

        print(
            json.dumps(
                {
                    "session": store.load(args.session_id, args.user_id),
                    "business_state": LocalReturnGateway(settings).state(
                        args.session_id, args.user_id
                    ),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.command.startswith("confirmation-"):
        with LocalReturnPoc(root, args.user_id, model=DemoModel(responses=[])) as poc:
            result = poc.confirmation(
                args.session_id,
                args.command.removeprefix("confirmation-"),
                wait_id=getattr(args, "wait_id", None),
                decision=getattr(args, "decision", None),
            )
            print(json.dumps(result, ensure_ascii=False))
        return
    with ExitStack() as stack:
        if args.command == "demo":
            sid = store.create(args.user_id)
            model = DemoModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "demo-draft",
                                "name": "update_return_task",
                                "args": {
                                    "order_id": "O10024",
                                    "item_id": 4,
                                    "quantity": 1,
                                    "reason": "no_reason",
                                    "condition": "sealed",
                                    "secondary_sale_ok": True,
                                },
                            },
                            {"id": "demo-preview", "name": "preview_return", "args": {}},
                        ],
                    ),
                    AIMessage(content="离线演示：隔离预览已生成，尚未创建申请。"),
                ]
            )
            text = "退O10024耳机一件，无理由，满足全部二次销售条件，请生成隔离预览"
        else:
            if args.env_file is None:
                parser.error("Paid ask/resume requires explicit --env-file")
            model, _ = stack.enter_context(
                paid_model(root, fixture_settings(root, Settings(_env_file=args.env_file)))
            )
            sid, text = args.session_id, getattr(args, "text", None)
        poc = stack.enter_context(LocalReturnPoc(root, args.user_id, model=model))
        for event in poc.stream(sid, text):
            print(event.model_dump_json(), flush=True)


if __name__ == "__main__":
    main()
