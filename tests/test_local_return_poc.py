"""Actual native Graph + isolated business SQLite; never network/model payment."""

import json
import socket
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import (
    TOOLS,
    LocalReturnGateway,
    ReconciliationError,
    TurnContext,
    fixture_settings,
    init_fixture,
)
from acs.sessions import SessionStore

from .test_langgraph_poc import ScriptedModel, call


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError("Local return contracts never use network")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / "isolated"
    init_fixture(root)
    return root


def draft():
    return dict(
        order_id="O10024",
        item_id=4,
        quantity=1,
        reason="no_reason",
        condition="sealed",
        secondary_sale_ok=True,
    )


def begin(root, text="我要退货"):
    settings = fixture_settings(root)
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", text)
    with store.transaction() as conn:
        conn.execute("INSERT INTO local_threads VALUES(?,?,?,0)", (sid, "graph-test", tid))
    return LocalReturnGateway(settings), TurnContext(sid, "USER001", tid)


def counts(gateway):
    with closing(gateway.store._connect()) as conn:
        return {
            t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
            for t in [
                "return_requests",
                "return_confirmations",
                "return_reviews",
                "return_audit",
                "return_previews",
                "local_receipts",
            ]
        }


def test_native_multiturn_correction_policy_preview_and_restart(fixture):
    model = ScriptedModel(
        responses=[
            call("get_order", {"order_id": "O10024"}, "order"),
            call("update_return_task", {"order_id": "O10024"}, "draft"),
            AIMessage(content="已存订单，请补商品。"),
        ]
    )
    store = SessionStore(fixture_settings(fixture).db_path)
    sid = store.create("USER001")
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        assert set(poc.graph.nodes) >= {"model", "tools"}
        first = list(poc.stream(sid, "我要退O10024，先保存订单"))[-1]
        assert first.detail["business_state"]["task"]["task"]["order_id"] == "O10024"
    model = ScriptedModel(
        responses=[
            call("get_product", {"product_id": "P001"}, "product"),
            AIMessage(content="商品支持降噪；草稿仍等待补齐。"),
        ]
    )
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        answer = list(poc.stream(sid, "插问这个耳机是否支持降噪"))[-1]
        assert "降噪" in answer.detail["answer"]
        assert answer.detail["business_state"]["task"]["task"]["revision"] == 1
        assert len([m for m in model.seen[0] if m.type == "human"]) == 2
    # Native batch executes serially: preview observes the preceding draft mutation.
    batch = AIMessage(
        content="先补齐再预览",
        tool_calls=[
            {"id": "complete", "name": "update_return_task", "args": draft()},
            {"id": "preview", "name": "preview_return", "args": {}},
        ],
    )
    model = ScriptedModel(responses=[batch, AIMessage(content="已生成隔离预览，尚未申请。")])
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        final = list(poc.stream(sid, "退耳机1件，无理由，未拆封且满足全部二次销售条件，请预览"))[-1]
        old = final.detail["business_state"]["preview"]["preview_id"]
        assert counts(poc.gateway)["return_previews"] == 1
    model = ScriptedModel(
        responses=[
            call("update_return_task", {"item_id": 5}, "correct"),
            AIMessage(content="更正商品，需重新确认数量和状况。"),
        ]
    )
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        final = list(poc.stream(sid, "改退同订单的充电宝"))[-1]
        assert final.detail["business_state"]["preview"] is None
        assert final.detail["business_state"]["task"]["task"]["quantity"] is None
        assert old
        assert all(
            counts(poc.gateway)[t] == 0
            for t in ["return_requests", "return_confirmations", "return_reviews", "return_audit"]
        )


