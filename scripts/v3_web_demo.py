"""V3 loopback customer Demo. Offline fixed scripts by default; paid mode explicit."""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from local_formal_submission import STAGES, stage_messages  # noqa: E402
from local_return_poc import paid_model  # noqa: E402

from acs.config import Settings  # noqa: E402
from acs.local_submission import init_formal_fixture  # noqa: E402
from acs.web_service import create_app  # noqa: E402


class FixedDemoModel(BaseChatModel):
    """Exact frozen decisions for browser/HTTP contracts, never model quality evidence."""

    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-web-fixed-script"

    def bind_tools(self, *args, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        self.seen.append(messages)
        position = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        user = messages[position]
        text = user.text
        offset = sum(isinstance(m, AIMessage) for m in messages[position + 1 :])
        if text in STAGES:
            replies = stage_messages(STAGES.index(text) + 1)
        elif text in {"查询P003充电器接口", "查询我的订单"}:
            name, args = (
                ("get_product", {"product_id": "P003"}) if "P003" in text else ("list_orders", {})
            )
            replies = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "query-" + str(len(messages)),
                            "name": name,
                            "args": args,
                        }
                    ],
                ),
                AIMessage(content="已完成隔离查询，详情以工具和业务状态为准。"),
            ]
        else:
            replies = [
                AIMessage(
                    content="当前是固定脚本演示，请选择“演示用例”；自由对话需显式启用真实模型。"
                )
            ]
        reply = replies[offset]
        if reply.tool_calls:
            reply = reply.model_copy(
                update={
                    "tool_calls": [dict(c, id=f"{user.id}:{c['id']}") for c in reply.tool_calls]
                }
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


@contextmanager
def offline_factory():
    yield FixedDemoModel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--init", action="store_true", help="创建不存在的新合成 Fixture")
    parser.add_argument("--deepseek", action="store_true", help="显式付费模式，¥3/24 HTTP硬上限")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.init:
        init_formal_fixture(args.state_dir)
    factory, secret_values = offline_factory, ()
    if args.deepseek:
        settings = Settings(_env_file=args.env_file)
        secret_values = (settings.llm_api_key,)

        @contextmanager
        def factory():
            with paid_model(args.state_dir, settings, max_requests=24) as (model, ledger):
                yield model

    app = create_app(
        args.state_dir, factory, origin=f"http://127.0.0.1:{args.port}", secret_values=secret_values
    )
    print(
        f"V3 synthetic Demo: http://127.0.0.1:{args.port} "
        f"({'DeepSeek capped ¥3 / 24 requests' if args.deepseek else 'OFFLINE fixed scripts'})"
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        workers=1,
        access_log=False,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
