"""Persistent native create_agent bridge. Only the official environment executes tools.

POSIX local-worker implementation. The transport journal records dispatch before
returning an intent. A missing receipt after restart is UNKNOWN, never permission
to replay a write. Restoring the official environment is the caller's responsibility.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from httpx import HTTPError
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages import ToolMessage as GraphToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from openai import APIError
from tau2.agent.base_agent import HalfDuplexAgent, is_valid_agent_history_message
from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau_tool_protocol import OfficialToolProtocol

from acs.langgraph_poc import deepseek_model
from acs.model_response_contract import ModelResponseError, require_response

VERSION = "tau-langgraph-halfduplex-2"


class TaskBudgetExceeded(RuntimeError):
    pass


class IncompleteModelResponse(ModelResponseError):
    pass


@dataclass(frozen=True)
class LangGraphState:
    thread_id: str


def native_message(message):
    if not is_valid_agent_history_message(message) or getattr(message, "is_audio", False):
        raise ValueError("Unsupported official history message")
    if isinstance(message, ToolMessage):
        return GraphToolMessage(
            content=message.content or "",
            tool_call_id=message.id,
            id="receipt:" + message.id,
            status="error" if message.error else "success",
        )
    if isinstance(message, UserMessage):
        return HumanMessage(content=message.content or "")
    return AIMessage(
        content=message.content or "",
        tool_calls=[
            {"id": c.id, "name": c.name, "args": c.arguments} for c in message.tool_calls or []
        ],
    )


def no_local_execution(**kwargs):
    raise AssertionError("Domain tools must execute only in the official Orchestrator")


class LangGraphAgent(HalfDuplexAgent[LangGraphState]):
    def __init__(
        self,
        tools,
        domain_policy,
        *,
        checkpoint_path: Path,
        model,
        max_model_calls=6,
        max_tool_calls=12,
        model_timeout_s=60,
        task_timeout_s=120,
        response_observer=None,
    ):
        super().__init__(tools, domain_policy)
        if min(max_model_calls, max_tool_calls, model_timeout_s, task_timeout_s) <= 0:
            raise ValueError("Budgets must be positive")
        self.path, self.model = Path(checkpoint_path).resolve(), model
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_model_calls, self.max_tool_calls = max_model_calls, max_tool_calls
        self.model_timeout_s, self.task_timeout_s = model_timeout_s, task_timeout_s
        self.protocol = OfficialToolProtocol(tools)
        self.response_observer = response_observer
        self.prompt = SYSTEM_PROMPT.format(
            agent_instruction=AGENT_INSTRUCTION, domain_policy=domain_policy
        )
        self.schemas = [tool.openai_schema for tool in tools]
        self.native_tools = [
            StructuredTool.from_function(
                no_local_execution,
                name=s["function"]["name"],
                description=s["function"]["description"],
                args_schema=s["function"]["parameters"],
            )
            for s in self.schemas
        ]
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "version": VERSION,
                    "prompt": self.prompt,
                    "tools": self.schemas,
                    "model_type": model._llm_type,
                    "model_config": {
                        k: getattr(model, k, None)
                        for k in (
                            "model_name",
                            "temperature",
                            "max_tokens",
                            "openai_api_base",
                            "request_timeout",
                            "model_kwargs",
                            "extra_body",
                            "max_retries",
                        )
                    },
                    "limits": [max_model_calls, max_tool_calls, model_timeout_s, task_timeout_s],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self._mutex = threading.Lock()
        self._runner = None
        self._closed = False

    @contextmanager
    def _locked(self):
        if self._closed:
            raise RuntimeError("Adapter stopped; restore with a fresh model and adapter")
        if not self._mutex.acquire(blocking=False):
            raise RuntimeError("Adapter operation already in progress")
        try:
            with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with closing(sqlite3.connect(self.path)) as conn, conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS tau_transport ("
                        "thread_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                        "record TEXT NOT NULL)"
                    )
                yield
        finally:
            self._mutex.release()

    def _load(self, state):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            row = conn.execute(
                "SELECT fingerprint,record FROM tau_transport WHERE thread_id=?", (state.thread_id,)
            ).fetchone()
        if row is None or row[0] != self.fingerprint:
            raise ValueError("Unknown thread or incompatible model/policy/tools/budgets")
        record = json.loads(row[1])
        # Also protect journals produced before an interrupted terminal projection.
        for receipt in record["protocol"]:
            if receipt["uncertain_write"] or receipt["protocol_error"]:
                record["stop_reason"] = (
                    "state_uncertain" if receipt["uncertain_write"] else "adapter_protocol_error"
                )
                break
        return record

    def _save(self, state, record):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO tau_transport VALUES(?,?,?)",
                (state.thread_id, self.fingerprint, json.dumps(record)),
            )

    def get_init_state(self, message_history=None):
        history = message_history or []
        # Accept complete message pairs only; live pending calls belong to the journal.
        pending, seen = set(), set()
        for message in history:
            native_message(message)
            if isinstance(message, AssistantMessage):
                if pending:
                    raise ValueError("Unpaired tool history")
                for call in message.tool_calls or []:
                    if call.id in seen:
                        raise ValueError("Duplicate historical tool call ID")
                    pending.add(call.id)
                    seen.add(call.id)
            elif isinstance(message, ToolMessage):
                if message.id not in pending:
                    raise ValueError("Unexpected historical tool result")
                pending.remove(message.id)
            elif pending:
                raise ValueError("Unpaired tool history")
        if pending:
            raise ValueError("Restore pending executions by thread_id, not incomplete history")
        state = LangGraphState(uuid4().hex)
        with self._locked():
            self._save(
                state,
                {
                    "history": [m.model_dump(mode="json") for m in history],
                    "deadline": time.time() + self.task_timeout_s,
                    "model_calls": 0,
                    "tool_calls": 0,
                    "batch": [],
                    "pending": None,
                    "receipts": {},
                    "protocol": [],
                    "used_ids": list(seen),
                    "status": "idle",
                    "stop_reason": None,
                },
            )
        return state

    def generate_next_message(self, message, state):
        with self._locked():
            record = self._load(state)
            if record["stop_reason"]:
                raise ValueError("Task already stopped")
            if isinstance(message, UserMessage):
                if record["status"] != "idle" or message.is_tool_call() or message.is_audio:
                    raise ValueError(
                        "Expected pending official tool receipt or text-only user input"
                    )
                record.pop("final_reply", None)
                record["status"] = "model"
                record["input"] = {"id": "input:" + uuid4().hex, "text": message.content or ""}
                self._save(state, record)
            elif isinstance(message, ToolMessage):
                if message.id != record["pending"] or message.requestor != "assistant":
                    raise ValueError("Wrong, duplicate or unsolicited official tool result ID")
                call = next(c for c in record["batch"] if c["id"] == message.id)
                report = self.protocol.inspect(ToolCall(**call), message)
                record["receipts"][message.id] = report
                record["protocol"].append(report)
                record["pending"] = None
                if report["uncertain_write"] or report["protocol_error"]:
                    # Receipt and terminal guard commit together, including crash recovery.
                    record["stop_reason"] = (
                        "state_uncertain" if report["uncertain_write"] else "adapter_protocol_error"
                    )
                self._save(state, record)
                if record["stop_reason"]:
                    return self._stopped(state, record, record["stop_reason"]), state
            else:
                raise TypeError("Adapter emits serial calls and expects a single ToolMessage")
            return self._run_graph(state, record), state

    def resume(self, state):
        """Resume graph work, never re-emit a previously dispatched tool intent.

        An outstanding official result must be supplied through generate_next_message.
        Restoring Graph alone does not restore the external official Retail DB.
        """
        with self._locked():
            record = self._load(state)
            if record["stop_reason"]:
                return self._stopped(state, record, record["stop_reason"]), state
            if record["pending"]:
                return AssistantMessage.text(
                    "An official tool receipt is missing. No operation was replayed.",
                    raw_data={
                        "acs_stop_reason": "state_uncertain",
                        "pending_id": record["pending"],
                    },
                ), state
            if record["status"] == "idle":
                if record.get("final_reply"):
                    return AssistantMessage(**record["final_reply"]), state
                raise ValueError("No pending graph work")
            return self._run_graph(state, record), state

    def _run_graph(self, state, record):
        # HTTP model clients retain loop-bound pools across HalfDuplex operations.
        if self._runner is None:
            self._runner = asyncio.Runner()
        return self._runner.run(self._advance(state, record))

    def _remaining(self, record):
        remaining = record["deadline"] - time.time()
        if remaining <= 0:
            raise TaskBudgetExceeded("Total task deadline exceeded")
        return remaining

    def _stopped(self, state, record, reason):
        record["stop_reason"] = reason
        self._save(state, record)
        return AssistantMessage.text(
            "Execution stopped: " + reason + ". No further operations were attempted.",
            raw_data={
                "acs_stop_reason": reason,
                "pending_id": record["pending"],
                "model_calls": record["model_calls"],
                "tool_calls": record["tool_calls"],
                "protocol": record["protocol"],
                "response_predicate": record.get("response_predicate"),
            },
        )

    async def _advance(self, state, record):
        config = {"configurable": {"thread_id": state.thread_id}, "recursion_limit": 100}

        @wrap_model_call
        async def model_boundary(request, handler):
            self._remaining(record)
            if record["model_calls"] >= self.max_model_calls:
                raise TaskBudgetExceeded("Model call limit exceeded")
            record["model_calls"] += 1
            self._save(state, record)  # Count attempted requests, including failures and restarts.
            remaining = self._remaining(record)
            snapshot_before = await graph.aget_state(config)
            context = {"thread_id": state.thread_id,
                       "checkpoint_id": (snapshot_before.config or {}).get(
                           "configurable", {}).get("checkpoint_id"),
                       "model_call": record["model_calls"]}
            if self.response_observer:
                self.response_observer(None, context)
            response = await asyncio.wait_for(
                handler(request.override(tools=self.schemas)), min(self.model_timeout_s, remaining)
            )
            self._remaining(record)
            for msg in response.result:
                if not isinstance(msg, AIMessage):
                    continue
                report = require_response(
                    msg.content,
                    [{"id": c["id"], "name": c["name"], "arguments": c["args"]}
                     for c in msg.tool_calls],
                    msg.response_metadata.get("finish_reason"),
                    known_tools=self.protocol.tools, used_ids=record["used_ids"],
                    invalid_calls=msg.invalid_tool_calls,
                    evidence=self.response_observer, context=context,
                )
                record["last_model_response"] = report
            return response

        async with AsyncSqliteSaver.from_conn_string(str(self.path)) as saver:
            graph = create_agent(
                self.model,
                tools=self.native_tools,
                system_prompt=self.prompt,
                checkpointer=saver,
                middleware=[model_boundary],
                interrupt_before=["tools"],
            )
            snapshot = await graph.aget_state(config)
            if record["batch"] and not record["pending"]:
                missing = [c for c in record["batch"] if c["id"] not in record["receipts"]]
                if missing:
                    return self._dispatch(state, record, missing[0])
                # Write journal first; native message IDs make a repeated projection idempotent.
                messages = [
                    GraphToolMessage(
                        content=record["receipts"][c["id"]]["model_content"],
                        tool_call_id=c["id"],
                        id="receipt:" + c["id"],
                        name=c["name"],
                        status="error"
                        if record["receipts"][c["id"]]["official_error"]
                        else "success",
                    )
                    for c in record["batch"]
                ]
                existing = {m.id for m in snapshot.values.get("messages", [])}
                if not all(m.id in existing for m in messages):
                    await graph.aupdate_state(config, {"messages": messages}, as_node="tools")
                record["batch"] = []
                self._save(state, record)
                snapshot = await graph.aget_state(config)
            try:
                inputs = None
                if record.get("input") and record["input"]["id"] not in {
                    m.id for m in snapshot.values.get("messages", [])
                }:
                    history = record.get("history", [])
                    from pydantic import TypeAdapter
                    from tau2.data_model.message import Message

                    inputs = {
                        "messages": [
                            native_message(TypeAdapter(Message).validate_python(m)) for m in history
                        ]
                        + [
                            HumanMessage(
                                content=record["input"]["text"],
                                id=record["input"]["id"],
                            )
                        ]
                    }
                # If the model checkpoint committed before a crash, inspect it before advancing.
                last = (snapshot.values.get("messages") or [None])[-1]
                if not (
                    (snapshot.next and all(n == "tools" for n in snapshot.next))
                    and isinstance(last, AIMessage)
                    and last.tool_calls
                ):
                    if inputs is not None or snapshot.next:
                        remaining = self._remaining(record)
                        await asyncio.wait_for(
                            graph.ainvoke(inputs, config, durability="sync"), remaining
                        )
                    snapshot = await graph.aget_state(config)
                last = (snapshot.values.get("messages") or [None])[-1]
                if (
                    isinstance(last, AIMessage)
                    and last.tool_calls
                    and (snapshot.next and all(n == "tools" for n in snapshot.next))
                ):
                    record["batch"] = [
                        ToolCall(id=c["id"], name=c["name"], arguments=c["args"]).model_dump(
                            mode="json"
                        )
                        for c in last.tool_calls
                    ]
                    record["used_ids"].extend(c["id"] for c in record["batch"])
                    record["status"] = "tools"
                    self._save(state, record)
                    return self._dispatch(state, record, record["batch"][0])
                if snapshot.next or not isinstance(last, AIMessage) or not last.text.strip():
                    raise IncompleteModelResponse("No complete final response")
                reply = AssistantMessage.text(
                    last.text,
                    raw_data={
                        "acs_stop_reason": "answered",
                        "checkpoint": snapshot.config,
                        "model_calls": record["model_calls"],
                        "tool_calls": record["tool_calls"],
                    },
                )
                record["status"] = "idle"
                record.pop("history", None)
                record.pop("input", None)
                # Durable outbound answer: explicit resume can recover a lost final delivery.
                record["final_reply"] = reply.model_dump(mode="json")
                self._save(state, record)
                return reply
            except ModelResponseError as exc:
                record["response_predicate"] = exc.predicate
                return self._stopped(state, record, "adapter_protocol_error")
            except TaskBudgetExceeded:
                return self._stopped(state, record, "budget_exceeded")
            except (TimeoutError, APIError, HTTPError, ConnectionError):
                if record["deadline"] <= time.time():
                    return self._stopped(state, record, "budget_exceeded")
                self._save(state, record)
                return AssistantMessage.text(
                    "Model request interrupted; execution is incomplete.",
                    raw_data={"acs_stop_reason": "llm_error"},
                )
            except Exception:
                self._save(state, record)
                raise

    def _dispatch(self, state, record, call):
        try:
            self._remaining(record)
            if record["tool_calls"] >= self.max_tool_calls:
                raise TaskBudgetExceeded("Tool call limit exceeded")
        except TaskBudgetExceeded:
            return self._stopped(state, record, "budget_exceeded")
        record["pending"], record["tool_calls"] = call["id"], record["tool_calls"] + 1
        self._save(state, record)  # Durable at-most-once dispatch boundary before returning.
        return AssistantMessage.text("", tool_calls=[ToolCall(**call)])

    @classmethod
    def is_stop(cls, message):
        return (message.raw_data or {}).get("acs_stop_reason") in {
            "state_uncertain",
            "adapter_protocol_error",
            "budget_exceeded",
            "llm_error",
        }

    def stop(self, message=None, state=None):
        """End this adapter/model lifetime; resume with a fresh instance after stopping."""
        if self._closed:
            return
        with self._locked():
            try:
                if client := getattr(self.model, "root_async_client", None):
                    if self._runner is None:
                        self._runner = asyncio.Runner()
                    self._runner.run(client.close())
                if client := getattr(self.model, "root_client", None):
                    client.close()
            finally:
                if self._runner is not None:
                    self._runner.close()
                    self._runner = None
                self._closed = True


def create_langgraph_agent(
    tools,
    domain_policy,
    *,
    checkpoint_path,
    model=None,
    settings=None,
    llm=None,
    llm_args=None,
    task=None,
    audio_native_config=None,
    audio_taps_dir=None,
    **budgets,
):
    """Register functools.partial(create_langgraph_agent, checkpoint_path=...) with tau2.

    Explicitly consumes the pinned official factory arguments. Each simultaneous
    simulation needs its own path. A future paid pilot must freeze these arguments.
    """
    if audio_native_config is not None or audio_taps_dir is not None:
        raise ValueError("Only text HalfDuplex is supported")
    if model is None:
        if settings is None:
            from acs.config import get_settings

            settings = get_settings()
        options = dict(llm_args or {})
        allowed = {"api_base", "temperature", "max_tokens", "timeout", "extra_body"}
        if options.keys() - allowed:
            raise ValueError("Unsupported official model options: " + str(options.keys() - allowed))
        if options.get("extra_body", {"thinking": {"type": "disabled"}}) != {
            "thinking": {"type": "disabled"},
        }:
            raise ValueError("Only the frozen non-thinking request contract is supported")
        mapping = {
            "api_base": "llm_base_url",
            "temperature": "llm_temperature",
            "max_tokens": "llm_max_output_tokens",
            "timeout": "llm_timeout_s",
        }
        updates = {mapping[k]: v for k, v in options.items() if k in mapping}
        if llm:
            updates["llm_model"] = llm.removeprefix("openai/")
        settings = settings.model_copy(update=updates)
        model = deepseek_model(settings)
    return LangGraphAgent(
        tools, domain_policy, checkpoint_path=checkpoint_path, model=model, **budgets
    )
