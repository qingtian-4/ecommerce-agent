"""Local draft/preview host over the existing native create_agent runtime.

SessionStore owns turns, the business DB owns receipts, SqliteSaver owns messages.
No custom model/tool loop, submission tool, or checkpoint-derived authority.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import ExitStack
from threading import Lock
from uuid import uuid4

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
    before_model,
    wrap_model_call,
)
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langgraph.checkpoint.sqlite import SqliteSaver

from .cs_tools import tool_model_content
from .langgraph_poc import AuditEvent
from .local_return_tools import (
    TOOLS,
    WRITES,
    BoundStore,
    LocalReturnGateway,
    ReconciliationError,
    TurnContext,
    fixture_settings,
    model_view,
)
from .model_response_contract import require_response
from .sessions import ConversationBusy

PROMPT = """你是电商客服。本实验仅支持本人商品、订单、物流和知识查询，退货草稿及预览。
用户身份和会话由应用提供，不能切换。订单、商品明细id、数量来自业务工具，政策来自知识工具。
政策须匹配商品类别；缺少适用政策时仅可查询规格、价格、订单和物流，不得套用期限或承诺保修、安装、换货。
明确退货意图必须调用update_return_task保存用户提供的字段，不能只口头答应；缺信息保留待办并询问。
先读取get_return_task及订单；只记录用户明确说出的字段，不能猜商品、数量、二次销售或非人为声明。
用户插问、否定取消、假设或引用取消都保留草稿。更正覆盖旧值，换订单/商品会清除依赖字段，需重新补齐。
仅明确的整句取消授权可清空草稿；混合取消授权不足时说明尚未取消，同时正常回答咨询，不能吞掉子问题。
每轮分别完成用户的业务动作和咨询子目标，用工具真实回执说明状态，不把中间文字当作完成。
信息齐全且用户要求时调用preview_return；只展示最新有效预览，更正后旧预览失效。
本实验没有确认提交、审核、退款、换货、真实人工转接入口，不能声称已申请或已退款。
知识、商品或政策查询不会自动建立、修改或取消草稿。当前应用事实优先于历史消息。
"""


class LocalState(AgentState):
    turn_id: str
    expected_revision: int


class LocalReturnPoc:
    fixture_kind = "local-return-draft-only-1"
    prompt = PROMPT
    preview_description = "核查当前草稿、政策与数量并生成隔离预览；没有提交、审核或退款入口。"

    def __init__(self, root, user_id, *, model, registry=None, fault=None, stream_callback=None,
                 read_only=False):
        self.settings = fixture_settings(root, kind=self.fixture_kind)
        self.root = root.resolve()
        self.user_id, self.model = user_id, model
        self.gateway = LocalReturnGateway(self.settings, registry=registry, fault=fault)
        self.store = self.gateway.store
        self.path = self.root / "graph.sqlite"
        self.run_lock = Lock()
        self.stream_callback = stream_callback
        self.read_only = read_only  # Fresh application restriction, never checkpoint authority.

    def __enter__(self):
        self.stack = ExitStack()
        try:
            lock = self.stack.enter_context((self.root / "worker.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self.path.exists():
                with self.store.transaction() as conn:
                    if conn.execute("SELECT 1 FROM local_threads").fetchone():
                        raise ReconciliationError("Graph file missing for existing sessions")
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self.path.chmod(0o600)
            self.stack.callback(self.conn.close)
            signature = json.dumps(
                {
                    "version": 1,
                    "db": str(self.settings.db_path.resolve()),
                    "fixture": json.loads((self.root / "fixture.json").read_text())["id"],
                },
                sort_keys=True,
            )
            with self.conn:
                self.conn.execute("CREATE TABLE IF NOT EXISTS local_runtime(signature TEXT)")
                row = self.conn.execute("SELECT signature FROM local_runtime").fetchone()
                if row and row[0] != signature:
                    raise ReconciliationError("Graph and business fixture mismatch")
                if not row:
                    self.conn.execute("INSERT INTO local_runtime VALUES(?)", (signature,))
            self.sequence = 0
            if (self.root / "trace.jsonl").exists():
                with (self.root / "trace.jsonl").open() as trace:
                    self.sequence = sum(1 for _ in trace)
            specs = {s["function"]["name"]: s["function"] for s in self.gateway.registry.schemas()}
            # Obtain session tools' canonical schemas without authorizing any execution.
            from .cs_tools import ToolRegistry

            schema_registry = ToolRegistry(self.settings)
            schema_registry.bind_return_task(self.store, "", "")
            specs.update({s["function"]["name"]: s["function"] for s in schema_registry.schemas()})

            @before_model(state_schema=LocalState)
            def refresh(state, runtime):
                context = runtime.context
                with self.store.transaction() as conn:
                    self.gateway.active(conn, context)
                    revision = (self.store._task(conn, context.session_id) or {}).get("revision", 0)
                return {"expected_revision": revision}

            @wrap_model_call
            def boundary(request, handler):
                context = request.runtime.context
                with self.store.transaction() as conn:
                    self.gateway.active(conn, context)
                facts = self.gateway.state(context.session_id, context.user_id)
                # Request projection only: preserve native history and tool-call/result pairs.
                # Exclude only legacy server-generated snapshots from old checkpoints.
                messages = [
                    m
                    for m in request.messages
                    if not (
                        isinstance(m, SystemMessage) and m.text.startswith("当前应用核验事实：")
                    )
                ]
                messages.append(
                    SystemMessage(
                        content="当前应用核验事实：" + json.dumps(facts, ensure_ascii=False)
                    )
                )
                from .return_rules import consultation_policy

                if (consultation_policy(self.settings) and getattr(self.model, "_llm_type", "")
                        != "m8-controlled-model-boundary"):
                    from langchain_core.messages import (
                        convert_to_messages,
                        convert_to_openai_messages,
                    )

                    from .shop_model_context import INSTRUCTIONS, project_messages

                    messages = convert_to_messages(project_messages(
                        convert_to_openai_messages(messages)))
                    messages.append(SystemMessage(content=INSTRUCTIONS))
                response = handler(request.override(messages=messages))
                used = {
                    c["id"]
                    for m in request.state["messages"]
                    if isinstance(m, AIMessage)
                    for c in m.tool_calls
                }
                for message in response.result:
                    if isinstance(message, AIMessage):
                        require_response(
                            message.content,
                            [
                                dict(id=c["id"], name=c["name"], arguments=c["args"])
                                for c in message.tool_calls
                            ],
                            message.response_metadata.get("finish_reason"),
                            known_tools={n for n in TOOLS if not self.read_only or n not in WRITES},
                            used_ids=used,
                            invalid_calls=message.invalid_tool_calls,
                        )
                return response

            self.graph = create_agent(
                self.model,
                tools=[
                    self._tool(n, specs[n]["description"], schema) for n, schema in TOOLS.items()
                    if not self.read_only or n not in WRITES
                ],
                system_prompt=self.prompt + (
                    "\n本轮应用仅授权只读咨询；不能修改草稿、取消任务或生成新预览。"
                    if self.read_only else ""),
                checkpointer=SqliteSaver(self.conn),
                state_schema=LocalState,
                context_schema=TurnContext,
                middleware=[
                    refresh,
                    boundary,
                    ModelCallLimitMiddleware(run_limit=8, exit_behavior="error"),
                    ToolCallLimitMiddleware(run_limit=16, exit_behavior="error"),
                ],
            )
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        self.stack.close()

    def confirmation(self, sid, action, *, wait_id=None, decision=None, fault=None):
        """Trusted application API only; deliberately absent from model ToolRegistry."""
        if not self.run_lock.acquire(blocking=False):
            raise ConversationBusy("Local worker is executing another application operation")
        try:
            from .local_confirmation import LocalConfirmation

            gate = LocalConfirmation(self.gateway, SqliteSaver(self.conn), fault=fault)
            if action == "open":
                return gate.open(sid, self.user_id)
            if action == "pending":
                return gate.pending(sid, self.user_id)
            if action == "decide":
                return gate.decide(sid, self.user_id, wait_id, decision)
            if action == "resume":
                return gate.resume(sid, self.user_id, wait_id)
            raise ValueError("Unknown application confirmation action")
        finally:
            self.run_lock.release()

    def _tool(self, name, description, schema):
        if name == "preview_return":
            description = self.preview_description

        def execute(runtime: ToolRuntime, **arguments):
            if self.read_only and name in WRITES:
                raise PermissionError("Application restricted this Turn to read-only tools")
            context = runtime.context  # Supplied anew by the application on EVERY invocation.
            if runtime.state["turn_id"] != context.turn_id:
                raise ReconciliationError("Graph Turn does not match current authority")
            calls = next(
                m for m in reversed(runtime.state["messages"]) if isinstance(m, AIMessage)
            ).tool_calls
            index = next(i for i, c in enumerate(calls) if c["id"] == runtime.tool_call_id)
            prior = [c["id"] for c in calls[:index] if c["name"] in WRITES]
            runtime.stream_writer(
                {"type": "tool_started", "name": name, "call_id": runtime.tool_call_id}
            )
            receipt = self.gateway.execute(
                name,
                arguments,
                runtime.tool_call_id,
                context,
                expected_revision=runtime.state["expected_revision"],
                prior_writes=prior,
            )
            runtime.stream_writer(
                {
                    "type": "tool_receipt",
                    "name": name,
                    "call_id": runtime.tool_call_id,
                    "receipt": receipt,
                }
            )
            if receipt["error"]:
                raise ToolException(json.dumps(receipt, ensure_ascii=False))
            return tool_model_content(receipt)

        return StructuredTool.from_function(
            execute,
            name=name,
            description=description,
            args_schema=schema.model_json_schema(),
            handle_validation_error="参数无效；按Schema更正，未执行该调用。",
            handle_tool_error=True,
        )

    def _binding(self, sid):
        self.store.load(sid, self.user_id)
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM local_threads WHERE session_id=?", (sid,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO local_threads VALUES(?,?,NULL,0)", (sid, uuid4().hex))
                row = conn.execute(
                    "SELECT * FROM local_threads WHERE session_id=?", (sid,)
                ).fetchone()
            return dict(row)

    @staticmethod
    def _config(binding):
        return {
            "configurable": {"thread_id": binding["thread_id"]},
            "recursion_limit": 60,
            "max_concurrency": 1,
        }

    def _reconcile(self, sid, binding, state):
        messages = state.values.get("messages", [])[binding["offset"] :]
        with self.store.transaction() as conn:
            for msg in messages:
                if not isinstance(msg, AIMessage):
                    continue
                for call in msg.tool_calls:
                    if call["name"] not in WRITES:
                        continue
                    delivered = next(
                        (
                            m
                            for m in messages
                            if isinstance(m, ToolMessage) and m.tool_call_id == call["id"]
                        ),
                        None,
                    )
                    if delivered is None:
                        continue
                    receipt = conn.execute(
                        "SELECT * FROM local_receipts WHERE session_id=? "
                        "AND turn_id=? AND call_id=?",
                        (sid, binding["turn_id"], call["id"]),
                    ).fetchone()
                    if receipt is None:
                        if delivered.status == "error":  # Native validation never entered gateway.
                            continue
                        raise ReconciliationError("Graph write result has no business receipt")
                    canonical = json.dumps(
                        TOOLS[call["name"]]
                        .model_validate(call["args"])
                        .model_dump(exclude_unset=True),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if (receipt["name"], receipt["arguments"]) != (call["name"], canonical):
                        raise ReconciliationError("Graph call differs from business receipt")
                    exposed = json.loads(delivered.content)
                    if model_view(exposed["result"]) != model_view(json.loads(receipt["result"])):
                        raise ReconciliationError("Graph result differs from business receipt")

    def stream(self, sid, text=None):
        if not self.run_lock.acquire(blocking=False):
            raise ConversationBusy("Single local worker is already executing a Turn")
        try:
            yield from self._stream(sid, text)
        finally:
            self.run_lock.release()

    def _stream(self, sid, text):
        binding = self._binding(sid)
        config = self._config(binding)
        if self.stream_callback is not None:
            config["callbacks"] = [self.stream_callback]
        state = self.graph.get_state(config)
        if self.read_only and state.next:
            delivered = {m.tool_call_id for m in state.values.get("messages", [])
                         if isinstance(m, ToolMessage)}
            if any(c["name"] in WRITES and c["id"] not in delivered
                   for m in state.values.get("messages", []) if isinstance(m, AIMessage)
                   for c in m.tool_calls):
                raise PermissionError(
                    "Read-only recovery cannot replay a pending write; reconcile first")
        if binding["turn_id"]:
            with self.store.transaction() as conn:
                previous = conn.execute(
                    "SELECT status FROM conversation_turns WHERE session_id=? AND turn_id=?",
                    (sid, binding["turn_id"]),
                ).fetchone()
            if previous and previous["status"] == "completed":
                last = state.values.get("messages", [None])[-1]
                if (
                    state.values.get("turn_id") != binding["turn_id"]
                    or state.next
                    or not isinstance(last, AIMessage)
                    or last.tool_calls
                    or not last.text
                ):
                    raise ReconciliationError(
                        "Completed business Turn has inconsistent Graph image"
                    )
                self._reconcile(sid, binding, state)
        if text is not None:
            if not text.strip():
                raise ValueError("Empty message")
            if state.next:
                raise ConversationBusy("Pending Graph execution requires explicit resume")
            with self.store.transaction() as conn:
                tid = BoundStore(self.settings.db_path, conn).begin_turn(sid, self.user_id, text)
                offset = len(state.values.get("messages", []))
                conn.execute(
                    "UPDATE local_threads SET turn_id=?,offset=? WHERE session_id=?",
                    (tid, offset, sid),
                )
            binding.update(turn_id=tid, offset=offset)
            inputs = {"messages": [HumanMessage(content=text)], "turn_id": tid}
        else:
            tid = binding["turn_id"]
            with self.store.transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM conversation_turns WHERE session_id=? AND turn_id=?", (sid, tid)
                ).fetchone()
                if row is None:
                    raise ValueError("No Turn to resume")
                completed = row["status"] == "completed"
                if not completed:
                    self.gateway.active(conn, TurnContext(sid, self.user_id, tid))
            if completed:
                if state.next:
                    raise ReconciliationError("Completed Turn has pending Graph execution")
                result = json.loads(row["result_json"]) | {
                    "answer_is_historical": True,
                    "business_state": self.gateway.state(sid, self.user_id),
                }
                yield self._event(sid, tid, "completed", result)
                return
            if state.values.get("turn_id") == tid:
                self._reconcile(sid, binding, state)
                inputs = None
            else:
                # Crash after reserving the business Turn but before initial Graph checkpoint.
                if len(state.values.get("messages", [])) != binding["offset"]:
                    raise ReconciliationError("Running Turn has inconsistent Graph image")
                with self.store.transaction() as conn:
                    if conn.execute(
                        "SELECT 1 FROM local_receipts WHERE session_id=? AND turn_id=?", (sid, tid)
                    ).fetchone():
                        raise ReconciliationError("Graph image missing for committed receipts")
                inputs = {"messages": [HumanMessage(content=row["user_text"])], "turn_id": tid}
        context = TurnContext(sid, self.user_id, tid)
        yield self._event(sid, tid, "started", {"resume": text is None})
        try:
            for mode, data in self.graph.stream(
                inputs,
                config,
                context=context,
                stream_mode=["updates"],
                durability="sync",
            ):
                yield self._event(sid, tid, mode, data)
            final = self.graph.get_state(config)
            last = final.values["messages"][-1]
            if final.next or not isinstance(last, AIMessage) or last.tool_calls or not last.text:
                raise ReconciliationError("Graph has no complete final answer")
            self._reconcile(sid, binding, final)
            messages = final.values["messages"][binding["offset"] :]
            saved = []
            for m in messages:
                if isinstance(m, HumanMessage):
                    saved.append({"role": "user", "content": m.content})
                elif isinstance(m, AIMessage):
                    item = {"role": "assistant", "content": m.content}
                    if m.tool_calls:
                        item["tool_calls"] = [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {
                                    "name": c["name"],
                                    "arguments": json.dumps(c["args"], ensure_ascii=False),
                                },
                            }
                            for c in m.tool_calls
                        ]
                    saved.append(item)
                elif isinstance(m, ToolMessage):
                    saved.append(
                        {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
                    )
            result = {
                "answer": last.text,
                "stop_reason": "answered",
                "business_state": self.gateway.state(sid, self.user_id),
            }
            self.store.finish_turn(sid, self.user_id, tid, saved, result)
            yield self._event(sid, tid, "completed", result)
        except BaseException as exc:
            self._event(sid, tid, "interrupted", {"error_type": type(exc).__name__})
            raise

    def _event(self, sid, tid, kind, detail):
        self.sequence += 1
        event = AuditEvent(
            run_id=tid, session_id=sid, sequence=self.sequence, type=kind, detail=detail
        )
        with os.fdopen(
            os.open(self.root / "trace.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "w"
        ) as out:
            out.write(
                json.dumps(
                    event.model_dump(),
                    ensure_ascii=False,
                    default=lambda x: x.model_dump() if hasattr(x, "model_dump") else str(x),
                )
                + "\n"
            )
            out.flush()
            os.fsync(out.fileno())
        return event
