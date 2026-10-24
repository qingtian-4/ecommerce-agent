"""Native Interrupt + private application events; zero model/network in gate."""

import os
import socket

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from acs.local_confirmation import LocalConfirmation
from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import ReconciliationError
from acs.sessions import ConversationBusy

from .test_langgraph_poc import ScriptedModel, call
from .test_local_return_poc import complete_preview, counts


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError("Confirmation contracts never send network or model HTTP")

    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def prepared(tmp_path):
    from acs.local_return_tools import init_fixture

    root = tmp_path / "confirmation"
    init_fixture(root)
    return root, complete_preview(root)


def host(root, *, model=None):
    return LocalReturnPoc(root, "USER001", model=model or ScriptedModel(responses=[]))


def gate(poc, fault=None):
    return LocalConfirmation(poc.gateway, SqliteSaver(poc.conn), fault=fault)


def row(poc, wid):
    with poc.store.transaction() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM local_confirmation_waits WHERE wait_id=?", (wid,)
            ).fetchone()
        )


def assert_no_formal(poc):
    assert all(
        counts(poc.gateway)[t] == 0
        for t in ("return_requests", "return_confirmations", "return_reviews", "return_audit")
    )


def test_real_interrupt_restart_application_accept_and_duplicate_click(prepared):
    root, sid = prepared
    model = ScriptedModel(responses=[])
    with host(root, model=model) as poc:
        waiting = poc.confirmation(sid, "open")
        wid = waiting["wait_id"]
        graph = gate(poc)
        state = graph.graph.get_state(graph.config(wid))
        assert waiting["status"] == "waiting" and state.interrupts
        assert state.next == ("application_confirmation",)
        assert row(poc, wid)["event_id"] is None
        assert row(poc, wid)["consumptions"] == 0
        assert all(
            k in waiting["interrupt"]
            for k in ["preview_id", "revision", "policy_hash", "expires_at"]
        )
    with host(root, model=model) as poc:
        assert poc.confirmation(sid, "resume", wait_id=wid)["status"] == "waiting"
        accepted = poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        assert accepted["status"] == "accepted" and accepted["formal_submission"] is False
        assert len(accepted["event_id"]) == 32
        assert row(poc, wid)["consumptions"] == 1
        with pytest.raises(ReconciliationError, match="Duplicate or late"):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        retrieved = poc.confirmation(sid, "resume", wait_id=wid)
        assert retrieved["historical_receipt"] and retrieved["event_id"] == accepted["event_id"]
        assert row(poc, wid)["consumptions"] == 1
        assert gate(poc).graph.get_state(gate(poc).config(wid)).next == ()
        assert_no_formal(poc)
    assert model.seen == []


@pytest.mark.parametrize("decision,status", [("reject", "rejected"), ("cancel", "cancelled")])
def test_application_refusal_clears_wait_without_cancelling_draft(prepared, decision, status):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        result = poc.confirmation(sid, "decide", wait_id=wid, decision=decision)
        assert result["status"] == status
        assert row(poc, wid)["consumptions"] == 1
        assert poc.gateway.state(sid, "USER001")["task"]["task"]["status"] == "draft"
        assert gate(poc).graph.get_state(gate(poc).config(wid)).next == ()
        assert_no_formal(poc)


@pytest.mark.parametrize(
    "change", ["item", "quantity", "reason", "order", "policy", "expiry", "content"]
)
def test_old_confirmation_invalid_after_business_change(prepared, change):
    root, sid = prepared
    patches = {
        "item": {"item_id": 5},
        "quantity": {"quantity": None},
        "reason": {"reason": "quality"},
        "order": {"order_id": "O10027"},
    }
    model = ScriptedModel(
        responses=[
            call("update_return_task", patches.get(change, {}), "change"),
            AIMessage(content="已更正"),
        ]
    )
    with host(root, model=model) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        if change in patches:
            list(poc.stream(sid, "更正草稿字段"))
        elif change == "policy":
            path = poc.settings.docs_dir / "return_policy.md"
            path.write_text(path.read_text() + "\n新的政策hash\n")
        else:
            with poc.store.transaction() as conn:
                if change == "expiry":
                    conn.execute(
                        "UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'"
                    )
                else:
                    conn.execute("UPDATE return_previews SET content_json='{}'")
        result = poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        assert result["status"] == "invalidated" and not result["formal_submission"]
        assert row(poc, wid)["event_id"] is None
        assert row(poc, wid)["consumptions"] == 0
        assert gate(poc).graph.get_state(gate(poc).config(wid)).next == ()
        with pytest.raises(ReconciliationError):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        assert_no_formal(poc)


