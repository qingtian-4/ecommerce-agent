"""Budget the actual SDK HTTP boundary, including failed and streaming attempts.

HalfDuplex remains the default. Web opt-in forwards SSE frames before completion,
but withholds [DONE] until the full response contract and usage are durable.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import closing, suppress
from uuid import uuid4

import httpx
from langchain_core.callbacks import BaseCallbackHandler

from acs.async_transport import iterate_request
from acs.model_response_contract import ModelResponseError, inspect_wire
from acs.runtime import BudgetExceeded


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
    def __init__(self, ledger, episode, role, inner=None, *, max_body_bytes=None):
        self.ledger, self.episode, self.role = ledger, episode, role
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.max_body_bytes = max_body_bytes

    async def handle_async_request(self, request):
        return await self._handle(request)

    async def _handle(self, request, publish=None):
        body = await request.aread()
        if self.max_body_bytes is not None and len(body) > self.max_body_bytes:
            self.ledger.event(self.episode, "preflight_rejected", reason="body_limit",
                              body_bytes=len(body), limit=self.max_body_bytes)
            raise BudgetExceeded("request_body_limit")
        reservation = self.ledger.reserve(self.episode, self.role, body)
        started = time.perf_counter()
        partial = bytearray()
        pending = bytearray()
        terminal = False

        def relay(chunk):
            nonlocal terminal
            pending.extend(chunk)
            while not terminal:
                boundary = re.search(rb"\r?\n\r?\n", pending)
                if boundary is None:
                    break
                frame = bytes(pending[:boundary.end()])
                # Match the SDK's termination rule, including split network chunks.
                terminal = any(
                    line.startswith(b"data:") and line[5:].lstrip().startswith(b"[DONE]")
                    for line in frame.splitlines()
                )
                if not terminal:
                    del pending[:boundary.end()]
                    publish(("value", ("chunk", frame)))

        async def receive():
            response = await self.inner.handle_async_request(request)
            try:
                if publish:
                    publish(("value", ("headers", response.status_code, response.headers)))
                async for chunk in response.aiter_bytes():
                    partial.extend(chunk)
                    if publish:
                        relay(chunk)
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
        if publish:
            if pending:
                publish(("value", ("chunk", bytes(pending))))
            return None
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

    def __init__(self, ledger, episode, role, inner_factory=None, *, streaming=False,
                 max_body_bytes=None):
        self.ledger, self.episode, self.role = ledger, episode, role
        self.inner_factory = inner_factory
        self.streaming = streaming
        self.max_body_bytes = max_body_bytes

    def handle_request(self, request):
        request.read()
        live = self.streaming and bool(json.loads(request.content).get("stream"))

        async def receive(publish):
            inner = self.inner_factory() if self.inner_factory else None
            transport = EpisodeAsyncTransport(self.ledger, self.episode, self.role, inner,
                                               max_body_bytes=self.max_body_bytes)
            try:
                if live:
                    await transport._handle(request, publish)
                else:
                    response = await transport.handle_async_request(request)
                    publish(("value", response))
            finally:
                await transport.aclose()

        # Actual transport enforces exact deadline; bridge allowance only permits cleanup.
        events = iterate_request(receive, self.ledger.remaining(self.episode) + 2)
        if not live:
            with closing(events):
                return next(events)
        try:
            kind, status, headers = next(events)
            if kind != "headers":
                raise RuntimeError("Streaming transport did not receive headers")
            headers = [(k, v) for k, v in headers.multi_items()
                       if k.lower() not in {
                           "content-encoding", "content-length", "transfer-encoding"}]
            return httpx.Response(status, headers=headers, request=request,
                                  stream=_EpisodeBytes(events))
        except BaseException:
            events.close()
            raise


class _EpisodeBytes(httpx.SyncByteStream):
    def __init__(self, events):
        self.events = events

    def __iter__(self):
        for kind, chunk in self.events:
            if kind != "chunk":
                raise RuntimeError("Invalid streaming transport event")
            yield chunk

    def close(self):
        self.events.close()


class EpisodeCallback(BaseCallbackHandler):
    """Audit the real LangChain callback path; HTTP transport alone reserves money."""

    raise_error = True
    run_inline = True

    def __init__(self, ledger, episode):
        self.ledger, self.episode = ledger, episode
        self.streamed = set()

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        if token and run_id not in self.streamed:
            self.streamed.add(run_id)
            self.ledger.event(self.episode, "sdk_first_token", run_id=str(run_id))

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
