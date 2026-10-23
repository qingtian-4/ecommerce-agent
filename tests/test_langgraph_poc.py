"""Native graph/SQLite contract tests. Scripted messages are not model quality evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("langchain")

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from pydantic import Field

from acs.cs_tools import ToolRegistry
from acs.langgraph_poc import READ_TOOLS, ReadOnlyPoc
from acs.poc_budget import PocBudget


class ScriptedModel(BaseChatModel):
    responses: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-contract-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ChatResult(generations=[ChatGeneration(message=response)])


def call(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def run(poc, text="查询商品P001", sid="session"):
    return list(poc.stream(sid, text))


def test_native_tools_and_final_checkpoint(settings, tmp_path):
    model = ScriptedModel(responses=[call("get_product", {"product_id": "P001"}),
                                     AIMessage(content="依据商品查询结果回答")])
    db_before = settings.db_path.read_bytes()
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001", model=model) as poc:
        assert set(poc.graph.nodes) >= {"model", "tools"}
        events = run(poc)
        assert events[-1].type == "run_completed"
        assert events[-1].detail["checkpoint_id"]
        result = next(e for e in events if e.type == "tool_finished")
        assert result.detail["trace"][-1]["detail"]["result"]["found"]
        assert poc.snapshot("session").next == ()
        assert len(model.seen) == 2
    assert db_before == settings.db_path.read_bytes()
    saved = [json.loads(line) for line in poc.audit_path.read_text().splitlines()]
    assert [e.event_id for e in events] == [e["event_id"] for e in saved]
    assert len({e.event_id for e in events}) == len(events)


def test_history_correction_and_insertion_survive_reopen(settings, tmp_path):
    path = tmp_path / "graph.sqlite"
    prompts = ["P001先看500元内", "改成400元内", "插问O10023物流", "回到之前那款，它多少钱"]
    for prompt in prompts:
        model = ScriptedModel(responses=[AIMessage(content="测试回答")])
        with ReadOnlyPoc(settings, path, "USER001", model=model) as poc:
            run(poc, prompt)
            history = [m.content for m in model.seen[0] if m.type == "human"]
            assert history == prompts[:prompts.index(prompt) + 1]
    # This proves history transport, not pronoun resolution by a real model.


def test_registry_permission_and_identity_injection(settings, tmp_path):
    registry = ToolRegistry(settings)
    other = registry.db.list_orders("USER002")[0]["order_id"]
    model = ScriptedModel(responses=[call("get_order", {"order_id": other}),
                                     AIMessage(content="只能查询本人订单")])
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001",
                     model=model, registry=registry) as poc:
        run(poc, "查询订单")
    payload = json.loads(next(m.content for m in model.seen[-1] if isinstance(m, ToolMessage)))
    assert not payload["found"]
    assert "items" not in payload


@pytest.mark.parametrize("args", [{}, {"product_id": 123},
                                  {"product_id": "P001", "current_user_id": "USER002"}])
def test_bad_arguments_return_error_without_executing(settings, tmp_path, args):
    registry = ToolRegistry(settings)
    executed = []
    original = registry.run

    def spy(*a, **kw):
        executed.append(a)
        return original(*a, **kw)

    registry.run = spy
    model = ScriptedModel(responses=[call("get_product", args),
                                     call("get_product", {"product_id": "P001"}, "c2"),
                                     AIMessage(content="已更正参数并查询")])
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001",
                     model=model, registry=registry) as poc:
        run(poc)
    assert len(executed) == 1
    assert any("参数无效" in m.content for m in model.seen[-1] if m.type == "tool")


def test_write_tools_not_exposed_or_executed(settings, tmp_path):
    model = ScriptedModel(responses=[call("update_return_task", {"order_id": "O10023"}),
                                     AIMessage(content="退货申请已提交，退款已经完成。")])
    before = settings.db_path.read_bytes()
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001", model=model) as poc:
        assert set(READ_TOOLS) == {"list_products", "get_product", "list_orders", "get_order",
                                   "get_logistics", "get_price_history"}
        with pytest.raises(PermissionError):
            run(poc)
    assert before == settings.db_path.read_bytes()
    assert len(model.seen) == 1
    events = [json.loads(line) for line in poc.audit_path.read_text().splitlines()]
    assert not any(e["type"] in {"tool_started", "run_completed"} for e in events)


def test_truncated_model_output_never_completes(settings, tmp_path):
    model = ScriptedModel(responses=[AIMessage(content="尚未写完的回答",
                                               response_metadata={"finish_reason": "length"})])
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001", model=model) as poc:
        with pytest.raises(RuntimeError, match="Incomplete"):
            run(poc)
        assert poc.snapshot("session").next
    events = [json.loads(line) for line in poc.audit_path.read_text().splitlines()]
    assert not any(e["type"] == "run_completed" for e in events)


@pytest.mark.parametrize("close_after_completion", [False, True])
def test_generator_close_terminal_event(settings, tmp_path, close_after_completion):
    model = ScriptedModel(responses=[AIMessage(content="完整回答")])
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001", model=model) as poc:
        stream = poc.stream("session", "查询")
        assert next(stream).type == "run_started"
        if close_after_completion:
            for event in stream:
                if event.type == "run_completed":
                    break
        stream.close()
    events = [json.loads(line) for line in poc.audit_path.read_text().splitlines()]
    terminal = [e["type"] for e in events if e["type"] in {"run_completed", "run_interrupted"}]
    assert terminal == ["run_completed" if close_after_completion else "run_interrupted"]


def test_tool_execution_failure_has_consistent_native_status(settings, tmp_path):
    import sqlite3

    registry = ToolRegistry(settings)

    def fail(*a, **kw):
        raise sqlite3.OperationalError("controlled DB failure")

    registry.db.get_product = fail
    model = ScriptedModel(responses=[call("get_product", {"product_id": "P001"}),
                                     AIMessage(content="查询失败，尚未核实价格")])
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001",
                     model=model, registry=registry) as poc:
        events = run(poc)
        tool_message = next(m for m in poc.snapshot("session").values["messages"]
                            if isinstance(m, ToolMessage))
        assert tool_message.status == "error"
        assert "tool get_product failed" in tool_message.content
    assert next(e for e in events if e.type == "tool_finished").detail["error"]


def test_tool_limit_blocks_excess_calls_without_stranding_session(settings, tmp_path):
    batch = AIMessage(content="", tool_calls=[
        {"id": f"c{i}", "name": "get_product", "args": {"product_id": "P001"}}
        for i in range(13)
    ])
    model = ScriptedModel(responses=[batch, AIMessage(content="有一个查询超过调用上限，未执行"),
                                     AIMessage(content="新问题可以继续")])
    with ReadOnlyPoc(settings, tmp_path / "graph.sqlite", "USER001", model=model) as poc:
        events = run(poc)
        assert sum(e.type == "tool_started" for e in events) == 12
        messages = poc.snapshot("session").values["messages"]
        receipts = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(receipts) == 13
        assert len({m.tool_call_id for m in receipts}) == 13
        assert sum(m.status == "error" for m in receipts) == 1
        assert run(poc, "继续新问题")[-1].type == "run_completed"


def test_model_failure_resume_does_not_replay_finished_read(settings, tmp_path):
    path = tmp_path / "graph.sqlite"
    first = ScriptedModel(responses=[call("get_product", {"product_id": "P001"}),
                                     RuntimeError("controlled provider failure")])
    with ReadOnlyPoc(settings, path, "USER001", model=first) as poc:
        with pytest.raises(RuntimeError, match="controlled provider failure"):
            run(poc)
        assert poc.snapshot("session").next
        with pytest.raises(ValueError, match="resume"):
            run(poc, "新问题")
    second = ScriptedModel(responses=[AIMessage(content="恢复后基于工具结果回答")])
    with ReadOnlyPoc(settings, path, "USER001", model=second) as poc:
        assert run(poc, None)[-1].type == "run_completed"
        with pytest.raises(ValueError, match="没有待恢复"):
            run(poc, None)
    events = [json.loads(line) for line in poc.audit_path.read_text().splitlines()]
    assert sum(e["type"] == "tool_finished" for e in events) == 1
    assert sum(e["type"] == "run_failed" for e in events) == 1


def test_abrupt_process_exit_resumes_pending_native_tool(settings, tmp_path):
    path = tmp_path / "graph.sqlite"
    script = '''
import os, sys
from pathlib import Path
from acs.config import Settings
from acs.langgraph_poc import ReadOnlyPoc
from tests.test_langgraph_poc import ScriptedModel, call
settings = Settings(_env_file=None, db_path=Path(sys.argv[1]))
model = ScriptedModel(responses=[call("get_product", {"product_id": "P001"})])
with ReadOnlyPoc(settings, Path(sys.argv[2]), "USER001", model=model) as poc:
    poc.registry.run = lambda *a, **kw: os._exit(17)
    list(poc.stream("crash", "查P001"))
'''
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root), str(root / "src")]))
    result = subprocess.run([sys.executable, "-c", script, str(settings.db_path), str(path)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 17, result.stderr
    model = ScriptedModel(responses=[AIMessage(content="进程重启后工具查询完成")])
    with ReadOnlyPoc(settings, path, "USER001", model=model) as poc:
        assert "tools" in poc.snapshot("crash").next
        events = run(poc, None, "crash")
        assert events[-1].type == "run_completed"
        assert any(e.type == "tool_finished" for e in events)


def test_owner_check_before_loading_history(settings, tmp_path):
    path = tmp_path / "graph.sqlite"
    with ReadOnlyPoc(settings, path, "USER001",
                     model=ScriptedModel(responses=[AIMessage(content="私人历史")])) as poc:
        run(poc)
    with ReadOnlyPoc(settings, path, "USER002", model=ScriptedModel()) as poc:
        with pytest.raises(PermissionError):
            poc.snapshot("session")
        with pytest.raises(PermissionError):
            run(poc, "尝试访问")
        with pytest.raises(PermissionError):
            run(poc, None)


def test_concurrent_worker_rejected_and_database_separation(settings, tmp_path):
    with pytest.raises(ValueError, match="分开"):
        ReadOnlyPoc(settings, settings.db_path, "USER001", model=ScriptedModel())
    path = tmp_path / "graph.sqlite"
    with (
        ReadOnlyPoc(settings, path, "USER001", model=ScriptedModel()),
        pytest.raises(BlockingIOError),
        ReadOnlyPoc(settings, path, "USER001", model=ScriptedModel()),
    ):
        pass


def test_budget_reserves_before_request_and_survives_restart(tmp_path):
    path = tmp_path / "requests.jsonl"
    meter = PocBudget(path, max_output_tokens=1024)
    invocation = {"invocation_params": {"model": "deepseek-flash"}}
    for _ in range(40):
        meter.on_chat_model_start({}, [[]], run_id=uuid4(), **invocation)
    assert sum(r["reserved_cny"] for r in meter.rows() if r["type"] == "reserved") < 5
    second = PocBudget(path, max_output_tokens=1024)
    with pytest.raises(RuntimeError, match="上限"):
        second.on_chat_model_start({}, [[]], run_id=uuid4(), **invocation)
    with pytest.raises(ValueError, match="仅核准"):
        second.on_chat_model_start({}, [[]], run_id=uuid4())


def test_usage_is_accounted_and_missing_usage_stops(tmp_path):
    meter = PocBudget(tmp_path / "requests.jsonl", max_output_tokens=1024)
    rid = uuid4()
    meter.on_chat_model_start({}, [[]], run_id=rid,
                              invocation_params={"model": "deepseek-flash"})
    message = AIMessage(content="ok", usage_metadata={"input_tokens": 100,
                        "output_tokens": 10, "total_tokens": 110})
    meter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=rid)
    assert meter.rows()[-1]["usage"]["total_tokens"] == 110
    with pytest.raises(RuntimeError, match="usage"):
        meter.on_llm_end(LLMResult(generations=[]), run_id=uuid4())


def test_money_limit_blocks_oversized_request_without_reservation(tmp_path):
    from langchain_core.messages import HumanMessage

    meter = PocBudget(tmp_path / "requests.jsonl", max_output_tokens=1024)
    with pytest.raises(RuntimeError, match="上限"):
        meter.on_chat_model_start({}, [[HumanMessage(content="x" * 1_200_000)]], run_id=uuid4(),
                                  invocation_params={"model": "deepseek-flash"})
    assert not any(r["type"] == "reserved" for r in meter.rows())


def test_real_callback_path_blocks_http_when_budget_exhausted(tmp_path):
    import httpx
    from langchain_openai import ChatOpenAI

    meter = PocBudget(tmp_path / "requests.jsonl", max_output_tokens=1024)
    for _ in range(40):
        meter.on_chat_model_start({}, [[]], run_id=uuid4(),
                                  invocation_params={"model": "deepseek-flash"})
    network_calls = []

    def transport(request):
        network_calls.append(request)
        raise AssertionError("must stop before HTTP")

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        model = ChatOpenAI(model="deepseek-flash", api_key="offline-fixture",
                           http_client=client, callbacks=[meter], max_retries=0)
        with pytest.raises(RuntimeError, match="上限"):
            model.invoke("test budget guard")
    assert network_calls == []
