"""Actual OpenAI SDK SSE, gated bytes and durable budget; no model network."""

import asyncio
import json
from threading import Event

import httpx
import pytest
from openai import APIConnectionError, OpenAI

from acs.episode_budget import EpisodeLedger, EpisodeLimits
from acs.episode_transport import EpisodeSyncTransport
from acs.model_response_contract import ModelResponseError
from acs.runtime import BudgetExceeded


def frame(content=None, *, finish=None, usage=None):
    data = {"id": "stream-1", "object": "chat.completion.chunk", "created": 1,
            "model": "deepseek-flash", "choices": []}
    if content is not None or finish:
        data["choices"] = [{"index": 0, "delta": {"content": content or ""},
                            "finish_reason": finish}]
    if usage:
        data["usage"] = usage
    return b"data: " + json.dumps(data).encode() + b"\n\n"


USAGE = {"prompt_tokens": 100, "completion_tokens": 20}


def client_for(tmp_path, body_factory, *, limit=2, timeout=5):
    ledger = EpisodeLedger(tmp_path / "fees.jsonl", cap_cny=3, max_requests=limit)
    ledger.begin("web", EpisodeLimits(requests=limit, model_timeout_s=timeout,
                                      episode_timeout_s=30))
    sent = []

    def respond(request):
        assert ledger.summary()["requests"] == len(sent) + 1
        sent.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=body_factory())

    http = httpx.Client(transport=EpisodeSyncTransport(
        ledger, "web", "agent", lambda: httpx.MockTransport(respond), streaming=True,
    ))
    return OpenAI(api_key="offline-test", http_client=http, max_retries=0), ledger, sent


def request(client):
    return client.chat.completions.create(model="deepseek-flash", max_tokens=1024,
        messages=[{"role": "user", "content": "fixture"}], stream=True,
        stream_options={"include_usage": True})


def test_sdk_observes_first_bytes_before_upstream_can_complete(tmp_path):
    release = Event()

    class Gated(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame("first")
            assert await asyncio.to_thread(release.wait, 3), "SDK never saw live bytes"
            yield frame(" second", finish="stop") + frame(usage=USAGE) + b"data: [DONE]\n\n"

    client, ledger, sent = client_for(tmp_path, Gated, limit=1)
    with client, request(client) as stream:
        first = next(stream)
        assert first.choices[0].delta.content == "first"
        assert not any(r["type"] == "request_end" for r in ledger.rows())
        release.set()
        assert " second" in "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
    assert ledger.rows()[-1]["type"] == "request_end"
    assert ledger.rows()[-1]["usage"] == USAGE
    # A restarted process cannot obtain another request by reopening its ledger.

    again, reopened, more = client_for(tmp_path, Gated, limit=1)
    with again, pytest.raises((BudgetExceeded, APIConnectionError)):
        list(request(again))
    assert len(sent) == 1 and more == [] and reopened.summary()["requests"] == 1


@pytest.mark.parametrize("tail,predicate", [
    (frame("bad", finish="length") + frame(usage=USAGE) + b"data: [DONE]\n\n",
     "invalid_finish_reason"),
    (frame("cut", finish="stop") + frame(usage=USAGE), "incomplete_stream"),
])
def test_invalid_stream_cannot_deliver_a_valid_done(tmp_path, tail, predicate):
    class Invalid(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame("partial")
            yield tail

    client, ledger, sent = client_for(tmp_path, Invalid)
    with client, pytest.raises(ModelResponseError, match=predicate):
        list(request(client))
    assert len(sent) == 1
    assert ledger.summary()["stop_reasons"] == ["adapter_protocol_error"]


def test_missing_usage_is_not_free_and_stops_before_successful_completion(tmp_path):
    class NoUsage(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame("partial")
            yield frame("done", finish="stop") + b"data: [DONE]\n\n"

    client, ledger, sent = client_for(tmp_path, NoUsage)
    with client, pytest.raises(BudgetExceeded, match="usage_missing"):
        list(request(client))
    assert len(sent) == 1 and ledger.summary()["unknown_requests"] == 1
    assert ledger.summary()["reserved_cny"] != "0"

@pytest.mark.parametrize('fault', ['network', 'timeout', 'closed'])
def test_partial_stream_failure_retains_reservation_and_blocks_later_http(tmp_path, fault):
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame('partial')
            if fault == 'network':
                raise httpx.ReadError('offline interruption')
            await asyncio.sleep(5)

    client, ledger, sent = client_for(tmp_path, Broken, timeout=.1)
    with client:
        stream = request(client)
        assert next(stream).choices[0].delta.content == 'partial'
        if fault == 'closed':
            stream.close()
        else:
            with pytest.raises(APIConnectionError if fault == 'network' else BudgetExceeded):
                list(stream)
        stream.close()
    assert len(sent) == 1
    assert ledger.summary()['unknown_requests'] == 1
    assert ledger.summary()['reserved_cny'] != '0'
    assert ledger.summary()['stop_reasons']
    with (client_for(tmp_path, Broken, timeout=.1)[0] as again,
          pytest.raises((BudgetExceeded, APIConnectionError))):
        request(again)
    assert len(sent) == 1


def test_split_terminal_frame_is_not_released_before_validation(tmp_path):
    class Split(httpx.AsyncByteStream):
        async def __aiter__(self):
            body = frame('safe', finish='stop') + frame(usage=USAGE) + b'data: [DONE]\n\n'
            for byte in body:
                yield bytes([byte])
    client, ledger, sent = client_for(tmp_path, Split)
    with client:
        assert ''.join(c.choices[0].delta.content or '' for c in request(client)
                       if c.choices) == 'safe'
    assert len(sent) == 1 and ledger.summary()['cost_complete']


def test_close_after_headers_before_consuming_body_is_not_free(tmp_path):
    class Idle(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield frame('never')
    client, ledger, sent = client_for(tmp_path, Idle)
    with client:
        stream = request(client)
        stream.close()
    assert len(sent)==1
    assert ledger.summary()['unknown_requests']==1
    assert ledger.summary()['stop_reasons']==['model_interrupted']
    again, _, blocked = client_for(tmp_path, Idle)
    with again, pytest.raises((BudgetExceeded, APIConnectionError)):
        request(again)
    assert blocked==[]
