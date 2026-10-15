"""Real local HTTP faults, including a stream that never finishes."""

import json
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.llm import LLMClient
from acs.runtime import BudgetExceeded, RunBudget
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall


@pytest.fixture
def provider():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for _ in range(100):
                        chunk = {"id": "fault", "object": "chat.completion.chunk", "created": 0,
                                 "model": "test", "choices": [{"index": 0, "finish_reason": None,
                                 "delta": {"content": "尚未完成"}}]}
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                        self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                time.sleep(1.5)
                try:
                    self.send_response(503)
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_trickling_stream_stops_with_partial_text_and_no_orphan_messages(settings, provider):
    base, requests = provider
    settings = settings.model_copy(update={
        "llm_api_key": "test-key", "llm_base_url": base, "turn_timeout_s": 0.8,
    })
    updates = list(ChatService(settings).run_turn("测试持续断流", "USER001"))
    final = updates[-1]
    assert final.result.stop_reason == "budget_exceeded"
    assert "尚未完成" in final.result.answer and "本次回答未完成" in final.result.answer
    assert final.elapsed_ms < 1800
    assert len(requests) == 1  # No hidden SDK retry.
    assert final.result.totals["calls"] == 1
    assert final.result.totals["tokens_are_partial"] is True
    stored = ChatService(settings).store.load(final.session_id, "USER001")
    assert not stored["running"]
    assert stored["messages"][-1]["content"] == final.result.answer
    assert not any(t.name == "acs-provider-request" for t in threading.enumerate())


@pytest.mark.parametrize("kind", ["chat", "embedding", "rerank"])
def test_nested_provider_request_uses_same_deadline_and_records_failed_attempt(
    settings, provider, kind,
):
    base, requests = provider
    settings = settings.model_copy(update={
        "llm_api_key": "test-key", "llm_base_url": base,
        "embedding_api_key": "test-key", "embedding_base_url": base,
        "reranker_api_key": "test-key", "reranker_base_url": base,
    })
    client = LLMClient(settings)
    client.budget = RunBudget(0.35, 2)
    started = time.monotonic()
    from openai import APITimeoutError

    with pytest.raises((BudgetExceeded, APITimeoutError)):
        if kind == "chat":
            client.complete("核验", stage="kb_relevance")
        elif kind == "embedding":
            client.embed(["核验"], stage="kb_search")
        else:
            client.rerank("核验", ["正文"])
    assert time.monotonic() - started < 1.2
    assert len(requests) == client.budget.requests == client.totals()["calls"] == 1
    assert client.totals()["tokens_are_partial"] is True


def test_tool_batch_limit_completes_pairs_without_executing_excess_tools(settings):
    settings = settings.model_copy(update={"max_tool_calls": 1})
    llm = ScriptedLLM([ScriptedResponse(wants_tools=True, tool_calls=[
        ScriptedToolCall("first", "get_order", '{"order_id":"O10024"}'),
        ScriptedToolCall("denied", "update_return_task", '{"quantity":1}'),
    ])])
    agent = CustomerServiceAgent(settings=settings, llm=llm, registry=ToolRegistry(settings))
    service = ChatService(settings)
    final = list(service.run_turn("查单", "USER001", agent=agent))[-1]
    assert final.result.stop_reason == "budget_exceeded" and final.result.tool_calls == 1
    stored = service.store.load(final.session_id, "USER001")["messages"]
    denied = next(m for m in stored if m.get("tool_call_id") == "denied")
    assert json.loads(denied["content"])["executed"] is False
    assert service.store.get_return_task(final.session_id, "USER001")["task"] is None


def test_api_request_limit_counts_attempts_and_rejects_before_a_new_request():
    budget = RunBudget(5, 1)
    budget.request()
    with pytest.raises(BudgetExceeded, match="请求数"):
        budget.request()
    assert budget.requests == 1


def test_nested_model_input_limit_rejects_before_spending_a_request(settings):
    from acs.context import ContextLimitExceeded

    client = LLMClient(settings.model_copy(update={
        "llm_api_key": "test-key", "context_max_chars": 100,
    }))
    client.budget = RunBudget(5, 2)
    with pytest.raises(ContextLimitExceeded):
        client.complete("过长的待核验正文" * 100, stage="kb_relevance")
    assert client.budget.requests == 0 and client.totals()["calls"] == 0


def test_closing_stream_cancels_network_instead_of_running_in_background(settings, provider):
    base, _ = provider
    client = LLMClient(settings.model_copy(update={
        "llm_api_key": "test-key", "llm_base_url": base,
    }))
    client.budget = RunBudget(10, 2)
    with closing(client.chat_stream([{"role": "user", "content": "测试"}])) as stream:
        assert next(stream) == "尚未完成"
    assert client.totals()["calls"] == 1
    assert not any(t.name == "acs-provider-request" for t in threading.enumerate())
