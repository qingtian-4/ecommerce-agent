"""τ half-duplex bridge to the actual ACS loop; the official environment executes all tools.

The bridge pauses at the loop's published tool-call event, returns it to the
orchestrator, then feeds the official ToolMessage back into registry.run(). It
does not invoke environment functions itself or substitute local business data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tau2.agent.base_agent import HalfDuplexAgent
from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage

from acs.config import get_settings
from acs.cs_tools import TraceEvent
from acs.runtime import RunBudget
from acs.service_agent import CustomerServiceAgent


class OfficialRegistry:
    def __init__(self, tools, *, receipt_guard=False):
        self.tools = tools
        self.reply = None
        self.protocol = None
        self.protocol_record = None
        if receipt_guard:
            from tau_tool_protocol import OfficialToolProtocol

            self.protocol = OfficialToolProtocol(tools)

    def schemas(self):
        return [t.openai_schema for t in self.tools]

    def run(self, name, arguments, *, current_user_id):
        if self.reply is None:
            raise RuntimeError("Official tool result was not supplied")
        reply, self.reply = self.reply, None
        if self.protocol:
            self.protocol_record = self.protocol.inspect(
                ToolCall(
                    id=reply.id,
                    name=name,
                    arguments=json.loads(arguments),
                ),
                reply,
            )
        try:
            payload = json.loads(reply.content or "null")
        except json.JSONDecodeError:
            payload = reply.content
        return (
            payload,
            [
                TraceEvent("tool_call", name, {"arguments": arguments}),
                TraceEvent("tool_result", name, {"result": payload}),
            ],
            (f"official_tool_error:{name}" if reply.error else None),
        )


@dataclass
class ACSState:
    agent: Any
    stream: Any = None
    seen_calls: int = 0
    trace_cursor: int = 0
    pending_id: str | None = None


class ACSAgent(HalfDuplexAgent[ACSState]):
    def __init__(
        self, tools, domain_policy, *, settings=None, llm_factory=None, receipt_guard=False
    ):
        super().__init__(tools, domain_policy)
        self.settings, self.llm_factory, self.receipt_guard = settings, llm_factory, receipt_guard

    def get_init_state(self, message_history=None):
        settings = (self.settings or get_settings()).model_copy(update={"context_max_chars": 64000})
        agent = CustomerServiceAgent(
            settings=settings,
            registry=OfficialRegistry(self.tools, receipt_guard=self.receipt_guard),
            llm=self.llm_factory(settings) if self.llm_factory else None,
        )
        agent.messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    agent_instruction=AGENT_INSTRUCTION,
                    domain_policy=self.domain_policy,
                ),
            }
        ]
        for message in message_history or []:
            if isinstance(message, ToolMessage):
                agent.messages.append(
                    {"role": "tool", "tool_call_id": message.id, "content": message.content}
                )
            else:
                row = {"role": message.role, "content": message.content}
                if message.tool_calls:
                    row["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in message.tool_calls
                    ]
                agent.messages.append(row)
        return ACSState(agent)

    def generate_next_message(self, message, state):
        if isinstance(message, UserMessage):
            if state.stream is not None:
                raise RuntimeError("New user message arrived before the previous turn finished")
            settings = state.agent.settings
            state.stream = state.agent.chat_stream(
                message.content or "",
                budget=RunBudget(settings.turn_timeout_s, settings.max_api_requests),
            )
            state.seen_calls = state.trace_cursor = 0
        elif isinstance(message, ToolMessage):
            if message.id != state.pending_id:
                raise ValueError("Official tool result id does not match pending call")
            state.agent.registry.reply = message
            state.pending_id = None
        else:
            raise TypeError("Adapter emits serial tool calls; expected a single ToolMessage")
        for result in state.stream:
            record = state.agent.registry.protocol_record
            if record and (record["uncertain_write"] or record["protocol_error"]):
                state.stream.close()
                state.stream = None
                reason = (
                    "state_uncertain" if record["uncertain_write"] else "adapter_protocol_error"
                )
                return AssistantMessage.text(
                    "Official tool outcome is unverified. No further operations were attempted.",
                    raw_data={
                        "acs_stop_reason": reason,
                        "protocol_guard": record,
                        "acs_receipt_guard": True,
                    },
                ), state
            for event in result.trace[state.trace_cursor :]:
                state.trace_cursor += 1
                if event.type != "tool_call":
                    continue
                # The current batch is already on the real loop's message history.
                calls = next(
                    m["tool_calls"] for m in reversed(state.agent.messages) if m.get("tool_calls")
                )
                completed = sum(
                    m["role"] == "tool"
                    for m in reversed(state.agent.messages)
                    if m.get("tool_call_id") in {c["id"] for c in calls}
                )
                call = calls[completed]
                state.pending_id = call["id"]
                return AssistantMessage.text(
                    "",
                    tool_calls=[
                        ToolCall(
                            id=call["id"],
                            name=call["function"]["name"],
                            arguments=json.loads(call["function"]["arguments"]),
                        )
                    ],
                ), state
            if result.stop_reason != "running":
                # Exhaust the generator so its budget cleanup is not delayed to GC.
                state.stream.close()
                state.stream = None
                return AssistantMessage.text(
                    result.answer,
                    usage={
                        "prompt_tokens": result.totals.get("prompt_tokens", 0),
                        "completion_tokens": result.totals.get("completion_tokens", 0),
                    },
                    raw_data={
                        "acs_stop_reason": result.stop_reason,
                        "acs_receipt_guard": self.receipt_guard,
                        "acs_totals": result.totals,
                        "acs_trace": [e.as_dict() for e in result.trace],
                    },
                ), state
        raise RuntimeError("ACS loop ended without a final result")

    @classmethod
    def is_stop(cls, message):
        return bool((message.raw_data or {}).get("acs_receipt_guard")) and (
            (message.raw_data or {}).get("acs_stop_reason")
            in {
                "state_uncertain",
                "adapter_protocol_error",
                "budget_exceeded",
                "max_steps",
                "llm_error",
            }
        )

    def stop(self, message=None, state=None):
        if state is not None and state.stream is not None:
            state.stream.close()
            state.stream = None


def create_acs_agent(tools, domain_policy, **kwargs):
    return ACSAgent(tools=tools, domain_policy=domain_policy)
