"""Mandatory offline paid-boundary tests; real SDKs, no external network."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import socket
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import litellm
import pytest
from openai import APIConnectionError, AsyncOpenAI

from acs.config import Settings
from acs.episode_budget import EpisodeLedger, EpisodeLimits
from acs.episode_transport import EpisodeAsyncTransport
from acs.runtime import BudgetExceeded

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tau2.data_model.message import UserMessage  # noqa: E402
from tau2.domains.retail.environment import get_environment  # noqa: E402
from tau2.evaluator import evaluator_nl_assertions  # noqa: E402
from tau2.utils.llm_utils import generate  # noqa: E402
from tau_episode import make_agent, sync_client  # noqa: E402

BODY = json.dumps({"model": "deepseek-flash", "max_tokens": 1024}).encode()
USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 40}
ARMS = ["acs_loop_receipt_guard", "langgraph"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network forbidden in episode budget tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)


def ledger_at(path, **kwargs):
    ledger = EpisodeLedger(path, **kwargs)
    ledger.begin("e")
    return ledger


def settings():
    return Settings(_env_file=None, llm_api_key="offline-test", llm_model="deepseek-flash",
                    llm_temperature=0, llm_max_output_tokens=1024, llm_thinking=False)


def completion(message, body):
    if body.get("stream"):
        delta = dict(message)
        delta.pop("role", None)
        for index, call in enumerate(delta.get("tool_calls") or []):
            call["index"] = index
        chunks = [{"choices": [{"index": 0, "delta": delta,
                                "finish_reason": ("tool_calls" if delta.get("tool_calls")
                                                  else "stop")}]},
                  {"choices": [], "usage": USAGE}]
        data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
        return httpx.Response(200, content=data+b"data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 1,
        "model": "deepseek-flash", "usage": USAGE,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}]})


def responder(messages, observed):
    def respond(request):
        body = json.loads(request.content)
        observed.append(body)
        return completion(messages.pop(0), body)
    return lambda: httpx.MockTransport(respond)


def call_message(cid="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": cid,
        "type": "function", "function": {"name": "get_order_details",
                                         "arguments": '{"order_id":"#W1845024"}'}}]}


def reserve_worker(path, queue):
    ledger = EpisodeLedger(path, max_requests=2)
    try:
        ledger.reserve("e", "agent", BODY)
        queue.put("sent")
    except BudgetExceeded:
        queue.put("blocked")


def test_reservations_cross_process_atomic(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger_at(path, max_requests=2)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    processes = [ctx.Process(target=reserve_worker, args=(path, queue)) for _ in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(45)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=1) for _ in processes) == ["blocked", "blocked", "sent", "sent"]
    assert EpisodeLedger(path, max_requests=2).summary()["requests"] == 2


def test_deadline_and_limits_survive_reopen(tmp_path):
    now = [100.0]
    path = tmp_path / "budget.jsonl"
    ledger = EpisodeLedger(path, clock=lambda: now[0])
    limits = EpisodeLimits(episode_timeout_s=5, model_timeout_s=2)
    ledger.begin("e", limits)
    assert ledger.remaining("e") == 2
    now[0] = 104
    reopened = EpisodeLedger(path, clock=lambda: now[0])
    reopened.begin("e", limits)
    assert reopened.remaining("e") == 1
    with pytest.raises(ValueError):
        reopened.begin("e", replace(limits, requests=20))
    now[0] = 105
    with pytest.raises(BudgetExceeded, match="episode_deadline"):
        reopened.reserve("e", "user", BODY)
    assert reopened.summary()["requests"] == 0


@pytest.mark.parametrize("role", ["agent", "user", "judge"])
def test_usage_roles_and_cache(tmp_path, role):
    ledger = ledger_at(tmp_path / "ledger")
    reservation = ledger.reserve("e", role, BODY)
    ledger.finish(reservation, status="completed", usage=USAGE, latency_ms=12)
    summary = ledger.summary("e")
    assert summary["cost_complete"]
    assert summary["roles"][role]["requests"] == 1
    assert Decimal(summary["known_peak_estimated_cny"]) == Decimal("0.0002816")
    with pytest.raises(ValueError, match="Duplicate HTTP"):
        ledger.finish(reservation, status="completed", usage=USAGE)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 1, "completion_tokens": 1025}])
def test_missing_and_excess_usage_keep_reservation_and_stop(tmp_path, usage):
    ledger = ledger_at(tmp_path / "ledger")
    reservation = ledger.reserve("e", "agent", BODY)
    with pytest.raises(BudgetExceeded):
        ledger.finish(reservation, status="completed", usage=usage)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("e", "judge", BODY)
    assert not ledger.summary()["cost_complete"]
    assert ledger.summary()["reserved_cny"] == reservation["reserved_cny"]


def test_unknown_receipt_tool_limits_and_steps(tmp_path):
    ledger = EpisodeLedger(tmp_path / "ledger")
    ledger.begin("e", EpisodeLimits(tools=1, steps=1))
    ledger.tool_intent("e", {"id": "1"})
    ledger.tool_receipt("e", {"id": "1", "content": "OK"})
    with pytest.raises(BudgetExceeded, match="adapter_protocol_error"):
        ledger.tool_receipt("e", {"id": "1", "content": "OK"})
    ledger.begin("e2", EpisodeLimits(tools=1, steps=1))
    ledger.step("e2")
    with pytest.raises(BudgetExceeded, match="orchestrator_step_limit"):
        ledger.step("e2")
    ledger.begin("e3", EpisodeLimits(tools=1))
    ledger.tool_intent("e3", {"id": "1"})
    with pytest.raises(BudgetExceeded, match="episode_tool_limit"):
        ledger.tool_intent("e3", {"id": "2"})


def test_cost_limit_stops_before_transport(tmp_path):
    ledger = ledger_at(tmp_path / "ledger", cap_cny=0.001)
    sends = []
    async def run():
        async with AsyncOpenAI(api_key="offline", max_retries=0,
            http_client=httpx.AsyncClient(transport=EpisodeAsyncTransport(
                ledger, "e", "agent", httpx.MockTransport(lambda r: sends.append(r))))) as client:
            with pytest.raises((APIConnectionError, BudgetExceeded)):
                await client.chat.completions.create(model="deepseek-flash", max_tokens=1024,
                                                      messages=[{"role": "user", "content": "hi"}])
    asyncio.run(run())
    assert sends == []
    assert ledger.summary()["stop_reasons"] == ["global_cost_limit"]


@pytest.mark.parametrize("arm", ARMS)
def test_sdk_multiturn_and_budget_no_http(tmp_path, arm):
    ledger = EpisodeLedger(tmp_path / "ledger")
    ledger.begin("e", EpisodeLimits(requests=2))
    observed = []
    agent = make_agent(arm, get_environment(), settings(), ledger, "e", tmp_path / "graph.sqlite",
                       transport_factory=responder([{"role": "assistant", "content": "one"},
                            {"role": "assistant", "content": "two"}], observed))
    state = agent.get_init_state()
    try:
        for text in ["first", "second"]:
            reply, state = agent.generate_next_message(
                UserMessage(role="user", content=text), state)
            assert reply.content in {"one", "two"}
            assert reply.usage == {"prompt_tokens": 100, "completion_tokens": 20}
            assert reply.cost > 0
        reply, state = agent.generate_next_message(UserMessage(role="user", content="third"), state)
        assert agent.is_stop(reply)
        assert reply.raw_data["episode_stop_reason"] == "episode_request_limit"
        assert len(observed) == 2
        assert ledger.summary()["requests"] == 2
        if arm == "langgraph":
            assert any(r["type"] == "sdk_end" for r in ledger.rows())
    finally:
        agent.stop(state=state)


@pytest.mark.parametrize("arm", ARMS)
def test_official_tool_receipt_budget_and_duplicate(tmp_path, arm):
    ledger = ledger_at(tmp_path / "ledger")
    env, observed = get_environment(), []
    agent = make_agent(arm, env, settings(), ledger, "e", tmp_path / "graph.sqlite",
                       transport_factory=responder([call_message(),
                           {"role": "assistant", "content": "done"}], observed))
    state = agent.get_init_state()
    try:
        first, state = agent.generate_next_message(UserMessage(role="user", content="order"), state)
        assert first.tool_calls[0].id == "c1"
        receipt = env.get_response(first.tool_calls[0])
        last, state = agent.generate_next_message(receipt, state)
        assert last.content == "done"
        duplicate, state = agent.generate_next_message(receipt, state)
        assert duplicate.raw_data["episode_stop_reason"] == "adapter_protocol_error"
        assert len(observed) == 2
        tool_history = observed[-1]["messages"][-1]
        assert tool_history["role"] == "tool" and tool_history["tool_call_id"] == "c1"
    finally:
        agent.stop(state=state)


@pytest.mark.parametrize("arm", ARMS)
def test_pending_write_reconnect_fails_closed(tmp_path, arm):
    ledger = ledger_at(tmp_path / "ledger")
    ledger.tool_intent("e", {"id": "write", "name": "modify_user_address"})
    agent = make_agent(arm, get_environment(), settings(), ledger, "e", tmp_path / "graph.sqlite",
                       transport_factory=responder([], []))
    try:
        with pytest.raises(BudgetExceeded, match="state_uncertain"):
            agent.get_init_state()
    finally:
        agent.stop()


@pytest.mark.parametrize("role", ["user", "judge"])
def test_official_litellm_real_sdk_path_and_limit(tmp_path, role):
    ledger = EpisodeLedger(tmp_path / "ledger")
    ledger.begin("e", EpisodeLimits(requests=1))
    observed = []
    with sync_client(settings(), ledger, "e", role, inner_factory=responder(
            [{"role": "assistant", "content": "official SDK path"}], observed)) as client:
        kwargs = {"model": "openai/deepseek-flash", "messages": [UserMessage(
            role="user", content="test")], "client": client, "num_retries": 0,
            "max_tokens": 1024, "temperature": 0}
        reply = generate(**kwargs)
        assert reply.content == "official SDK path"
        with pytest.raises(litellm.InternalServerError):
            generate(**kwargs)
    assert len(observed) == 1
    assert ledger.summary()["roles"][role]["requests"] == 1


def test_official_judge_uses_guarded_sdk(tmp_path, monkeypatch):
    ledger = ledger_at(tmp_path / "ledger")
    observed = []
    result = {"results": [{"expectedOutcome": "greet", "metExpectation": True,
                           "reasoning": "offline fixture"}]}
    with sync_client(settings(), ledger, "e", "judge", inner_factory=responder(
            [{"role": "assistant", "content": json.dumps(result)}], observed)) as client:
        monkeypatch.setattr(evaluator_nl_assertions, "DEFAULT_LLM_NL_ASSERTIONS",
                            "openai/deepseek-flash")
        monkeypatch.setattr(evaluator_nl_assertions, "DEFAULT_LLM_NL_ASSERTIONS_ARGS", {
            "client": client, "num_retries": 0, "max_tokens": 1024, "temperature": 0})
        checks = evaluator_nl_assertions.NLAssertionsEvaluator.evaluate_nl_assertions(
            [UserMessage(role="user", content="hi")], ["greet"])
    assert checks[0].met
    assert len(observed) == ledger.summary()["roles"]["judge"]["requests"] == 1


@pytest.mark.parametrize("episode_timeout,model_timeout,reason", [
    (2, 0.02, "TimeoutError"), (0.2, 2, "TimeoutError")])
def test_total_response_timeout_not_per_chunk(tmp_path, episode_timeout, model_timeout, reason):
    ledger = EpisodeLedger(tmp_path / "ledger")
    ledger.begin("e", EpisodeLimits(episode_timeout_s=episode_timeout,
                                    model_timeout_s=model_timeout))
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await asyncio.sleep(0.005)
    async def run():
        async with httpx.AsyncClient(transport=EpisodeAsyncTransport(ledger, "e", "agent",
            httpx.MockTransport(lambda r: httpx.Response(200, stream=Trickle())))) as client:
            with pytest.raises(TimeoutError):
                await client.post("https://offline.test", content=BODY)
    asyncio.run(run())
    assert next(r for r in ledger.rows() if r["type"] == "request_end")["status"] == reason
    assert not ledger.summary()["cost_complete"]


@pytest.mark.parametrize("arm", ARMS)
def test_unknown_write_is_terminal_after_process_reconnect(tmp_path, arm):
    from tau2.data_model.message import ToolMessage
    from tau2.domains.retail.environment import get_tasks
    action = next(t for t in get_tasks("base") if t.id == "33").evaluation_criteria.actions[-1]
    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "write",
        "type": "function", "function": {"name": action.name,
                                         "arguments": json.dumps(action.arguments)}}]}
    ledger = ledger_at(tmp_path / "ledger")
    env = get_environment()
    observed = []
    agent = make_agent(arm, env, settings(), ledger, "e", tmp_path / "graph",
                       transport_factory=responder([message], observed))
    state = agent.get_init_state()
    try:
        intent, state = agent.generate_next_message(
            UserMessage(role="user", content="change"), state)
        assert intent.tool_calls
        stopped, state = agent.generate_next_message(ToolMessage(
            role="tool", id="write", content="Transport disconnected; outcome unknown", error=True),
            state)
        assert stopped.raw_data["episode_stop_reason"] == "state_uncertain"
    finally:
        agent.stop(state=state)
    reopened = make_agent(arm, env, settings(), EpisodeLedger(tmp_path / "ledger"), "e",
                          tmp_path / "graph", transport_factory=responder([], observed))
    try:
        with pytest.raises(BudgetExceeded, match="state_uncertain"):
            reopened.get_init_state()
    finally:
        reopened.stop()
    assert len(observed) == 1


@pytest.mark.parametrize("arm", ARMS)
def test_late_real_receipt_recorded_without_next_model_request(tmp_path, arm):
    now = [100.0]
    ledger = EpisodeLedger(tmp_path / "ledger", clock=lambda: now[0])
    ledger.begin("e", EpisodeLimits(episode_timeout_s=2))
    env, observed = get_environment(), []
    agent = make_agent(arm, env, settings(), ledger, "e", tmp_path / "graph",
                       transport_factory=responder([call_message()], observed))
    state = agent.get_init_state()
    try:
        intent, state = agent.generate_next_message(
            UserMessage(role="user", content="order"), state)
        receipt = env.get_response(intent.tool_calls[0])
        now[0] = 102
        stopped, state = agent.generate_next_message(receipt, state)
        assert stopped.raw_data["episode_stop_reason"] == "episode_deadline"
        assert [r["receipt"]["id"] for r in ledger.rows() if r["type"] == "tool_receipt"] == ["c1"]
        assert len(observed) == 1
    finally:
        agent.stop(state=state)


def test_native_outbox_usage_export_survives_lost_delivery(tmp_path):
    ledger = ledger_at(tmp_path / "ledger")
    env, observed = get_environment(), []
    agent = make_agent("langgraph", env, settings(), ledger, "e", tmp_path / "graph",
                       transport_factory=responder([{"role": "assistant", "content": "answer"}],
                                                   observed))
    state = agent.get_init_state()
    first, state = agent.generate_next_message(UserMessage(role="user", content="question"), state)
    agent.stop(state=state)
    recovered = make_agent("langgraph", env, settings(), EpisodeLedger(tmp_path / "ledger"),
                           "e", tmp_path / "graph", transport_factory=responder([], observed))
    try:
        second, state = recovered.resume(state)
        assert second.usage == first.usage == {"prompt_tokens": 100, "completion_tokens": 20}
        assert second.cost == first.cost > 0
        assert len(observed) == 1
    finally:
        recovered.stop(state=state)


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("failure,expected", [("timeout", "model_timeout"),
                                             ("disconnect", "model_request_failed")])
def test_failure_canonical_reasons_same_on_both_arms(tmp_path, arm, failure, expected):
    ledger = EpisodeLedger(tmp_path / "ledger")
    ledger.begin("e", EpisodeLimits(model_timeout_s=0.1))
    calls = []
    async def broken(request):
        calls.append(request)
        if failure == "timeout":
            await asyncio.sleep(1)
        raise httpx.ReadError("offline disconnect")
    agent = make_agent(arm, get_environment(), settings(), ledger, "e", tmp_path / "graph",
                       transport_factory=lambda: httpx.MockTransport(broken))
    state = agent.get_init_state()
    try:
        reply, state = agent.generate_next_message(
            UserMessage(role="user", content="question"), state)
        assert reply.raw_data["episode_stop_reason"] == expected
        assert ledger.summary()["unknown_requests"] == 1
        assert len(calls) == 1
    finally:
        agent.stop(state=state)


def test_actual_sdk_wire_inputs_match_across_runtimes(tmp_path):
    bodies = []
    for arm in ARMS:
        ledger = ledger_at(tmp_path / (arm + ".ledger"))
        observed = []
        env = get_environment()
        agent = make_agent(arm, env, settings(), ledger, "e", tmp_path / "graph",
            transport_factory=responder([call_message(),
                {"role": "assistant", "content": "done"}], observed))
        state = agent.get_init_state()
        try:
            reply, state = agent.generate_next_message(
            UserMessage(role="user", content="order"), state)
            _, state = agent.generate_next_message(env.get_response(reply.tool_calls[0]), state)
        finally:
            agent.stop(state=state)
        for body in observed:
            # Transport-only differences; preserve original request bodies in the SDK.
            body.pop("stream", None)
            body.pop("stream_options", None)
            if "max_completion_tokens" in body:
                body["max_tokens"] = body.pop("max_completion_tokens")
            for message in body["messages"]:
                for call in message.get("tool_calls") or []:
                    call["function"]["arguments"] = json.loads(call["function"]["arguments"])
        bodies.append(observed)
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("task_id", ["33", "34"])
def test_full_official_episode_paired_sdk_reward_db_and_inputs(tmp_path, task_id):
    """Fixed decisions prove protocol parity only, never quality improvement."""
    from eval_tau_development_pilot import attach_host_hooks
    from tau2.domains.retail.environment import get_tasks
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.runner.build import build_user
    from tau2.runner.simulation import run_simulation
    task = next(t for t in get_tasks("base") if t.id == task_id)
    results = []
    for arm in ARMS:
        out = tmp_path / arm
        out.mkdir()
        ledger = ledger_at(out / "ledger")
        observed = []
        decisions = []
        for action in task.evaluation_criteria.actions:
            decisions.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": action.action_id, "type": "function", "function": {
                    "name": action.name, "arguments": json.dumps(action.arguments)}}]})
        # Batch two independent order reads in one model decision; both executors
        # must dispatch serially without dropping either or repeating the model call.
        decisions[2]["tool_calls"].extend(decisions.pop(3)["tool_calls"])
        decisions.append({"role": "assistant", "content": "The requested changes are complete."})
        env = get_environment()
        user_observed = []
        with sync_client(settings(), ledger, "e", "user", inner_factory=responder([
            {"role": "assistant", "content": "Please follow my original request."},
            {"role": "assistant", "content": "###STOP###"}], user_observed)) as client:
            user = build_user("user_simulator", env, task, llm="openai/deepseek-flash",
                llm_args={"num_retries": 0, "max_tokens": 1024,
                          "temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}})
            user.llm_args["client"] = client
            agent = make_agent(arm, env, settings(), ledger, "e", out / "graph",
                               transport_factory=responder(decisions, observed))
            orch = Orchestrator("retail", agent, user, env, task, seed=20261023,
                                max_steps=40, timeout=240)
            attach_host_hooks(orch, ledger, "e", out)
            simulation = run_simulation(orch)
        assert simulation.reward_info.reward == 1
        assert ledger.summary()["roles"]["agent"]["requests"] == 6
        assert ledger.summary()["roles"]["user"]["requests"] == 2
        assert sum(r["type"] == "tool_execute_started" for r in ledger.rows()) == 6
        assert sum((m.usage or {}).get("prompt_tokens", 0) for m in simulation.messages
                   if m.role == "assistant") == 600
        for body in observed:
            body.pop("stream", None)
            body.pop("stream_options", None)
            if "max_completion_tokens" in body:
                body["max_tokens"] = body.pop("max_completion_tokens")
            for message in body["messages"]:
                for call in message.get("tool_calls") or []:
                    call["function"]["arguments"] = json.loads(call["function"]["arguments"])
        results.append((env.get_db_hash(), simulation.reward_info.model_dump(mode="json"),
                        observed))
    assert results[0] == results[1]


@pytest.mark.parametrize("payload", [
    {"n": 2}, {"max_completion_tokens": 1024}, {"thinking": {"type": "enabled"}},
    {"max_tokens": -1}, {"max_tokens": 1025}, {"model": "unpriced"},
])
def test_unpriced_or_expanded_request_rejected_without_reservation(tmp_path, payload):
    ledger = ledger_at(tmp_path / "ledger")
    with pytest.raises(ValueError):
        ledger.reserve("e", "agent", json.dumps(json.loads(BODY) | payload).encode())
    assert ledger.summary()["requests"] == 0


def test_cli_freeze_and_gate_rejects_changed_source(tmp_path):
    from eval_tau_development_pilot import fingerprints, validate_gates
    path = tmp_path / "gates.json"
    path.write_text(json.dumps({"source": fingerprints() | {"modified": "source"}}))
    with pytest.raises(ValueError, match="different source"):
        validate_gates(path)


@pytest.mark.parametrize("tag", ["skipped", "failure", "error", "empty"])
def test_pilot_gate_cannot_silently_skip_tests(tmp_path, tag):
    from eval_tau_development_pilot import fingerprints, validate_gates
    path = tmp_path / "gates.json"
    path.write_text(json.dumps({"source": fingerprints(),
        "pytest": {"exit_code": 0, "junit": "junit.xml"},
        "ruff": {"exit_code": 0}, "secrets": {"exit_code": 0}}))
    (tmp_path / "junit.xml").write_text('<testsuite/>' if tag == "empty" else
        f'<testsuite><testcase><{tag}/></testcase></testsuite>')
    with pytest.raises(ValueError, match="failed, empty or silently skipped"):
        validate_gates(path)


@pytest.mark.parametrize("arm", ARMS)
def test_completed_history_reconnect_inherits_request_budget(tmp_path, arm):
    limits = EpisodeLimits(requests=1)
    ledger = EpisodeLedger(tmp_path / "ledger")
    ledger.begin("e", limits)
    env, observed = get_environment(), []
    agent = make_agent(arm, env, settings(), ledger, "e", tmp_path / "graph",
        transport_factory=responder([{"role": "assistant", "content": "answer"}], observed))
    state = agent.get_init_state()
    question = UserMessage(role="user", content="first")
    reply, state = agent.generate_next_message(question, state)
    agent.stop(state=state)
    reopened_ledger = EpisodeLedger(tmp_path / "ledger")
    reopened_ledger.begin("e", limits)
    reconnected = make_agent(arm, env, settings(), reopened_ledger, "e", tmp_path / "graph",
                             transport_factory=responder([], observed))
    state = reconnected.get_init_state([question, reply])
    try:
        stopped, state = reconnected.generate_next_message(
            UserMessage(role="user", content="second"), state)
        assert stopped.raw_data["episode_stop_reason"] == "episode_request_limit"
        assert len(observed) == 1
        assert reopened_ledger.summary()["requests"] == 1
    finally:
        reconnected.stop(state=state)


@pytest.mark.parametrize("receipt", [{"id": "wrong"}, {"id": "1", "requestor": "user"}])
def test_wrong_receipt_identity_stops_durably(tmp_path, receipt):
    ledger = ledger_at(tmp_path / "ledger")
    ledger.tool_intent("e", {"id": "1", "requestor": "assistant"})
    with pytest.raises(BudgetExceeded, match="adapter_protocol_error"):
        ledger.tool_receipt("e", receipt)
    assert ledger.summary()["stop_reasons"] == ["adapter_protocol_error"]
