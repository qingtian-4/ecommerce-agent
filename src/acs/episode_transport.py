"""Budget the actual SDK HTTP boundary, including failed and streaming attempts.

HalfDuplex only: buffer a response to obtain its final usage before exposing it.
This doesn't change JSON/SSE contents. UI streaming is not routed through here.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import closing, suppress

import httpx
from langchain_core.callbacks import BaseCallbackHandler

from acs.async_transport import iterate_request


def response_usage(content):
    try:
        return json.loads(content).get("usage")
    except (ValueError, AttributeError):
        usage = None
        for line in content.splitlines():
            if line.startswith(b"data: ") and line[6:] != b"[DONE]":
                with suppress(ValueError):
                    usage = json.loads(line[6:]).get("usage") or usage
        return usage


class EpisodeAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, episode, role, inner=None):
        self.ledger, self.episode, self.role = ledger, episode, role
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        reservation = self.ledger.reserve(self.episode, self.role, await request.aread())
        started = time.perf_counter()

        async def receive():
            response = await self.inner.handle_async_request(request)
            try:
                content = await response.aread()
                return response, content
            finally:
                await response.aclose()

        try:
            response, content = await asyncio.wait_for(
                receive(), self.ledger.remaining(self.episode))
        except BaseException as exc:
            self.ledger.finish(reservation, status=type(exc).__name__,
                               latency_ms=(time.perf_counter()-started)*1000)
            raise
        self.ledger.finish(reservation, status="completed" if response.is_success else
                           f"http_{response.status_code}", usage=response_usage(content),
                           latency_ms=(time.perf_counter()-started)*1000)
        headers = [(k, v) for k, v in response.headers.multi_items()
                   if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
        return httpx.Response(response.status_code, headers=headers, content=content,
                              request=request)

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

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.ledger.event(self.episode, "sdk_error", run_id=str(run_id), error=type(error).__name__)
