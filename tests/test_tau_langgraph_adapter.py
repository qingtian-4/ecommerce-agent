"""Mandatory pinned official-domain contract tests, not model quality evaluation.

Install --extra tau --extra v3 and set TAU2_DATA_DIR to the pinned checkout/data.
Missing dependencies or data are errors, never successful skipped checks.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai.chat_models.base import _convert_message_to_dict
from pydantic import Field

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from tau2.data_model.message import (  # noqa: E402
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.domains.retail.environment import get_environment, get_tasks  # noqa: E402
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation  # noqa: E402
from tau2.orchestrator.orchestrator import Orchestrator  # noqa: E402
from tau2.runner.build import build_user  # noqa: E402
from tau2.user import user_simulator  # noqa: E402
from tau2.utils.llm_utils import to_litellm_messages  # noqa: E402
from tau_acs_agent import ACSAgent  # noqa: E402
from tau_langgraph_agent import LangGraphAgent  # noqa: E402

from acs.config import Settings  # noqa: E402

from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

TASKS = {t.id: t for t in get_tasks("base")}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("No network permitted in official adapter offline contracts")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setenv("ACS_LLM_API_KEY", "")
    monkeypatch.setenv("ACS_EMBEDDING_API_KEY", "")


class Decisions(BaseChatModel):
    responses: list = Field(default_factory=list)
    observed: list = Field(default_factory=list)
    bound: list = Field(default_factory=list)
    delay_s: float = 0

    @property
    def _llm_type(self):
        return "offline-official-contract"

    def bind_tools(self, tools, **kwargs):
        from langchain_core.utils.function_calling import convert_to_openai_tool

        self.bound.append([convert_to_openai_tool(t) for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ChatResult(generations=[ChatGeneration(message=response)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self._generate(messages, stop=stop, **kwargs)


def tool(name="get_order_details", args=None, cid="c1"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": cid,
                "name": name,
                "args": args if args is not None else {"order_id": "#W1845024"},
            }
        ],
    )


def plan_task(task_id):
    return [
        tool(a.name, a.arguments, a.action_id) for a in TASKS[task_id].evaluation_criteria.actions
    ]


def legacy_decisions(responses):
    if isinstance(responses[0], Exception):
        model = ScriptedLLM([])

        def interrupted(messages, *, tools=None, stage="unspecified", **kwargs):
            model.calls.append({"messages": list(messages), "tools": tools or [], "stage": stage})
            raise responses[0]

        model.chat = interrupted
        return model
    return ScriptedLLM(
        [
            ScriptedResponse(
                text=m.content,
                wants_tools=bool(m.tool_calls),
                tool_calls=[
                    ScriptedToolCall(c["id"], c["name"], json.dumps(c["args"]))
                    for c in m.tool_calls
                ],
            )
            for m in responses
        ]
    )


def candidate(env, path, responses, **kw):
    model = Decisions(responses=responses)
    return LangGraphAgent(
        env.get_tools(), env.get_policy(), checkpoint_path=path, model=model, **kw
    ), model


def paired_run(tmp_path, responses, task_id="33", receipt_fault=None, **limits):
    rows = []
    for arm in ["old", "graph"]:
        env = get_environment()
        executed = []
        original = env.get_response

        def execute(call, executed=executed, original=original):
            executed.append(call.model_dump(mode="json"))
            reply = original(call)
            if receipt_fault and call.name == "modify_user_address":
                reply = reply.model_copy(update=receipt_fault)
            return reply

        env.get_response = execute
        if arm == "old":
            model = legacy_decisions(responses)
            agent = ACSAgent(
                env.get_tools(),
                env.get_policy(),
                receipt_guard=True,
                settings=Settings(
                    _env_file=None,
                    max_steps=limits.get("max_model_calls", 10),
                    max_tool_calls=limits.get("max_tool_calls", 12),
                    context_max_chars=64000,
                ),
                llm_factory=lambda _, model=model: model,
            )
        else:
            agent, model = candidate(
                env,
                tmp_path / (arm + ".sqlite"),
                list(responses),
                max_model_calls=limits.get("max_model_calls", 10),
                max_tool_calls=limits.get("max_tool_calls", 12),
            )
        orchestrator = Orchestrator(
            "retail",
            agent,
            build_user("user_simulator", env, TASKS[task_id], llm="offline-fixed-decisions"),
            env,
            TASKS[task_id],
            max_steps=40,
            max_errors=10,
            timeout=120,
            seed=17,
        )
        user_requests = []

        def user_model(*, model, messages, tools, call_name, user_requests=user_requests, **kwargs):
            user_requests.append(
                {
                    "model": model,
                    "call_name": call_name,
                    "messages": to_litellm_messages(messages),
                }
            )
            return AssistantMessage.text(
                "Please follow the requested task." if len(user_requests) == 1 else "###STOP###"
            )

        # Replace only the user's model transport; official simulator/persona/history run unchanged.
        with patch.object(user_simulator, "generate", user_model):
            result = orchestrator.run()
        # DB-only official tasks do not require paid NL judges. ALL is unmodified.
        try:
            reward = evaluate_simulation(
                result,
                TASKS[task_id],
                EvaluationType.ALL,
                solo_mode=False,
                domain="retail",
                strict_replay=True,
            ).model_dump(mode="json")
        except ValueError as error:
            if not receipt_fault:
                raise
            # Synthetic transport faults cannot pass the official strict replay.
            # Preserve that refusal; never relax the scorer or invent a reward.
            reward = {
                "reward": None,
                "status": "strict_replay_rejected_injected_receipt",
                "error_type": type(error).__name__,
            }

        rows.append(
            {
                "arm": arm,
                "task_id": task_id,
                "db": env.get_db_hash(),
                "reward": reward,
                "executed": executed,
                "messages": result.messages,
                "termination": result.termination_reason.value,
                "model": model,
                "model_requests": [
                    {"messages": c["messages"], "tools": c["tools"]} for c in model.calls
                ]
                if arm == "old"
                else [
                    {
                        "messages": [_convert_message_to_dict(m) for m in messages],
                        "tools": model.bound[i],
                    }
                    for i, messages in enumerate(model.observed)
                ],
                "user_requests": user_requests,
                "state_note": "Official environment hash and raw receipts are authoritative",
            }
        )
    if output := os.environ.get("ACS_TAU_CONTRACT_OUT"):
        path = Path(output) / (tmp_path.name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        k: ([m.model_dump(mode="json") for m in v] if k == "messages" else v)
                        for k, v in row.items()
                        if k != "model"
                    }
                    for row in rows
                ],
                indent=2,
            )
            + "\n"
        )
    assert rows[0]["db"] == rows[1]["db"]
    assert rows[0]["reward"]["reward"] == rows[1]["reward"]["reward"]
    assert rows[0]["executed"] == rows[1]["executed"]
    assert rows[0]["user_requests"][0] == rows[1]["user_requests"][0]
    return rows


@pytest.mark.parametrize("scenario", ["direct", "single", "steps", "batch", "bad_args", "refusal"])
def test_official_pair_contracts(tmp_path, scenario):
    plans = {
        "direct": [],
        "single": [tool()],
        "steps": [tool(), tool("get_user_details", {"user_id": "noah_patel_6952"}, "c2")],
        "batch": [
            AIMessage(
                content="",
                tool_calls=tool().tool_calls
                + tool("get_user_details", {"user_id": "noah_patel_6952"}, "c2").tool_calls,
            )
        ],
        "bad_args": [tool(args={})],
        "refusal": [tool("cancel_pending_order", {"order_id": "#W6111398", "reason": "other"})],
    }
    rows = paired_run(tmp_path, plans[scenario] + [AIMessage(content="Task response complete.")])
    # Equivalent semantic request projection: native IDs/metadata differ by representation.
    old = rows[0]["model"].calls
    model = rows[1]["model"]
    assert len(old) == len(model.observed)
    for old_call, native in zip(old, model.observed, strict=True):
        assert old_call["messages"][0]["content"] == native[0].content
        assert old_call["messages"] == [_convert_message_to_dict(m) for m in native]
    assert all(schemas == old[0]["tools"] for schemas in model.bound)
    if scenario in {"bad_args", "refusal"}:
        assert any(isinstance(m, ToolMessage) and m.error for m in rows[1]["messages"])


@pytest.mark.parametrize("task_id", ["33", "34"])
def test_original_task_reference_actions_db_and_reward_match(tmp_path, task_id):
    rows = paired_run(
        tmp_path,
        plan_task(task_id) + [AIMessage(content="The update is complete.")],
        task_id=task_id,
    )
    assert rows[0]["reward"]["reward"] == rows[1]["reward"]["reward"] == 1
    assert len(rows[0]["executed"]) == len(TASKS[task_id].evaluation_criteria.actions)


def test_boundary_never_calls_toolnode_and_matches_result_ids(tmp_path):
    env = get_environment()
    agent, model = candidate(env, tmp_path / "graph.sqlite", [tool(), AIMessage(content="Done")])
    state = agent.get_init_state()
    intent, state = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    assert intent.tool_calls[0].id == "c1"
    with pytest.raises(ValueError, match="ID"):
        agent.generate_next_message(ToolMessage(id="wrong", role="tool", content="{}"), state)
    reply = env.get_response(intent.tool_calls[0])
    answer, state = agent.generate_next_message(reply, state)
    assert answer.content == "Done"
    receipt = next(m for m in model.observed[-1] if m.type == "tool")
    assert receipt.tool_call_id == "c1"
    with pytest.raises(ValueError, match="ID"):
        agent.generate_next_message(reply, state)


@pytest.mark.parametrize("error,content", [(True, "Error: timeout"), (False, "{broken")])
def test_uncertain_write_stops_and_never_dispatches_next_batch_call(tmp_path, error, content):
    env = get_environment()
    write = plan_task("33")[-1]
    batch = AIMessage(content="", tool_calls=write.tool_calls + tool(cid="read").tool_calls)
    agent, model = candidate(env, tmp_path / "graph.sqlite", [batch])
    state = agent.get_init_state()
    intent, state = agent.generate_next_message(UserMessage(role="user", content="Update"), state)
    answer, state = agent.generate_next_message(
        ToolMessage(
            id=intent.tool_calls[0].id,
            role="tool",
            content=content,
            error=error,
        ),
        state,
    )
    assert agent.is_stop(answer)
    assert answer.raw_data["acs_stop_reason"] == "state_uncertain"
    assert len(model.observed) == 1
    assert agent.resume(state)[0].raw_data["tool_calls"] == 1


def test_lost_receipt_reopen_never_reemits_write(tmp_path):
    env = get_environment()
    path = tmp_path / "graph.sqlite"
    write = plan_task("33")[-1]
    agent, _ = candidate(env, path, [write])
    state = agent.get_init_state()
    intent, state = agent.generate_next_message(UserMessage(role="user", content="Update"), state)
    reply = env.get_response(intent.tool_calls[0])
    after = env.get_db_hash()
    reopened, _ = candidate(env, path, [AIMessage(content="Complete")])
    unknown, _ = reopened.resume(state)
    assert unknown.raw_data["acs_stop_reason"] == "state_uncertain"
    assert not unknown.tool_calls and env.get_db_hash() == after
    # A genuine externally retained receipt resolves the boundary; no environment replay.
    answer, _ = reopened.generate_next_message(reply, state)
    assert answer.content == "Complete" and env.get_db_hash() == after


def test_model_timeout_is_separate_from_total_deadline_and_resumable(tmp_path):
    env = get_environment()
    agent, model = candidate(
        env,
        tmp_path / "graph.sqlite",
        [AIMessage(content="Complete")],
        model_timeout_s=0.1,
        task_timeout_s=120,
    )
    model.delay_s = 1
    state = agent.get_init_state()
    result, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    assert result.raw_data["acs_stop_reason"] == "llm_error"
    model.delay_s = 0
    assert agent.resume(state)[0].content == "Complete"


@pytest.mark.parametrize("kind", ["model", "tool", "deadline"])
def test_budget_stops_before_excess_execution(tmp_path, kind):
    env = get_environment()
    responses = [tool(), tool(cid="c2"), AIMessage(content="not reached")]
    agent, model = candidate(
        env,
        tmp_path / "graph.sqlite",
        responses,
        max_model_calls=1 if kind == "model" else 6,
        max_tool_calls=1 if kind == "tool" else 12,
        task_timeout_s=0.01 if kind == "deadline" else 120,
    )
    state = agent.get_init_state()
    if kind == "deadline":
        import time

        time.sleep(0.02)
    result, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    if result.tool_calls:
        result, _ = agent.generate_next_message(env.get_response(result.tool_calls[0]), state)
    assert result.raw_data["acs_stop_reason"] == "budget_exceeded"
    assert len(model.observed) == {"model": 1, "tool": 2, "deadline": 0}[kind]


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_partial_model_response_is_not_a_final_answer(tmp_path, finish):
    env = get_environment()
    agent, _ = candidate(
        env,
        tmp_path / "graph.sqlite",
        [
            AIMessage(
                content="Partial",
                response_metadata={"finish_reason": finish},
            )
        ],
    )
    state = agent.get_init_state()
    result, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    assert result.raw_data["acs_stop_reason"] == "adapter_protocol_error"


def test_file_lock_blocks_other_worker(tmp_path):
    import fcntl

    env = get_environment()
    agent, _ = candidate(env, tmp_path / "graph.sqlite", [])
    with agent.path.with_suffix(".sqlite.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            agent.get_init_state()


def test_official_factory_and_multiple_user_turns(tmp_path):
    from functools import partial

    from tau2.registry import registry
    from tau2.runner.build import build_agent
    from tau_langgraph_agent import create_langgraph_agent

    env = get_environment()
    model = Decisions(responses=[AIMessage(content="First"), AIMessage(content="Second")])
    registry.register_agent_factory(
        partial(create_langgraph_agent, checkpoint_path=tmp_path / "factory.sqlite", model=model),
        "offline_langgraph_contract",
    )
    agent = build_agent(
        "offline_langgraph_contract", env, llm="offline", llm_args={}, task=TASKS["33"]
    )
    state = agent.get_init_state([AssistantMessage.text("Official greeting")])
    assert (
        agent.generate_next_message(UserMessage(role="user", content="One"), state)[0].content
        == "First"
    )
    assert (
        agent.generate_next_message(UserMessage(role="user", content="Two"), state)[0].content
        == "Second"
    )
    assert [m.content for m in model.observed[-1]][-3:] == ["One", "First", "Two"]


@pytest.mark.parametrize(
    "phase", ["model_checkpoint", "dispatched", "executed", "uncertain_receipt"]
)
def test_real_process_exit_and_recovery(tmp_path, phase):
    """A child really exits; Graph and external RetailDB are restored independently."""
    from tau2.domains.retail.data_model import RetailDB

    env = get_environment()
    path = tmp_path / "crash.sqlite"
    write = plan_task("33")[-1]
    batch = AIMessage(content="", tool_calls=write.tool_calls + tool(cid="next").tool_calls)
    agent, _ = candidate(env, path, [batch if phase == "uncertain_receipt" else write])
    state = agent.get_init_state()
    before = env.get_db_hash()
    pid = os.fork()
    if pid == 0:
        try:
            if phase == "model_checkpoint":
                agent._dispatch = lambda *args: os._exit(71)
            if phase == "uncertain_receipt":
                agent._stopped = lambda *args: os._exit(71)
            intent, _ = agent.generate_next_message(
                UserMessage(role="user", content="Update"), state
            )
            (tmp_path / "intent.json").write_text(intent.model_dump_json())
            if phase == "dispatched":
                os._exit(71)
            reply = env.get_response(intent.tool_calls[0])
            (tmp_path / "external_db.json").write_text(env.tools.db.model_dump_json())
            (tmp_path / "receipt.json").write_text(reply.model_dump_json())
            if phase == "executed":
                os._exit(71)
            reply = reply.model_copy(update={"error": True, "content": "Error: timeout"})
            agent.generate_next_message(reply, state)
        except BaseException:
            import traceback

            (tmp_path / "child_error.txt").write_text(traceback.format_exc())
        os._exit(72)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 71, (tmp_path / "child_error.txt").read_text()
    restored, model = candidate(env, path, [AIMessage(content="Recovered")])
    if phase == "model_checkpoint":
        # No dispatch journal exists: the committed model intent may be sent once.
        intent, _ = restored.resume(state)
        assert intent.tool_calls[0].id == write.tool_calls[0]["id"]
        answer, _ = restored.generate_next_message(env.get_response(intent.tool_calls[0]), state)
        assert answer.content == "Recovered" and len(model.observed) == 1
    else:
        unknown, _ = restored.resume(state)
        assert unknown.raw_data["acs_stop_reason"] == "state_uncertain"
        assert not unknown.tool_calls and not model.observed
        assert env.get_db_hash() == before  # Graph never restored or wrote this DB.
        if phase == "executed":
            external = get_environment(
                db=RetailDB.model_validate_json((tmp_path / "external_db.json").read_text())
            )
            after = external.get_db_hash()
            assert after != before
            receipt = ToolMessage.model_validate_json((tmp_path / "receipt.json").read_text())
            assert restored.generate_next_message(receipt, state)[0].content == "Recovered"
            assert external.get_db_hash() == after  # No write replay.
        elif phase == "uncertain_receipt":
            assert restored._load(state)["stop_reason"] == "state_uncertain"
            assert restored._load(state)["tool_calls"] == 1


def test_graph_receipt_projection_crash_is_idempotent(tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    env = get_environment()
    path = tmp_path / "projection.sqlite"
    agent, _ = candidate(env, path, [tool(), AIMessage(content="Final")])
    state = agent.get_init_state()
    intent, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    original = AsyncSqliteSaver.aput

    async def interrupted(saver, config, checkpoint, metadata, versions):
        result = await original(saver, config, checkpoint, metadata, versions)
        if metadata.get("source") == "update":
            raise RuntimeError("exit after native projection")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSqliteSaver, "aput", interrupted)
        with pytest.raises(RuntimeError, match="projection"):
            agent.generate_next_message(env.get_response(intent.tool_calls[0]), state)
    reopened, model = candidate(env, path, [AIMessage(content="Recovered")])
    assert reopened.resume(state)[0].content == "Recovered"
    receipts = [m for m in model.observed[-1] if m.type == "tool"]
    assert len(receipts) == 1 and receipts[0].tool_call_id == "c1"


@pytest.mark.parametrize("kind", ["duplicate", "empty", "mixed", "malformed"])
def test_invalid_model_calls_never_reach_official_execution(tmp_path, kind):
    env = get_environment()
    call = tool()
    messages = {
        "duplicate": AIMessage(content="", tool_calls=call.tool_calls * 2),
        "empty": tool(cid=""),
        "mixed": AIMessage(content="Also a reply", tool_calls=call.tool_calls),
        "malformed": AIMessage(
            content="",
            invalid_tool_calls=[
                {"id": "bad", "name": "get_order_details", "args": "{", "error": "invalid JSON"}
            ],
        ),
    }
    agent, _ = candidate(env, tmp_path / "bad.sqlite", [messages[kind]])
    state = agent.get_init_state()
    result, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    if kind == "mixed":
        assert result.tool_calls[0].id == "c1"
        assert result.content == ""
        assert agent._load(state)["tool_calls"] == 1
        return
    assert result.raw_data["acs_stop_reason"] == "adapter_protocol_error"
    assert agent._load(state)["tool_calls"] == 0


def test_task_deadline_during_model_is_terminal(tmp_path):
    env = get_environment()
    agent, model = candidate(
        env,
        tmp_path / "deadline.sqlite",
        [AIMessage(content="Late")],
        model_timeout_s=120,
        task_timeout_s=2,
    )
    model.delay_s = 5
    state = agent.get_init_state()
    result, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    assert result.raw_data["acs_stop_reason"] == "budget_exceeded"
    assert agent._load(state)["stop_reason"] == "budget_exceeded"


@pytest.mark.parametrize("limit", ["max_model_calls", "max_tool_calls"])
def test_paired_budget_final_db_reward_and_calls_match(tmp_path, limit):
    rows = paired_run(
        tmp_path, [tool(), tool(cid="c2"), AIMessage(content="Unreached")], **{limit: 1}
    )
    assert len(rows[0]["executed"]) == 1
    assert rows[0]["messages"][-1].raw_data["acs_stop_reason"] in {"max_steps", "budget_exceeded"}
    assert rows[1]["messages"][-1].raw_data["acs_stop_reason"] == "budget_exceeded"


def test_concurrent_worker_and_policy_mismatch_rejected(tmp_path):
    env = get_environment()
    path = tmp_path / "shared.sqlite"
    first, _ = candidate(env, path, [])
    second, _ = candidate(env, path, [])
    with first._locked(), pytest.raises(BlockingIOError):
        second.get_init_state()
    state = first.get_init_state()
    different = LangGraphAgent(
        env.get_tools(), "Changed policy", checkpoint_path=path, model=Decisions()
    )
    with pytest.raises(ValueError, match="incompatible"):
        different.resume(state)


@pytest.mark.parametrize(
    "fault",
    [
        {"error": True, "content": "Error: timeout"},
        {"error": False, "content": "{broken"},
    ],
)
def test_paired_uncertain_write_preserves_real_db_and_stops_batch(tmp_path, fault):
    write = plan_task("33")[-1]
    rows = paired_run(
        tmp_path,
        [AIMessage(content="", tool_calls=write.tool_calls + tool(cid="next").tool_calls)],
        receipt_fault=fault,
    )
    assert len(rows[0]["executed"]) == 1
    for row in rows:
        assert row["messages"][-1].raw_data["acs_stop_reason"] == "state_uncertain"
        assert row["termination"] == "agent_stop"


def test_model_connection_interruption_can_resume_without_tool_replay(tmp_path):
    env = get_environment()
    agent, model = candidate(
        env,
        tmp_path / "network.sqlite",
        [ConnectionError("interrupted model transport"), tool(), AIMessage(content="Recovered")],
    )
    state = agent.get_init_state()
    answer, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    assert answer.raw_data["acs_stop_reason"] == "llm_error"
    assert agent._load(state)["tool_calls"] == 0
    intent, _ = agent.resume(state)
    assert len(model.observed) == 2
    assert (
        agent.generate_next_message(env.get_response(intent.tool_calls[0]), state)[0].content
        == "Recovered"
    )


def test_incomplete_history_or_new_user_while_pending_rejected(tmp_path):
    env = get_environment()
    agent, _ = candidate(env, tmp_path / "history.sqlite", [tool()])
    intent = AssistantMessage.text(
        "",
        tool_calls=[ToolCall(id="missing", name="get_order_details", arguments={})],
    )
    with pytest.raises(ValueError, match="incomplete history"):
        agent.get_init_state([intent])
    with pytest.raises(ValueError, match="Unexpected historical"):
        agent.get_init_state([ToolMessage(id="wrong", role="tool", content="{}")])
    state = agent.get_init_state()
    agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    with pytest.raises(ValueError, match="pending"):
        agent.generate_next_message(UserMessage(role="user", content="Another"), state)


def test_final_response_survives_exit_after_journal_commit(tmp_path):
    env = get_environment()
    path = tmp_path / "final.sqlite"
    agent, _ = candidate(env, path, [AIMessage(content="Durable final answer")])
    state = agent.get_init_state()
    pid = os.fork()
    if pid == 0:
        save = agent._save

        def exit_after_final(state, record):
            save(state, record)
            if record.get("final_reply"):
                os._exit(71)

        agent._save = exit_after_final
        agent.generate_next_message(UserMessage(role="user", content="Check"), state)
        os._exit(72)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 71
    restored, model = candidate(env, path, [])
    assert restored.resume(state)[0].content == "Durable final answer"
    assert not model.observed


@pytest.mark.parametrize(
    "error", [ConnectionError("request lost"), TimeoutError("request timeout")]
)
def test_paired_model_interruption_has_same_official_stop_db_and_reward(tmp_path, error):
    rows = paired_run(tmp_path, [error])
    for row in rows:
        assert not row["executed"]
        assert row["termination"] == "agent_stop"
        assert row["messages"][-1].raw_data["acs_stop_reason"] == "llm_error"
    assert rows[0]["model_requests"] == rows[1]["model_requests"]


def test_model_cannot_reuse_an_executed_call_id(tmp_path):
    env = get_environment()
    agent, _ = candidate(env, tmp_path / "reuse.sqlite", [tool(), tool()])
    state = agent.get_init_state()
    intent, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
    result, _ = agent.generate_next_message(env.get_response(intent.tool_calls[0]), state)
    assert result.raw_data["acs_stop_reason"] == "adapter_protocol_error"
    assert not result.tool_calls and agent._load(state)["tool_calls"] == 1


def test_real_chatopenai_transport_uses_one_loop_across_official_receipts(tmp_path):
    import httpx
    from langchain_openai import ChatOpenAI

    seen = []

    async def transport(request):
        loop = asyncio.get_running_loop()
        if seen:
            assert loop is seen[0], "HTTP model transport changed event loops"
        seen.append(loop)
        message = (
            {"role": "assistant", "content": "Done"}
            if len(seen) == 2
            else {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "http-1",
                        "type": "function",
                        "function": {
                            "name": "get_order_details",
                            "arguments": '{"order_id":"#W1845024"}',
                        },
                    }
                ],
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "offline",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "stop" if len(seen) == 2 else "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    model = ChatOpenAI(
        model="offline",
        api_key="offline-fixture",
        http_async_client=client,
        max_retries=0,
        use_responses_api=False,
    )
    env = get_environment()
    agent = LangGraphAgent(
        env.get_tools(), env.get_policy(), checkpoint_path=tmp_path / "http.sqlite", model=model
    )
    state = agent.get_init_state()
    try:
        intent, _ = agent.generate_next_message(UserMessage(role="user", content="Check"), state)
        assert intent.tool_calls[0].id == "http-1"
        reply, _ = agent.generate_next_message(env.get_response(intent.tool_calls[0]), state)
        assert reply.content == "Done" and len(seen) == 2
    finally:
        agent.stop(state=state)
    assert client.is_closed
