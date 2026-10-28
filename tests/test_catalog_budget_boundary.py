"""No silent candidates/history loss, and no false completion from partial pages."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessageChunk

from acs.answer_grounding import Evidence, review_answer
from acs.cs_tools import (
    ToolRegistry,
    catalog_model_view,
    catalog_progress,
    expand_model_catalog,
)
from acs.episode_budget import EpisodeLedger
from acs.episode_transport import EpisodeSyncTransport
from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings
from acs.runtime import BudgetExceeded
from acs.shop_demo_data import materialize
from acs.web_service import AnswerStreamCallback


def test_all48_projection_is_exactly_reversible_including_unknown_fields(tmp_path):
    root = tmp_path / "shop"
    materialize(root)
    registry = ToolRegistry(fixture_settings(root, kind=FORMAL_FIXTURE_KIND))
    args, ids, receipts = {}, [], []
    while True:
        value, _, error = registry.run("list_products", json.dumps(args), current_user_id="USER001")
        assert error is None
        wire = json.loads(json.dumps(catalog_model_view(value), ensure_ascii=False))
        assert expand_model_catalog(wire) == value
        ids.extend(p["product_id"] for p in value["products"])
        receipts.append(dict(name="list_products", arguments=json.dumps(args),
                             result=json.dumps(value), error=None))
        progress = catalog_progress(receipts)[0]
        assert progress["complete"] is (not value["has_more"])
        if not value["has_more"]:
            break
        args = value["next_arguments"]
    assert len(ids) == len(set(ids)) == 48
    assert ids == [p["product_id"] for p in registry.db.list_products()]
    assert catalog_progress(receipts[-1:])[0]["complete"] is False


@pytest.mark.parametrize("text", ["已查完全部48件，没有其他商品。", "这是目前的第一批候选。"])
def test_partial_query_is_not_certified_by_answer_wording(text):
    evidence = Evidence(catalog_queries=[dict(complete=False, seen_count=8, total_count=48)])
    result = review_answer(text, evidence)
    assert "查询尚未完成" in result.text and "8/48" in result.text
    assert text not in result.text
    assert result.findings[0].predicate == "catalog_query_incomplete"
    complete = Evidence(catalog_queries=[dict(complete=True, seen_count=48, total_count=48)])
    assert review_answer(text, complete).text == text


def test_incomplete_catalog_does_not_publish_unverified_live_claim():
    published = []
    engine = SimpleNamespace(secret_values=(), emit=lambda *a, **kw: published.append((a, kw)),
        gateway=SimpleNamespace(catalog_queries=lambda *a: [dict(complete=False)]))
    stream = AnswerStreamCallback(engine, "sid", "jid")
    rid = uuid4()
    stream.on_chat_model_start({}, [], run_id=rid)
    text = "已查完所有商品，没有遗漏。" * 30
    stream.on_llm_new_token(text, run_id=rid,
                           chunk=SimpleNamespace(message=AIMessageChunk(content=text)))
    assert not published
    engine.gateway.catalog_queries = lambda *a: [dict(complete=True)]
    stream.on_chat_model_start({}, [], run_id=rid)
    stream.on_llm_new_token(text, run_id=rid,
                           chunk=SimpleNamespace(message=AIMessageChunk(content=text)))
    assert published[0][0][2] == "answer_delta"


def test_actual_utf8_body_is_rejected_before_reservation_or_http(tmp_path):
    ledger = EpisodeLedger(tmp_path / "fees.jsonl")
    ledger.begin("wire")
    called = []
    transport = EpisodeSyncTransport(ledger, "wire", "agent",
        lambda: httpx.MockTransport(lambda r: called.append(r)),
        streaming=True, max_body_bytes=32768)
    body = json.dumps(dict(model="deepseek-flash", max_tokens=1024, stream=True,
                           messages=[dict(role="user", content="约束"*9000)]),
                      ensure_ascii=False).encode()
    assert len(body) > 32768
    with (httpx.Client(transport=transport) as client,
          pytest.raises(BudgetExceeded, match="request_body_limit")):
        client.post("https://offline.invalid/chat/completions", content=body)
    assert not called and ledger.summary()["requests"] == 0
    assert ledger.rows()[-1]["reason"] == "body_limit"


def test_web_history_growth_reports_query_incomplete_without_dispatch(tmp_path):
    from fastapi.testclient import TestClient

    from acs.config import Settings
    from acs.langgraph_poc import deepseek_model
    from acs.local_submission import init_formal_fixture
    from acs.web_service import create_app

    from .test_web_service import ORIGIN, browser, done, events, post

    root = tmp_path / "web"
    init_formal_fixture(root)
    ledger = EpisodeLedger(root / "fees.jsonl")
    ledger.begin("web")
    bodies = []

    def respond(request):
        bodies.append(len(request.content))
        return httpx.Response(200, json=dict(
            id="offline", object="chat.completion", created=1, model="deepseek-flash",
            choices=[dict(index=0, message=dict(role="assistant", content="已记录。"),
                          finish_reason="stop")],
            usage=dict(prompt_tokens=100, completion_tokens=10),
        ))

    @contextmanager
    def factory():
        with httpx.Client(transport=EpisodeSyncTransport(ledger, "web", "agent",
                lambda: httpx.MockTransport(respond), max_body_bytes=32768)) as client:
            yield deepseek_model(Settings(_env_file=None, llm_api_key="offline-test",
                llm_model="deepseek-flash", llm_thinking=False, llm_max_output_tokens=1024),
                http_client=client)

    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        for _ in range(4):
            assert post(client, sid, headers, "messages", text="约束" * 1900).status_code == 202
            state = done(client, sid)
            if state["jobs"][-1]["status"] == "failed":
                break
        assert state["jobs"][-1]["status"] == "failed"
        assert events(client, sid)[-1]["data"]["reason"] == "query_incomplete"
        assert bodies and max(bodies) <= 32768
        assert ledger.summary()["requests"] == len(bodies)
        assert ledger.rows()[-1]["type"] == "preflight_rejected"
