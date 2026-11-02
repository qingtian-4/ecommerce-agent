"""Planner wiring through real SDK/native Graph, with offline HTTP only."""

from __future__ import annotations

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import AIMessage
from openai import APIConnectionError

from acs.config import Settings
from acs.episode_budget import EpisodeLedger, EpisodeLimits
from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport
from acs.intent_planner import IntentPlan, IntentPlanner, PlannedGoal
from acs.langgraph_poc import deepseek_model
from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import init_fixture
from acs.local_submission import init_formal_fixture
from acs.runtime import BudgetExceeded
from acs.sessions import SessionStore
from acs.shop_demo_model import offline_factory
from acs.web_service import create_app

from .test_langgraph_poc import ScriptedModel, call
from .test_web_service import ORIGIN, browser, done, post
from .test_web_service import setup as setup  # noqa: F401


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("V3 planner regressions must not reach the network")
    monkeypatch.setattr(socket.socket, "connect", blocked)


def test_fixed_script_cannot_enable_a_paid_planner_from_environment(setup, monkeypatch):
    from acs.llm import LLMClient

    monkeypatch.setenv("ACS_V3_PLANNER", "1")
    monkeypatch.setenv("ACS_LLM_API_KEY", "offline-key")
    paid_calls = []

    def complete(*args, **kwargs):
        paid_calls.append(kwargs)
        return type("Response", (), {"text": '{"goals":[]}'})()

    monkeypatch.setattr(LLMClient, "complete", complete)
    root, factory, _ = setup
    with TestClient(create_app(root, factory, runtime_mode="fixed_script"),
                    base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        assert done(client, sid)["jobs"][-1]["status"] == "completed"
    assert paid_calls == []


def test_v3_clarification_reaches_model_instead_of_rule_write_hint(tmp_path):
    root = tmp_path / "fixture"
    init_fixture(root)
    model = ScriptedModel(responses=[AIMessage(content="您要改哪一笔订单？")])
    with LocalReturnPoc(root, "USER001", model=model, intent_routing=True) as poc:
        poc.intent_planner = type("Planner", (), {"plan": lambda *a, **k: IntentPlan(
            [PlannedGoal("return", .2)], needs_clarification=True,
            clarify_question="您要改哪一笔订单？")})()
        sid = SessionStore(poc.settings.db_path).create("USER001")
        list(poc.stream(sid, "我要修改退货草稿"))
    sent = "\n".join(str(m.content) for m in model.seen[0])
    assert "您要改哪一笔订单？" in sent
    assert "澄清前不要创建、修改或取消" in sent
    assert "优先使用这些工具" not in sent


def test_planner_uses_host_http_budget_and_body_limit(tmp_path):
    observed = []

    def respond(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash",
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"goals": [
                    {"intent": "orders", "confidence": .9}], "needs_clarification": False}),
            }}],
        })

    ledger = EpisodeLedger(tmp_path / "fees.jsonl", max_requests=1)
    ledger.begin("e", EpisodeLimits(requests=1))
    transport = EpisodeSyncTransport(ledger, "e", "agent",
                                    lambda: httpx.MockTransport(respond),
                                    max_body_bytes=32768)
    with httpx.Client(transport=transport) as client:
        model = deepseek_model(Settings(_env_file=None, llm_api_key="offline-key"),
                               http_client=client, callbacks=[EpisodeCallback(ledger, "e")])
        planner = IntentPlanner.for_chat_model(model)
        assert planner.plan("查我的订单").names == ["orders"]
        assert ledger.summary()["requests"] == 1
        assert planner.plan("再查一次").source == "planner_error"
        assert len(observed) == 1
    assert observed[0].get("max_tokens", observed[0].get("max_completion_tokens")) == 400
    assert not observed[0].get("tools")

    # A rejected oversized planner prompt never reaches the mock provider.
    ledger = EpisodeLedger(tmp_path / "body-fees.jsonl")
    ledger.begin("e")
    transport = EpisodeSyncTransport(ledger, "e", "agent",
                                    lambda: httpx.MockTransport(respond), max_body_bytes=100)
    with httpx.Client(transport=transport) as client:
        model = deepseek_model(Settings(_env_file=None, llm_api_key="offline-key"),
                               http_client=client)
        assert IntentPlanner.for_chat_model(model).plan("查订单").source == "planner_error"
    assert ledger.summary()["requests"] == 0
    assert len(observed) == 1


def test_native_turn_call_limit_includes_the_planner(tmp_path):
    root = tmp_path / "bounded"
    init_fixture(root)
    model = ScriptedModel(responses=[AIMessage(content=json.dumps({"goals": []}))] + [
        call("get_return_task", {}, f"t{i}") for i in range(7)
    ])
    planner = IntentPlanner.for_chat_model(model)
    with LocalReturnPoc(root, "USER001", model=model, intent_routing=True,
                        intent_planner=planner) as poc:
        sid = SessionStore(poc.settings.db_path).create("USER001")
        with pytest.raises(ModelCallLimitExceededError):
            list(poc.stream(sid, "查询草稿"))
    assert len(model.seen) == 8  # one planner + seven business calls


@pytest.mark.parametrize("interactive", [False, True])
def test_offline_ui_can_select_interactive_limit_without_weakening_default(tmp_path, interactive):
    from langchain_core.messages import HumanMessage

    root = tmp_path / "body-profile"
    init_formal_fixture(root)
    kwargs = {"max_body_bytes": 65536} if interactive else {}
    with offline_factory(root, **kwargs)() as model:
        if interactive:
            assert model.invoke([HumanMessage(content="x" * 33000)]).content
        else:
            with pytest.raises((BudgetExceeded, APIConnectionError)):
                model.invoke([HumanMessage(content="x" * 33000)])
    rows = [json.loads(line) for line in (root / "offline-fees.jsonl").read_text().splitlines()]
    assert sum(r["type"] == "reserved" for r in rows) == int(interactive)
    if not interactive:
        assert any(r["type"] == "preflight_rejected" and r["limit"] == 32768 for r in rows)