def test_duplicate_call_is_one_write_and_one_preview(fixture):
    g, c = begin(fixture)
    first = g.execute("update_return_task", draft(), "update", c)
    again = g.execute("update_return_task", draft(), "update", c)
    assert first["result"]["task"]["revision"] == again["result"]["task"]["revision"] == 1
    assert again["replayed"]
    one = g.execute("preview_return", {}, "preview", c)
    two = g.execute("preview_return", {}, "preview", c)
    assert one["result"]["preview_id"] == two["result"]["preview_id"]
    assert counts(g)["return_previews"] == 1
    with pytest.raises(ReconciliationError):
        g.execute("update_return_task", {"quantity": 2}, "update", c)


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_lost_receipt_reopen_reconciles_without_double_write(fixture, point):
    g, c = begin(fixture)

    def crash(where):
        if where == point:
            raise ConnectionError("simulated response loss")

    g.fault = crash
    with pytest.raises(ConnectionError):
        g.execute("update_return_task", draft(), "same", c)
    fresh = LocalReturnGateway(g.settings)
    result = fresh.execute("update_return_task", draft(), "same", c)
    assert result["result"]["task"]["revision"] == 1
    assert result["replayed"] == (point == "after_commit")
    assert counts(fresh)["local_receipts"] == 1


@pytest.mark.parametrize(
    "text,allowed",
    [
        ("中止退货草稿", True),
        ("不要取消退货草稿", False),
        ("如果取消退货草稿会怎样", False),
        ("他说“取消退货草稿”是什么意思", False),
        ("取消？", False),
        ("中止退货草稿，同时问运费", False),
    ],
)
def test_cancel_authority_never_inferred_from_model(fixture, text, allowed):
    g, c = begin(fixture, text)
    g.execute("update_return_task", {"order_id": "O10024"}, "draft", c)
    result = g.execute("cancel_return_task", {}, "cancel", c)
    actual = g.store.get_return_task(c.session_id, c.user_id)["task"]
    assert (actual["status"] == "cancelled") == allowed
    assert bool(result["error"]) != allowed


def test_denied_mixed_cancel_does_not_cover_policy_answer(fixture):
    store = SessionStore(fixture_settings(fixture).db_path)
    sid = store.create("USER001")
    model = ScriptedModel(
        responses=[
            call("update_return_task", {"order_id": "O10024"}, "d"),
            AIMessage(content="已保存草稿"),
            call("cancel_return_task", {}, "c"),
            call("search_knowledge", {"query": "七天无理由退货运费"}, "p"),
            AIMessage(content="未取消草稿；七天无理由退货运费由用户承担。"),
        ]
    )
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        list(poc.stream(sid, "我要退O10024"))
        done = list(poc.stream(sid, "如果取消退货草稿会怎样，另外运费谁出"))[-1]
        assert "运费" in done.detail["answer"] and "未取消" in done.detail["answer"]
        assert done.detail["business_state"]["task"]["task"]["status"] == "draft"
        with poc.store.transaction() as conn:
            row = conn.execute("SELECT result FROM local_receipts WHERE call_id='p'").fetchone()
        assert json.loads(row["result"])["found"]
        assert json.loads(row["result"])["documents"]


@pytest.mark.parametrize("change", ["revision", "expiry", "policy"])
def test_old_preview_replay_is_stale_not_regenerated(fixture, change):
    g, c = begin(fixture)
    g.execute("update_return_task", draft(), "d", c)
    g.execute("preview_return", {}, "p", c)
    if change == "revision":
        g.execute("update_return_task", {"condition": "opened"}, "change", c)
    elif change == "expiry":
        with g.store.transaction() as conn:
            conn.execute("UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'")
    else:
        policy = g.settings.docs_dir / "return_policy.md"
        policy.write_text(policy.read_text() + "\n测试政策版本变化\n")
    with pytest.raises(ReconciliationError):
        g.execute("preview_return", {}, "p", c)
    assert counts(g)["return_previews"] == 1


def test_stale_turn_cross_owner_identity_override_and_submission_denied(fixture):
    g, c = begin(fixture)
    with pytest.raises(ValidationError):
        g.execute("update_return_task", dict(draft(), user_id="USER002"), "bad", c)
    with pytest.raises(PermissionError):
        g.execute("confirm_and_submit", {}, "submit", c)
    with pytest.raises(PermissionError):
        g.execute("get_return_task", {}, "foreign", TurnContext(c.session_id, "USER002", c.turn_id))
    g.store.interrupt_turn(c.session_id, c.user_id, c.turn_id)
    with pytest.raises(ReconciliationError):
        g.execute("preview_return", {}, "stale", c)
    assert counts(g)["return_previews"] == 0
    assert "confirm_and_submit" not in TOOLS


