"""Optional external integration checks; run with the pinned τ environment."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("tau2")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tau_acs_agent  # noqa: E402
from tau2.data_model.message import AssistantMessage, ToolMessage, UserMessage  # noqa: E402

from acs.config import Settings  # noqa: E402

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402


def test_bridge_runs_real_loop_and_keeps_official_tool_result(monkeypatch):
    from acs import service_agent

    llm = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall("official-1", "lookup", '{"order_id":"X"}'),
        ]), ScriptedResponse(text="The official order is pending."),
    ])
    monkeypatch.setattr(service_agent, "LLMClient", lambda **kwargs: llm)
    monkeypatch.setattr(tau_acs_agent, "get_settings", lambda: Settings(_env_file=None))
    from types import SimpleNamespace
    tool = SimpleNamespace(openai_schema={"type": "function", "function": {
        "name": "lookup", "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}}, "required": ["order_id"]}}})
    adapter = tau_acs_agent.ACSAgent(tools=[tool], domain_policy="Official policy")
    state = adapter.get_init_state([AssistantMessage.text("Hello!")])
    call, state = adapter.generate_next_message(UserMessage(role="user", content="Check X"), state)
    assert call.tool_calls[0].id == "official-1"
    assert call.tool_calls[0].arguments == {"order_id": "X"}
    reply, state = adapter.generate_next_message(ToolMessage(
        role="tool", id="official-1", content='{"status":"pending","source":"official"}',
    ), state)
    assert reply.content == "The official order is pending."
    assert state.stream is None
    sent = llm.calls[-1]["messages"]
    assert any(m.get("tool_call_id") == "official-1" and "official" in m["content"] for m in sent)
    assert sent[0]["content"].endswith("</policy>")
    assert "Official policy" in sent[0]["content"]


def test_v2_adapter_runs_actual_router_and_exposes_official_tool_names(monkeypatch):
    from types import SimpleNamespace

    from acs import service_agent

    model = ScriptedLLM([ScriptedResponse(text='Please provide an order id.')])
    settings = Settings(_env_file=None, workflow_router_enabled=True,
                        runtime_recovery_enabled=True)
    monkeypatch.setattr(service_agent, 'LLMClient', lambda **kw: model)
    monkeypatch.setattr(tau_acs_agent, 'get_settings', lambda: settings)
    tool = SimpleNamespace(openai_schema={'type': 'function', 'function': {
        'name': 'get_order_details', 'parameters': {'type': 'object', 'properties': {}}}})
    adapter = tau_acs_agent.ACSAgent(tools=[tool], domain_policy='Official policy')
    state = adapter.get_init_state()
    reply, state = adapter.generate_next_message(
        UserMessage(role='user', content='Can you help with my order?'), state,
    )
    route = next(e for e in reply.raw_data['acs_trace'] if e['type'] == 'route_selected')
    assert route['detail']['confidence'] == 'fallback'
    assert route['detail']['allowed_tools'] == ['get_order_details']
    assert reply.content == 'Please provide an order id.' and state.stream is None


def test_route_rejection_cannot_escape_to_official_environment_before_loop_check(monkeypatch):
    from types import SimpleNamespace

    from acs import service_agent

    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('blocked', 'get_product', '{"product_id":"X"}'),
        ]), ScriptedResponse(text='No external tool was executed.'),
    ])
    monkeypatch.setattr(service_agent, 'LLMClient', lambda **kw: model)
    monkeypatch.setattr(tau_acs_agent, 'get_settings', lambda: Settings(
        _env_file=None, workflow_router_enabled=True, runtime_recovery_enabled=True,
    ))
    tool = SimpleNamespace(openai_schema={'type': 'function', 'function': {
        'name': 'get_product', 'parameters': {'type': 'object', 'properties': {}}}})
    order_tool = SimpleNamespace(openai_schema={'type': 'function', 'function': {
        'name': 'get_order', 'parameters': {'type': 'object', 'properties': {}}}})
    adapter = tau_acs_agent.ACSAgent(tools=[tool, order_tool], domain_policy='Official policy')
    reply, state = adapter.generate_next_message(
        UserMessage(role='user', content='查订单'), adapter.get_init_state(),
    )
    assert not reply.tool_calls
    assert any(e['type'] == 'error' and e['detail'].get('executed') is False
               for e in reply.raw_data['acs_trace'])
    assert state.pending_id is None


def test_official_error_text_is_classified_without_dict_union_crash(monkeypatch):
    from types import SimpleNamespace

    from acs import service_agent

    model = ScriptedLLM([
        ScriptedResponse(wants_tools=True, tool_calls=[
            ScriptedToolCall('bad', 'lookup', '{}'),
        ]), ScriptedResponse(text='The official lookup failed.'),
    ])
    monkeypatch.setattr(service_agent, 'LLMClient', lambda **kw: model)
    monkeypatch.setattr(tau_acs_agent, 'get_settings', lambda: Settings(
        _env_file=None, runtime_recovery_enabled=True,
    ))
    tool = SimpleNamespace(openai_schema={'type': 'function', 'function': {
        'name': 'lookup', 'parameters': {'type': 'object', 'properties': {}}}})
    order_tool = SimpleNamespace(openai_schema={'type': 'function', 'function': {
        'name': 'get_order', 'parameters': {'type': 'object', 'properties': {}}}})
    adapter = tau_acs_agent.ACSAgent(tools=[tool, order_tool], domain_policy='Official policy')
    _, state = adapter.generate_next_message(UserMessage(role='user', content='Lookup'),
                                             adapter.get_init_state())
    answer, state = adapter.generate_next_message(ToolMessage(
        role='tool', id='bad', content='Official error text', error=True,
    ), state)
    assert answer.content == 'The official lookup failed.'
    assert any(e['type'] == 'runtime_failure' for e in answer.raw_data['acs_trace'])