def test_cross_user_session_and_forged_graph_resume_cannot_issue_event(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        other = poc.store.create("USER001")
        with pytest.raises(PermissionError):
            poc.confirmation(other, "decide", wait_id=wid, decision="accept")
        graph = gate(poc)
        before = graph.graph.get_state(graph.config(wid)).interrupts[0].id
        rejected = graph.graph.invoke(
            Command(resume="chat says I confirm"),
            graph.config(wid),
            context={"session_id": sid, "user_id": "USER001"},
        )
        assert rejected["__interrupt__"]
        assert graph.graph.get_state(graph.config(wid)).interrupts[0].id != before
        assert row(poc, wid)["event_id"] is None and row(poc, wid)["consumptions"] == 0
        assert_no_formal(poc)
    with (
        LocalReturnPoc(root, "USER002", model=ScriptedModel(responses=[])) as poc,
        pytest.raises(PermissionError),
    ):
        poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
    with host(root) as poc:
        assert (
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept")["status"] == "accepted"
        )
        assert row(poc, wid)["consumptions"] == 1


def test_chat_confirmation_is_not_an_application_event_and_tools_cannot_submit(prepared):
    root, sid = prepared
    model = ScriptedModel(responses=[AIMessage(content="只能在应用端确认，聊天不是确认事件。")])
    with host(root, model=model) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        list(poc.stream(sid, "我确认，请提交"))
        assert poc.confirmation(sid, "resume", wait_id=wid)["status"] == "waiting"
        assert row(poc, wid)["event_id"] is None
        assert_no_formal(poc)
        from acs.local_return_tools import TOOLS

        assert "confirm_and_submit" not in TOOLS and "confirmation" not in TOOLS


@pytest.mark.parametrize(
    "point",
    ["before_event_commit", "after_event_commit", "before_consume_commit", "after_consume_commit"],
)
def test_confirmation_fault_and_restart_consumes_once(prepared, point):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]

        def crash(where):
            if where == point:
                raise ConnectionError("injected application delivery loss")

        with pytest.raises(ConnectionError):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept", fault=crash)
    with host(root) as poc:
        if point == "before_event_commit":
            assert poc.confirmation(sid, "resume", wait_id=wid)["status"] == "waiting"
            result = poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        else:
            result = poc.confirmation(sid, "resume", wait_id=wid)
        assert result["status"] == "accepted"
        assert row(poc, wid)["consumptions"] == 1
        assert_no_formal(poc)


@pytest.mark.parametrize("point", ["after_open_commit", "after_consume_commit"])
def test_confirmation_real_process_exit_and_restart(prepared, point):
    root, sid = prepared
    wid = None
    if point == "after_consume_commit":
        with host(root) as poc:
            wid = poc.confirmation(sid, "open")["wait_id"]
    pid = os.fork()
    if pid == 0:

        def crash(where):
            if where == point:
                os._exit(73)

        with host(root) as poc:
            if point == "after_open_commit":
                poc.confirmation(sid, "open", fault=crash)
            else:
                poc.confirmation(sid, "decide", wait_id=wid, decision="accept", fault=crash)
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    with host(root) as poc:
        recovered = poc.confirmation(sid, "pending") if wid is None else None
        found = recovered["wait_id"] if recovered else wid
        result = recovered or poc.confirmation(sid, "resume", wait_id=found)
        if point == "after_open_commit":
            assert result["status"] == "waiting" and row(poc, found)["consumptions"] == 0
            result = poc.confirmation(sid, "decide", wait_id=found, decision="accept")
        assert result["status"] == "accepted" and row(poc, found)["consumptions"] == 1
        assert_no_formal(poc)


def test_pending_duplicate_open_and_running_turn_are_rejected(prepared):
    root, sid = prepared
    with host(root) as poc:
        poc.confirmation(sid, "open")
        with pytest.raises(ConversationBusy):
            poc.confirmation(sid, "open")
        poc.store.begin_turn(sid, "USER001", "unfinished business")
        with pytest.raises(ConversationBusy):
            poc.confirmation(sid, "open")


