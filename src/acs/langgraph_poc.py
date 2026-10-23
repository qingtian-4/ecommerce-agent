"""Isolated read-only create_agent experiment; never imported by the default runtime."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    wrap_model_call,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .cs_tools import ToolRegistry

RUNTIME_VERSION = "v3-readonly-1"


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProductArgs(EmptyArgs):
    product_id: str


class OrderArgs(EmptyArgs):
    order_id: str


READ_TOOLS = {
    "list_products": EmptyArgs,
    "get_product": ProductArgs,
    "get_price_history": ProductArgs,
    "list_orders": EmptyArgs,
    "get_order": OrderArgs,
    "get_logistics": OrderArgs,
}

PROMPT = """你是电商客服。本实验只能查询商品、本人订单、物流和价格，不能办理业务。
商品、金额、日期、订单、物流必须依据对应工具的结果；缺少信息明确说未知。
多候选商品或订单必须澄清，不能猜选；代词结合历史，仍不明确就追问。
保留用户之前的约束，明确更正覆盖旧值；临时插问不取消原问题。
历史业务结果可能过时，询问当前状态时重新查询。区分每个商品和订单的事实。
回答简洁自然，覆盖本轮各项问题。没有政策工具，不能编造退货、换货、价保政策或资格。
模型没有提交、审核、退款、转人工能力；不能宣称这些操作已完成。
"""


class AuditEvent(BaseModel):
    version: Literal[1] = 1
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    session_id: str
    sequence: int
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    type: str
    detail: dict[str, Any] = Field(default_factory=dict)


@wrap_model_call
def validate_model_protocol(request, handler):
    response = handler(request)
    for message in response.result:
        if not isinstance(message, AIMessage):
            continue
        finish = message.response_metadata.get("finish_reason")
        if finish not in {None, "stop", "tool_calls"} or message.invalid_tool_calls:
            raise RuntimeError(f"Incomplete model response: {finish}")
        if any(call["name"] not in READ_TOOLS for call in message.tool_calls):
            raise PermissionError("模型请求了未开放工具；本轮停止，未执行该请求")
    return response


def deepseek_model(settings: Settings, *, callbacks=None, http_client=None,
                   http_async_client=None) -> ChatOpenAI:
    if not settings.has_llm_key:
        raise ValueError("未配置 DeepSeek API key")
    if settings.llm_thinking:
        raise ValueError("只读 PoC 仅验收 non-thinking 模式")
    return ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url, temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_output_tokens, timeout=settings.llm_timeout_s,
        max_retries=0, use_responses_api=False,
        extra_body={"thinking": {"type": "disabled"}},
        stream_usage=True, callbacks=callbacks,
        http_client=http_client, http_async_client=http_async_client,
    )


class ReadOnlyPoc:
    """One local worker per checkpoint file; identity comes from the application.

    Hold the context open while consuming stream(). A file lock rejects competing
    workers; SIGKILL releases it. This is a local CLI boundary, not HTTP authentication.
    """

    def __init__(self, settings: Settings, checkpoint_path: Path, user_id: str,
                 *, model: BaseChatModel, registry: ToolRegistry | None = None):
        if checkpoint_path.resolve() == settings.db_path.resolve():
            raise ValueError("Graph 与业务数据库必须分开")
        self.settings, self.path, self.user_id = settings, checkpoint_path, user_id
        self.model = model
        self.registry = registry or ToolRegistry(settings)
        self.audit_path = checkpoint_path.with_suffix(".events.jsonl")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stack = ExitStack()
        try:
            lock = self._stack.enter_context(self.path.with_suffix(".lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self._stack.callback(self.conn.close)
            with self.conn:
                self.conn.execute("CREATE TABLE IF NOT EXISTS poc_threads ("
                                  "session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                                  "runtime_version TEXT NOT NULL, business_path TEXT NOT NULL)")
            specs = {s["function"]["name"]: s["function"] for s in self.registry.schemas()}
            tools = [self._tool(name, specs[name]["description"], args)
                     for name, args in READ_TOOLS.items()]
            self.graph = create_agent(
                self.model, tools=tools, system_prompt=PROMPT,
                checkpointer=SqliteSaver(self.conn),
                middleware=[
                    ModelCallLimitMiddleware(run_limit=6, exit_behavior="error"),
                    ToolCallLimitMiddleware(run_limit=12, exit_behavior="continue"),
                    validate_model_protocol,
                ],
            )
            return self
        except BaseException:
            self._stack.close()
            raise

    def __exit__(self, *exc):
        self._stack.close()

    def _tool(self, name, description, args_schema):
        def execute(**arguments):
            writer = get_stream_writer()
            writer({"type": "tool_started", "name": name, "arguments": arguments})
            result, events, error = self.registry.run(
                name, json.dumps(arguments, ensure_ascii=False), current_user_id=self.user_id,
            )
            writer({"type": "tool_finished", "name": name, "error": error,
                    "trace": [e.as_dict() for e in events]})
            if error:
                raise ToolException(json.dumps(result, ensure_ascii=False))
            return json.dumps(result, ensure_ascii=False)

        return StructuredTool.from_function(
            execute, name=name, description=description, args_schema=args_schema,
            handle_validation_error="工具参数无效，请按 schema 更正后重试。",
            handle_tool_error=True,
        )

    def _config(self, session_id: str, *, create: bool = False) -> dict:
        if not session_id.strip():
            raise ValueError("session_id 不能为空")
        with self.conn:
            if create:
                self.conn.execute("INSERT OR IGNORE INTO poc_threads VALUES(?,?,?,?)", (
                    session_id, self.user_id, RUNTIME_VERSION, str(self.settings.db_path.resolve()),
                ))
            row = self.conn.execute("SELECT user_id,runtime_version,business_path "
                                    "FROM poc_threads WHERE session_id=?", (session_id,)).fetchone()
        if row is None or row[0] != self.user_id:
            raise PermissionError("会话不存在或无权访问")
        if row[1:] != (RUNTIME_VERSION, str(self.settings.db_path.resolve())):
            raise ValueError("Checkpoint 运行版本或业务数据库不匹配，须使用新会话")
        return {"configurable": {"thread_id": session_id}, "recursion_limit": 40}

    def snapshot(self, session_id: str):
        return self.graph.get_state(self._config(session_id))

    def stream(self, session_id: str, text: str | None = None) -> Iterator[AuditEvent]:
        """None resumes pending work. A completed checkpoint is never replayed."""
        if text is not None and not text.strip():
            raise ValueError("问题不能为空")
        config = self._config(session_id, create=text is not None)
        before = self.graph.get_state(config)
        if text is not None and before.next:
            raise ValueError("仍有未完成执行，请先 resume")
        if text is None and not before.next:
            raise ValueError("没有待恢复的执行")
        run_id, sequence = uuid4().hex, 0

        def event(kind, detail):
            nonlocal sequence
            sequence += 1
            value = AuditEvent(run_id=run_id, session_id=session_id, sequence=sequence,
                               type=kind, detail=detail)
            with self.audit_path.open("a", encoding="utf-8") as out:
                out.write(value.model_dump_json() + "\n")
                out.flush()
                os.fsync(out.fileno())
            return value

        inputs = None if text is None else {"messages": [{"role": "user", "content": text}]}
        completed = False
        try:
            yield event("run_started", {"resume": text is None, "runtime": RUNTIME_VERSION})
            for mode, data in self.graph.stream(
                inputs, config, stream_mode=["updates", "custom", "messages"], durability="sync",
            ):
                if mode == "custom":
                    yield event(data["type"], data)
                elif mode == "messages":
                    chunk, metadata = data
                    # Token text is provisional. Never expose reasoning or tool arguments here.
                    if metadata.get("langgraph_node") == "model" and chunk.content:
                        yield event("token", {"text": chunk.text, "provisional": True})
                else:
                    for node, update in data.items():
                        messages = (update or {}).get("messages", [])
                        yield event("node_update", {
                            "node": node,
                            "messages": [{"type": m.type, "content": m.content,
                                          "tool_calls": getattr(m, "tool_calls", []),
                                          "tool_call_id": getattr(m, "tool_call_id", None),
                                          "status": getattr(m, "status", None),
                                          "usage": getattr(m, "usage_metadata", None)}
                                         for m in messages
                                         if isinstance(m, (AIMessage, ToolMessage))],
                            "provisional": True,
                        })
            state = self.graph.get_state(config)
            last = state.values["messages"][-1]
            if (state.next or not isinstance(last, AIMessage) or last.tool_calls
                    or last.invalid_tool_calls or not last.text.strip()):
                raise RuntimeError("Graph 未达到最终回答状态")
            final_event = event("run_completed", {
                "answer": last.text,
                "checkpoint_id": state.config["configurable"]["checkpoint_id"],
            })
            completed = True
            yield final_event
        except GeneratorExit:
            if not completed:
                event("run_interrupted", {"reason": "consumer_closed"})
            raise
        except Exception as exc:
            event("run_failed", {"error_type": type(exc).__name__})
            raise
