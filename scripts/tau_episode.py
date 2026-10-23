"""Comparable outer budget/usage boundary; official environment remains executor."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import httpx
from openai import AsyncOpenAI, OpenAI
from tau2.agent.base_agent import HalfDuplexAgent
from tau2.data_model.message import AssistantMessage, ToolMessage
from tau_acs_agent import ACSAgent
from tau_langgraph_agent import LangGraphAgent

from acs.episode_transport import EpisodeAsyncTransport, EpisodeCallback, EpisodeSyncTransport
from acs.langgraph_poc import deepseek_model
from acs.llm import LLMClient
from acs.runtime import BudgetExceeded


class EpisodeAgent(HalfDuplexAgent):
    """Both arms share this boundary. No additional model/tool loop."""

    def __init__(self, inner, ledger, episode):
        super().__init__(inner.tools, inner.domain_policy)
        self.inner, self.ledger, self.episode = inner, ledger, episode

    def set_seed(self, seed):
        self.inner.set_seed(seed)

    def get_init_state(self, message_history=None):
        # Complete transcripts can reconnect with the SAME journal/deadline. Pending
        # official business operations require external reconciliation, never replay.
        rows = self.ledger.rows(self.episode)
        receipts = {r["receipt"]["id"] for r in rows if r["type"] == "tool_receipt"}
        if any(r["type"] == "tool_intent" and r["call"]["id"] not in receipts for r in rows):
            raise BudgetExceeded("state_uncertain")
        self.ledger.remaining(self.episode)
        return self.inner.get_init_state(message_history)

    def generate_next_message(self, message, state):
        try:
            if isinstance(message, ToolMessage):
                self.ledger.tool_receipt(self.episode, message.model_dump(mode="json"))
            self.ledger.remaining(self.episode)
            reply, state = self.inner.generate_next_message(message, state)
            if self.inner.is_stop(reply):
                self.ledger.stop(self.episode, (reply.raw_data or {}).get(
                    "acs_stop_reason", "adapter_protocol_error"))
            # Check after I/O as well; a response arriving at the deadline is unfinished.
            self.ledger.remaining(self.episode)
            for call in reply.tool_calls or []:
                self.ledger.tool_intent(self.episode, call.model_dump(mode="json"))
        except BudgetExceeded as exc:
            reply = AssistantMessage.text(
                "Episode execution stopped; no further operations attempted.",
                raw_data={"episode_stop_reason": str(exc)},
            )
        except (ValueError, TypeError):
            self.ledger.stop(self.episode, "adapter_protocol_error")
            reply = AssistantMessage.text("Protocol validation failed; execution stopped.",
                raw_data={"episode_stop_reason": "adapter_protocol_error"})
        stopped = self.ledger.summary(self.episode)["stop_reasons"]
        if stopped:
            reply = AssistantMessage.text(
                "Episode execution stopped; no further operations attempted.",
                raw_data={"episode_stop_reason": stopped[0]},
            )
        self._export_usage(reply)
        return reply, state

    def _export_usage(self, reply):
        # Export request deltas ONCE, including native graph tool-intent messages.
        # AssistantMessage.cost is USD as in upstream, CNY ledger is authoritative cap.
        delivery_id = hashlib.sha256(json.dumps(reply.model_dump(mode="json", exclude={
            "usage", "cost", "turn_idx"}), sort_keys=True).encode()).hexdigest()
        with self.ledger.journal.transaction() as (rows, append):
            existing = next((r for r in rows if r["type"] == "usage_export"
                             and r["episode"] == self.episode
                             and r["delivery_id"] == delivery_id), None)
            if existing:
                reply.usage, reply.cost = existing["usage"], existing["cost"]
                return
            exported = {rid for r in rows if r["type"] == "usage_export"
                        and r["episode"] == self.episode for rid in r["request_ids"]}
            ends = [r for r in rows if r["type"] == "request_end"
                    and r["episode"] == self.episode and r["role"] == "agent"
                    and r["request_id"] not in exported]
            reply.usage = {k: sum((r["usage"] or {}).get(k, 0) for r in ends)
                           for k in ("prompt_tokens", "completion_tokens")}
            reply.cost = (float(sum((Decimal(r["estimated_usd"]) for r in ends), Decimal(0)))
                          if all(r["estimated_usd"] is not None for r in ends) else None)
            append({"type": "usage_export", "episode": self.episode,
                    "delivery_id": delivery_id, "usage": reply.usage, "cost": reply.cost,
                    "request_ids": [r["request_id"] for r in ends]})

    def resume(self, state):
        self.ledger.remaining(self.episode)
        reply, state = self.inner.resume(state)
        if self.inner.is_stop(reply):
            self.ledger.stop(self.episode, reply.raw_data["acs_stop_reason"])
        self._export_usage(reply)
        return reply, state

    @classmethod
    def is_stop(cls, message):
        raw = message.raw_data or {}
        return (bool(raw.get("episode_stop_reason")) or ACSAgent.is_stop(message)
                or LangGraphAgent.is_stop(message))

    def stop(self, message=None, state=None):
        self.inner.stop(message, state)
        if isinstance(self.inner, ACSAgent) and state is not None:
            state.agent.llm._client.close()


def sync_client(settings, ledger, episode, role, *, inner_factory=None):
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url,
                  max_retries=0, timeout=settings.llm_timeout_s,
                  http_client=httpx.Client(transport=EpisodeSyncTransport(
                      ledger, episode, role, inner_factory)))


def make_agent(arm, environment, settings, ledger, episode, checkpoint, *, transport_factory=None):
    tools, policy = environment.get_tools(), environment.get_policy()
    limits = ledger._state(ledger.rows(), episode)["limits"]
    # Both inner protections exceed the ENTIRE outer experiment; they cannot stop
    # a normal task first. Context truncation is likewise excluded from this pilot.
    settings = settings.model_copy(update={
        "max_steps": 49, "max_api_requests": 49, "max_tool_calls": 49,
        "turn_timeout_s": limits["episode_timeout_s"] + 1000,
        "context_max_chars": 4_000_000,
    })

    def http_async():
        return httpx.AsyncClient(transport=EpisodeAsyncTransport(
            ledger, episode, "agent", transport_factory() if transport_factory else None))

    if arm == "acs_loop_receipt_guard":
        def llm_factory(config):
            def client_factory(**kwargs):
                return AsyncOpenAI(**kwargs, http_client=http_async())
            return LLMClient(config, async_client_factory=client_factory)

        inner = ACSAgent(tools, policy, settings=settings, llm_factory=llm_factory,
                         receipt_guard=True)
    elif arm == "langgraph":
        model = deepseek_model(settings, callbacks=[EpisodeCallback(ledger, episode)],
                               http_async_client=http_async(),
                               http_client=httpx.Client(transport=EpisodeSyncTransport(
                                   ledger, episode, "agent", transport_factory)))
        inner = LangGraphAgent(tools, policy, checkpoint_path=checkpoint, model=model,
                              max_model_calls=49, max_tool_calls=49,
                              model_timeout_s=limits["model_timeout_s"] + 1,
                              task_timeout_s=limits["episode_timeout_s"] + 1000)
    else:
        raise ValueError("Unknown comparison arm")
    return EpisodeAgent(inner, ledger, episode)
