"""Real SDK -> native Graph -> persisted public events, with upstream held open."""

import asyncio
import json
import socket
import time
from contextlib import contextmanager
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from acs.config import Settings
from acs.episode_budget import EpisodeLedger, EpisodeLimits
from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport
from acs.langgraph_poc import deepseek_model
from acs.local_submission import init_formal_fixture
from acs.response_evidence import ResponseEvidence
from acs.web_service import create_app

from .test_episode_streaming import USAGE, frame
from .test_web_service import ORIGIN, browser, counts, done, events, post


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Offline streaming contracts cannot connect to a model")
    monkeypatch.setattr(socket.socket, "connect", reject)


@pytest.mark.parametrize("invalid", [False, True])
def test_native_draft_arrives_before_model_end_and_is_withdrawn_on_failure(tmp_path, invalid):
    root = tmp_path / "stream"
    init_formal_fixture(root)
    ledger = EpisodeLedger(root / "fees.jsonl", cap_cny=3, max_requests=2)
    ledger.begin("web", EpisodeLimits(requests=2, model_timeout_s=15, episode_timeout_s=60))
    ledger.evidence = ResponseEvidence(root / ".private-evidence", secrets=("hidden-value",))
    release = Event()
    text = "商品规格正在依据工具核对，这是一段生成中的答复，尚未核验业务结果。" * 8

    class Live(httpx.AsyncByteStream):
        async def __aiter__(self):
            # Reasoning must never cross the public event boundary.
            reasoning = json.loads(frame("" )[6:])
            reasoning["choices"][0]["delta"] = {"reasoning_content": "private-reasoning"}
            yield b"data: " + json.dumps(reasoning).encode() + b"\n\n"
            yield frame(text + " Bearer hidden-")
            assert await asyncio.to_thread(release.wait, 10), "no public live draft"
            yield frame("value alice@example.test 13912345678。结束。",
                        finish="length" if invalid else "stop")
            yield frame(usage=USAGE) + b"data: [DONE]\n\n"

    @contextmanager
    def factory():
        with httpx.Client(transport=EpisodeSyncTransport(
            ledger, "web", "agent", lambda: httpx.MockTransport(lambda r: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Live()
            )), streaming=True,
        )) as client:
            yield deepseek_model(
                Settings(_env_file=None, llm_api_key="offline-test", llm_model="deepseek-flash",
                         llm_thinking=False, llm_max_output_tokens=1024),
                http_client=client, callbacks=[EpisodeCallback(ledger, "web")],
            ).model_copy(update={"streaming": True})

    with TestClient(create_app(root, factory, answer_streaming=True,
                              secret_values=("hidden-value",)), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        try:
            assert post(client, sid, headers, "messages", text="查询商品").status_code == 202
            deadline = time.monotonic() + 12
            while True:
                published = events(client, sid)
                drafts = [e for e in published if e["type"] == "answer_delta"]
                if drafts:
                    break
                assert time.monotonic() < deadline, "no draft until provider completes"
                time.sleep(.02)
            assert not any(r["type"] == "request_end" for r in ledger.rows())
            assert not any(e["type"] == "turn_completed" for e in published)
            assert drafts[0]["data"]["status"] == "provisional"
        finally:
            release.set()
        state = done(client, sid)
        published = events(client, sid)
        public = json.dumps(published, ensure_ascii=False)
        assert "private-reasoning" not in public and "hidden-" not in public
        assert "alice@example.test" not in public and "13912345678" not in public
        assert any(e["type"] == "answer_delta" for e in published)
        if invalid:
            assert state["jobs"][-1]["status"] == "failed"
            assert any(e["type"] == "answer_discarded" for e in published)
            assert not any(e["type"] == "answer_completed" for e in published)
        else:
            completed = [e for e in published if e["type"] == "answer_completed"]
            assert completed[-1]["data"]["text"] == state["history"][-1]["content"]
            assert state["jobs"][-1]["status"] == "completed"
        assert counts(root) == (0, 0, 0)
        # Replaying SSE is read-only and does not create another SDK request.
        before = ledger.summary()["requests"]
        assert events(client, sid) == published
        assert ledger.summary()["requests"] == before == 1

@pytest.mark.parametrize('unknown', [False, True])
def test_streamed_mixed_batch_only_executes_after_complete_validation(tmp_path, unknown):
    root = tmp_path / 'batch'
    init_formal_fixture(root)
    ledger = EpisodeLedger(root / 'fees.jsonl', cap_cny=3, max_requests=3)
    ledger.begin('web', EpisodeLimits(requests=3, model_timeout_s=15, episode_timeout_s=60))
    sent = []
    prefix = '工具前的中间说明，不是最终答复。' * 16

    def response(request):
        body = json.loads(request.content)
        sent.append(body)
        batch = len(sent) == 1
        calls = [{'index': i, 'id': f'call-{i}', 'type': 'function',
                  'function': {'name': name, 'arguments': json.dumps(args)}}
                 for i, (name, args) in enumerate([
                     ('get_product', {'product_id': 'P003'}),
                     ('unknown_tool' if unknown else 'list_orders', {}),
                 ])]

        class Batch(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield frame(prefix if batch else '商品和订单查询完成，请核对真实工具回执。')
                if batch:
                    data = json.loads(frame('')[6:])
                    data['choices'][0]['delta'] = {'tool_calls': calls}
                    data['choices'][0]['finish_reason'] = 'tool_calls'
                    yield b'data: ' + json.dumps(data).encode() + b'\n\n'
                else:
                    yield frame('', finish='stop')
                yield frame(usage=USAGE) + b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Batch())

    @contextmanager
    def factory():
        with httpx.Client(transport=EpisodeSyncTransport(ledger, 'web', 'agent',
            lambda: httpx.MockTransport(response), streaming=True)) as client:
            yield deepseek_model(Settings(_env_file=None, llm_api_key='offline-test',
                llm_model='deepseek-flash', llm_thinking=False, llm_max_output_tokens=1024),
                http_client=client).model_copy(update={'streaming': True})

    with TestClient(create_app(root, factory, answer_streaming=True), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, 'messages', text='查询商品和订单')
        state = done(client, sid)
        log = events(client, sid)
        assert any(e['type']=='answer_discarded' for e in log)
        completed = [e for e in log if e['type']=='tool_completed']
        if unknown:
            assert not completed and len(sent)==1
            assert state['jobs'][-1]['status']=='failed'
            assert not any(e['type']=='answer_completed' for e in log)
        else:
            assert [e['data']['call_id'] for e in completed]==['call-0','call-1']
            receipts = [m for m in sent[1]['messages'] if m['role']=='tool']
            assert [m['tool_call_id'] for m in receipts]==['call-0','call-1']
            assert state['jobs'][-1]['status']=='completed' and len(sent)==2
            assert prefix not in state['history'][-1]['content']
        assert counts(root)==(0,0,0)

@pytest.mark.parametrize('value', ['Bearer hidden-value', 'alice@example.test', '13912345678',
                                 '客户合成姓名', 'api_key=hidden-value'])
def test_every_sensitive_split_is_redacted_before_publication(value):
    from acs.web_service import PublicAnswer
    for offset in range(len(value)+1):
        answer = PublicAnswer('one', ('hidden-value', '客户合成姓名'))
        parts = ['安全句子。'*30+value[:offset], value[offset:]+' 尾部文字。'*30]
        exposed = [answer.add(part) or '' for part in parts]
        assert all(value not in text and 'hidden-' not in text for text in exposed)
        assert answer.visible