def test_new_model_context_has_no_internal_authority(fixture):
    store = SessionStore(fixture_settings(fixture).db_path)
    sid = store.create("USER001")
    model = ScriptedModel(
        responses=[
            call("update_return_task", {"order_id": "O10024"}, "d"),
            AIMessage(content="已保存"),
        ]
    )
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        list(poc.stream(sid, "我要退O10024"))
    tool = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
    text = tool.content
    assert all(x not in text for x in ["user_id", "turn_id", "session_id", sid])
    assert json.loads(text)["result"]["task"]["revision"] == 1


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_real_process_exit_and_native_graph_resume(fixture, point):
    import os

    store = SessionStore(fixture_settings(fixture).db_path)
    sid = store.create("USER001")
    pid = os.fork()
    if pid == 0:

        def crash(where):
            if where == point:
                os._exit(73)

        model = ScriptedModel(
            responses=[
                call("update_return_task", draft(), "durable"),
                AIMessage(content="child must exit before this"),
            ]
        )
        with LocalReturnPoc(fixture, "USER001", model=model, fault=crash) as poc:
            list(poc.stream(sid, "退O10024耳机1件，无理由，满足二次销售条件"))
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    model = ScriptedModel(responses=[AIMessage(content="恢复完成，草稿已保存。")])
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        result = list(poc.stream(sid))[-1]
        assert result.detail["business_state"]["task"]["task"]["revision"] == 1
        assert counts(poc.gateway)["local_receipts"] == 1
        assert len(model.seen) == 1
        receipt = next(m for m in model.seen[0] if isinstance(m, ToolMessage))
        assert json.loads(receipt.content)["replayed"] == (point == "after_commit")


def test_revision_changed_since_model_decision_stops(fixture):
    g, c = begin(fixture)
    g.execute("update_return_task", draft(), "external", c)
    with pytest.raises(ReconciliationError, match="changed after model"):
        g.execute("update_return_task", {"quantity": 2}, "old-decision", c, expected_revision=0)
    assert counts(g)["local_receipts"] == 1


def complete_preview(root):
    store = SessionStore(fixture_settings(root).db_path)
    sid = store.create("USER001")
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "d", "name": "update_return_task", "args": draft()},
                    {"id": "p", "name": "preview_return", "args": {}},
                ],
            ),
            AIMessage(content="已预览"),
        ]
    )
    with LocalReturnPoc(root, "USER001", model=model) as poc:
        list(poc.stream(sid, "我要退货并预览"))
    return sid


def test_completed_resume_refreshes_expired_preview_without_request(fixture):
    sid = complete_preview(fixture)
    store = SessionStore(fixture_settings(fixture).db_path)
    with store.transaction() as conn:
        conn.execute("UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'")
    model = ScriptedModel(responses=[])
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        done = list(poc.stream(sid))[-1]
        assert done.detail["answer_is_historical"]
        assert done.detail["business_state"]["preview"] is None
        assert model.seen == []


@pytest.mark.parametrize("operation", ["new", "resume"])
def test_old_graph_new_completed_database_is_rejected(fixture, operation):
    sid = complete_preview(fixture)
    store = SessionStore(fixture_settings(fixture).db_path)
    tid = store.begin_turn(sid, "USER001", "newer completed turn")
    store.finish_turn(
        sid,
        "USER001",
        tid,
        [
            {"role": "user", "content": "newer completed turn"},
            {"role": "assistant", "content": "newer"},
        ],
        {"answer": "newer"},
    )
    with store.transaction() as conn:
        conn.execute("UPDATE local_threads SET turn_id=? WHERE session_id=?", (tid, sid))
    model = ScriptedModel(responses=[])
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        with pytest.raises(ReconciliationError, match="inconsistent Graph"):
            list(poc.stream(sid, "next" if operation == "new" else None))
        assert model.seen == []


@pytest.mark.parametrize(
    "field,value", [("name", "cancel_return_task"), ("arguments", "{}"), ("result", "{}")]
)
def test_receipt_corruption_detected_before_new_model(fixture, field, value):
    sid = complete_preview(fixture)
    store = SessionStore(fixture_settings(fixture).db_path)
    with store.transaction() as conn:
        conn.execute(f"UPDATE local_receipts SET {field}=? WHERE call_id='d'", (value,))
    with (
        LocalReturnPoc(fixture, "USER001", model=ScriptedModel(responses=[])) as poc,
        pytest.raises(ReconciliationError, match="differs from business receipt"),
    ):
        list(poc.stream(sid, "continue"))