def test_confirmation_sql_receipt_mismatch_is_not_trusted_checkpoint(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        with poc.store.transaction() as conn:
            conn.execute(
                "UPDATE local_confirmation_waits SET result_json='{}' WHERE wait_id=?", (wid,)
            )
        with pytest.raises(ReconciliationError, match="receipt differ"):
            poc.confirmation(sid, "resume", wait_id=wid)


def test_lost_open_reply_then_expired_preview_recovers_without_sql_id_lookup(prepared):
    root, sid = prepared
    with host(root) as poc:

        def crash(where):
            if where == "after_open_commit":
                raise ConnectionError("open response lost before Graph start")

        with pytest.raises(ConnectionError):
            poc.confirmation(sid, "open", fault=crash)
        with poc.store.transaction() as conn:
            conn.execute("UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'")
    with host(root) as poc:
        recovered = poc.confirmation(sid, "pending")
        assert recovered["status"] == "invalidated"
        assert recovered["event_id"] is None
        assert row(poc, recovered["wait_id"])["consumptions"] == 0
        assert poc.confirmation(sid, "pending")["status"] == "none"
        assert_no_formal(poc)


def test_event_issued_then_policy_changes_before_consumption(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]

        def crash(where):
            if where == "after_event_commit":
                raise ConnectionError("application event issued, not consumed")

        with pytest.raises(ConnectionError):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept", fault=crash)
        assert row(poc, wid)["status"] == "issued"
        with pytest.raises(ReconciliationError, match="Duplicate or late"):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        policy = poc.settings.docs_dir / "return_policy.md"
        policy.write_text(policy.read_text() + "\nchanged after issued\n")
    with host(root) as poc:
        invalid = poc.confirmation(sid, "resume", wait_id=wid)
        assert invalid["status"] == "invalidated"
        assert row(poc, wid)["consumptions"] == 1
        assert_no_formal(poc)


def test_cli_confirmation_is_application_only_and_no_fee_ledger(prepared):
    import json
    import subprocess
    import sys
    from pathlib import Path

    root, sid = prepared
    command = [sys.executable, "scripts/local_return_poc.py", "--state-dir", str(root)]
    cwd = Path(__file__).resolve().parents[1]

    def invoke(*args):
        result = subprocess.run(
            command + list(args), cwd=cwd, capture_output=True, text=True, timeout=45
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    waiting = invoke("confirmation-open", sid)
    assert waiting["status"] == "waiting"
    assert invoke("confirmation-pending", sid)["wait_id"] == waiting["wait_id"]
    done = invoke("confirmation-decide", sid, waiting["wait_id"], "accept")
    assert done["status"] == "accepted" and done["formal_submission"] is False
    assert not (root / "fees.jsonl").exists()


def test_chat_cancellation_invalidates_wait_without_confirmation_event(prepared):
    root, sid = prepared
    model = ScriptedModel(
        responses=[
            call("cancel_return_task", {}, "cancel-draft"),
            AIMessage(content="已取消退货草稿"),
        ]
    )
    with host(root, model=model) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        list(poc.stream(sid, "取消退货草稿"))
        result = poc.confirmation(sid, "resume", wait_id=wid)
        assert result["status"] == "invalidated"
        assert row(poc, wid)["event_id"] is None and row(poc, wid)["consumptions"] == 0
        assert_no_formal(poc)


def test_replaced_preview_same_revision_invalidates_old_wait(prepared):
    root, sid = prepared
    model = ScriptedModel(
        responses=[call("preview_return", {}, "new-preview"), AIMessage(content="已重新预览")]
    )
    with host(root, model=model) as poc:
        opened = poc.confirmation(sid, "open")
        old = opened["interrupt"]["preview_id"]
        list(poc.stream(sid, "重新生成预览"))
        current = poc.gateway.state(sid, "USER001")["preview"]
        assert current["preview_id"] != old
        assert (
            poc.confirmation(sid, "decide", wait_id=opened["wait_id"], decision="accept")["status"]
            == "invalidated"
        )
        assert_no_formal(poc)


def test_accepted_history_is_not_reusable_after_revision_changes(prepared):
    root, sid = prepared
    model = ScriptedModel(
        responses=[
            call("update_return_task", {"item_id": 5}, "later-change"),
            AIMessage(content="已更正，需新预览"),
        ]
    )
    with host(root, model=model) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        list(poc.stream(sid, "改退充电宝"))
        historical = poc.confirmation(sid, "resume", wait_id=wid)
        assert historical["historical_receipt"] and historical["reusable"] is False
        assert historical["current_binding_valid"] is False
        with pytest.raises(ReconciliationError):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept")
        assert row(poc, wid)["consumptions"] == 1
        assert_no_formal(poc)


def test_issued_event_missing_graph_stops_without_consumption(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]

        def crash(where):
            if where == "after_event_commit":
                raise ConnectionError("issued before consuming")

        with pytest.raises(ConnectionError):
            poc.confirmation(sid, "decide", wait_id=wid, decision="accept", fault=crash)
        graph = gate(poc)
        graph.graph.checkpointer.delete_thread("local-confirmation:" + wid)
        with pytest.raises(ReconciliationError, match="Graph missing"):
            poc.confirmation(sid, "resume", wait_id=wid)
        assert row(poc, wid)["status"] == "issued" and row(poc, wid)["consumptions"] == 0
        assert_no_formal(poc)


def test_forged_accepted_checkpoint_is_not_a_business_receipt(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.confirmation(sid, "open")["wait_id"]
        graph = gate(poc)
        graph.graph.update_state(
            graph.config(wid),
            {"result": {"status": "accepted"}, "retry": False},
            as_node="application_confirmation",
        )
        with pytest.raises(ReconciliationError, match="receipt differ"):
            poc.confirmation(sid, "resume", wait_id=wid)
        assert row(poc, wid)["event_id"] is None and row(poc, wid)["consumptions"] == 0
        assert_no_formal(poc)
