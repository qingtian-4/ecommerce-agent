"""Memory integration through ChatService: inject prior facts, write explicit ones."""

from __future__ import annotations

import json

from acs.chat_service import ChatService
from acs.cs_tools import ToolRegistry
from acs.service_agent import CustomerServiceAgent

from .conftest import ScriptedLLM, ScriptedResponse


def make_agent(settings, text="已收到。"):
    return CustomerServiceAgent(
        settings=settings,
        llm=ScriptedLLM([ScriptedResponse(text=text)]),
        registry=ToolRegistry(settings),
    )


def test_chat_service_injects_prior_memory_into_model_context(settings):
    service = ChatService(settings, memory_enabled=True)
    service.memory.add_fact("USER001", "偏好入耳式耳机")
    agent = make_agent(settings)

    list(service.run_turn("推荐一款耳机", "USER001", agent=agent))

    sent = json.dumps(agent.llm.calls[0]["messages"], ensure_ascii=False)
    assert "偏好入耳式耳机" in sent
    assert "用户记忆" in sent


def test_chat_service_writes_explicit_preference_after_turn(settings):
    service = ChatService(settings, memory_enabled=True)
    agent = make_agent(settings)

    list(service.run_turn("请记住我偏好开放式耳机", "USER001", agent=agent))

    facts = service.memory.list_facts("USER001")
    assert any("开放式耳机" in fact.text for fact in facts)
    assert service.memory.get_summary(service.store.list("USER001")[0]["session_id"], "USER001")


def test_memory_is_not_visible_to_another_user(settings):
    service = ChatService(settings, memory_enabled=True)
    service.memory.add_fact("USER001", "只给USER001的偏好")
    agent = make_agent(settings)

    list(service.run_turn("推荐耳机", "USER002", agent=agent))

    sent = json.dumps(agent.llm.calls[0]["messages"], ensure_ascii=False)
    assert "只给USER001的偏好" not in sent
