"""Budget the actual SDK HTTP boundary, including failed and streaming attempts.

HalfDuplex only: buffer a response to obtain its final usage before exposing it.
This doesn't change JSON/SSE contents. UI streaming is not routed through here.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import closing, suppress
from uuid import uuid4

import httpx
from langchain_core.callbacks import BaseCallbackHandler

from acs.async_transport import iterate_request
from acs.model_response_contract import ModelResponseError, inspect_wire


def response_usage(content):
    try:
        usage = json.loads(content).get("usage")
        return usage if isinstance(usage, dict) else None
    except (ValueError, AttributeError):
        usage = None
        for line in content.splitlines():
            if line.startswith(b"data: ") and line[6:] != b"[DONE]":
                with suppress(ValueError, AttributeError, TypeError):
                    candidate = json.loads(line[6:]).get("usage")
                    usage = candidate if isinstance(candidate, dict) else usage
        return usage


class EpisodeAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, episode, role, inner=None):
        self.ledger, self.episode, self.role = ledger, episode, role
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        body = await request.aread()
        reservation = self.ledger.reserve(self.episode, self.role, body)
        started = time.perf_counter()
        partial = bytearray()

        async def receive():
            response = await self.inner.handle_async_request(request)
            try:
                async for chunk in response.aiter_bytes():
                    partial.extend(chunk)
                return response, bytes(partial)
            finally:
                await response.aclose()

        try:
            timeout = self.ledger.remaining(self.episode)
            response, content = await asyncio.wait_for(receive(), timeout)
        except BaseException as exc:
            self._evidence(reservation, bytes(partial), started,
                           error_type=type(exc).__name__)
            self.ledger.finish(reservation, status=type(exc).__name__,
                               latency_ms=(time.perf_counter()-started)*1000)
            raise
        # Capture provider output before SDK parsing, usage errors, or response guards.
        self._evidence(reservation, content, started, response=response)
        report = None
        if response.is_success and self.role == "agent":
            try:
                report = inspect_wire(content, json.loads(body))
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                report = {"predicate": "sdk_response_parse_error", "finish_reason": None,
                          "layer": "wire_parse", "error_type": type(exc).__name__}
            if report["predicate"] and "error_type" not in report:
                report.update(layer="model_output", error_type="ModelResponseError")
            self.ledger.event(self.episode, "response_contract", request_id=reservation[
                "request_id"], role=self.role, **report)
        self.ledger.finish(reservation, status="completed" if response.is_success else
                           f"http_{response.status_code}", usage=response_usage(content),
                           latency_ms=(time.perf_counter()-started)*1000)
        if report and report["predicate"]:
            self.ledger.stop(self.episode, "adapter_protocol_error")
            raise ModelResponseError(report["predicate"],
                                     layer=report.get("layer", "model_output"))
        headers = [(k, v) for k, v in response.headers.multi_items()
                   if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
        return httpx.Response(response.status_code, headers=headers, content=content,
                              request=request)

    def _evidence(self, reservation, content, started, *, response=None, error_type=None):
        if evidence := getattr(self.ledger, "evidence", None):
            context = next((r for r in reversed(self.ledger.rows(self.episode))
                            if r["type"] == "graph_context"), None)
            fingerprint = evidence.save(reservation["request_id"], {
                "reservation": reservation, "role": self.role, "graph": context,
                "provider_request_id": (response.headers.get("x-request-id")
                    or response.headers.get("x-ds-request-id")) if response else None,
                "status_code": response.status_code if response else None,
                "raw_response_body": content.decode("utf-8", errors="replace"),
                "error_type": error_type, "latency_ms": (time.perf_counter()-started)*1000,
                "usage": response_usage(content),
            })
            self.ledger.event(self.episode, "response_evidence", request_id=reservation[
                "request_id"], role=self.role, evidence_sha256=fingerprint,
                error_type=error_type)

    async def aclose(self):
        await self.inner.aclose()


class EpisodeSyncTransport(httpx.BaseTransport):
    """Official LiteLLM/OpenAI sync SDK; fresh async I/O lifecycle per request."""

    def __init__(self, ledger, episode, role, inner_factory=None):
        self.ledger, self.episode, self.role = ledger, episode, role
        self.inner_factory = inner_factory

    def handle_request(self, request):
        request.read()

        async def receive(publish):
            inner = self.inner_factory() if self.inner_factory else None
            transport = EpisodeAsyncTransport(self.ledger, self.episode, self.role, inner)
            try:
                response = await transport.handle_async_request(request)
                publish(("value", response))
            finally:
                await transport.aclose()

        # Actual transport enforces exact deadline; bridge allowance only permits cleanup.
        with closing(iterate_request(receive, self.ledger.remaining(self.episode) + 2)) as events:
            return next(events)


class EpisodeCallback(BaseCallbackHandler):
    """Audit the real LangChain callback path; HTTP transport alone reserves money."""

    raise_error = True
    run_inline = True

    def __init__(self, ledger, episode):
        self.ledger, self.episode = ledger, episode

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.ledger.remaining(self.episode)
        self.ledger.event(self.episode, "sdk_start", run_id=str(run_id))

    def on_llm_end(self, response, *, run_id, **kwargs):
        usages = [getattr(g.message, "usage_metadata", None)
                  for batch in response.generations for g in batch]
        self.ledger.event(self.episode, "sdk_end", run_id=str(run_id), usages=usages)
        if evidence := getattr(self.ledger, "evidence", None):
            latest = next((r for r in reversed(self.ledger.rows(self.episode))
                           if r["type"] == "request_end" and r["role"] == "agent"), {})
            evidence.save(uuid4().hex, {"sdk_run_id": str(run_id),
                "request_id": latest.get("request_id"), "sdk_result": response.model_dump()})

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.ledger.event(self.episode, "sdk_error", run_id=str(run_id),
                          error_type=type(error).__name__)