def test_missing_graph_and_incomplete_fixture_refused(fixture):
    complete_preview(fixture)
    (fixture / "graph.sqlite").unlink()
    with (
        pytest.raises(ReconciliationError, match="Graph file missing"),
        LocalReturnPoc(fixture, "USER001", model=ScriptedModel(responses=[])),
    ):
        pass
    store = SessionStore(fixture_settings(fixture).db_path)
    with store.transaction() as conn:
        conn.execute("DROP TRIGGER local_deny_return_requests_INSERT")
    with pytest.raises(ValueError, match="barriers"):
        fixture_settings(fixture)


def test_two_sessions_and_single_worker_lock(fixture):
    store = SessionStore(fixture_settings(fixture).db_path)
    one, two = store.create("USER001"), store.create("USER002")
    model = ScriptedModel(
        responses=[
            call("get_order", {"order_id": "O10024"}, "one"),
            AIMessage(content="本人的订单"),
            call("get_order", {"order_id": "O10024"}, "two"),
            AIMessage(content="无权读取他人订单"),
        ]
    )
    with LocalReturnPoc(fixture, "USER001", model=model) as poc:
        list(poc.stream(one, "查订单"))
        with (
            pytest.raises(BlockingIOError),
            LocalReturnPoc(fixture, "USER002", model=model),
        ):
            pass
        with pytest.raises(PermissionError):
            list(poc.stream(two, "偷看"))
    with LocalReturnPoc(fixture, "USER002", model=model) as poc:
        list(poc.stream(two, "查O10024"))
        receipt = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
        denied = json.loads(receipt.content)["result"]
        assert denied["found"] is False
        assert "items" not in denied and "order" not in denied
        assert counts(poc.gateway)["return_requests"] == 0


def test_cli_offline_demo_is_executable(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = tmp_path / "cli"
    run = subprocess.run(
        [sys.executable, "scripts/local_return_poc.py", "--state-dir", str(root), "demo"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    done = json.loads(run.stdout.splitlines()[-1])
    assert done["type"] == "completed"
    assert done["detail"]["business_state"]["preview"]["ready"]
    assert not (root / "fees.jsonl").exists()


def test_local_cli_sdk_budget_and_private_input_capture(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    import httpx
    from openai import APIConnectionError

    from acs.config import Settings
    from acs.runtime import BudgetExceeded

    path = Path(__file__).resolve().parents[1] / "scripts/local_return_poc.py"
    spec = importlib.util.spec_from_file_location("local_cli_test", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "verify_price", lambda *a: None)
    sends = []

    def respond(request):
        sends.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "mock-sdk",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-flash",
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 2,
                    "provider_metadata": "private.customer@example.test",
                },
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "草稿实验"},
                    }
                ],
            },
        )

    monkeypatch.setattr(cli.httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(respond))
    settings = Settings(_env_file=None, llm_api_key="offline-secret", llm_model="deepseek-flash")
    with cli.paid_model(tmp_path, settings) as (model, ledger):
        for _ in range(32):
            assert model.invoke("isolated synthetic input").text == "草稿实验"
        with pytest.raises((BudgetExceeded, APIConnectionError)):
            model.invoke("must never reach HTTP")
        assert ledger.summary()["requests"] == 32
    with (
        cli.paid_model(tmp_path, settings) as (model, ledger),
        pytest.raises((BudgetExceeded, APIConnectionError)),
    ):
        model.invoke("must still be blocked after process-style reopen")
    assert len(sends) == 32
    saved = [json.loads(p.read_text()) for p in (tmp_path / ".private-evidence").glob("*.json")]
    requests = [r for r in saved if r.get("kind") == "authorized_private_model_request"]
    assert len(requests) == 32
    assert all("isolated synthetic input" in json.dumps(r) for r in requests)
    assert "offline-secret" not in json.dumps(saved)
    from tau_pilot_artifacts import export_ledger

    ledger.event(cli.EPISODE, "response_contract", finish_reason="private.customer@example.test")
    export_ledger(ledger, tmp_path)
    public = (tmp_path / "ledger.public.jsonl").read_text()
    assert "private.customer@example.test" not in public
    assert "<unexpected>" in public
