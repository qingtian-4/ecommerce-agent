"""Synthetic SQLite submission, native interrupts and real crash contracts."""

import json
import os
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from acs.local_confirmation import LocalConfirmation
from acs.local_return_poc import LocalReturnPoc
from acs.local_return_tools import ReconciliationError, init_fixture
from acs.local_submission import (
    KIND,
    FormalConfirmation,
    FormalReturnPoc,
    init_formal_fixture,
)

from .test_langgraph_poc import ScriptedModel, call
from .test_local_return_poc import draft
from .test_return_submission import flow  # noqa: F401


def test_latest_preview_same_timestamp_uses_insertion_order(flow):  # noqa: F811
    _, store, service, sid = flow
    first = service.preview(sid, "USER001")
    second = service.preview(sid, "USER001")
    with store.transaction() as conn:
        conn.execute("UPDATE return_previews SET created_at='2026-10-24T00:00:00+00:00'")
    assert first["preview_id"] != second["preview_id"]
    assert service.latest_preview(sid, "USER001")["preview_id"] == second["preview_id"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("M6 must never send network/model HTTP")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def host(root, model=None):
    return FormalReturnPoc(root, "USER001", model=model or ScriptedModel(responses=[]))


def gate(poc, fault=None):
    return FormalConfirmation(poc.gateway, SqliteSaver(poc.conn), fault=fault)


def state(poc):
    with poc.store.transaction() as conn:
        return {
            table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in (
                "return_requests",
                "return_confirmations",
                "return_audit",
                "return_reviews",
                "local_confirmation_waits",
                "local_submission_receipts",
            )
        }


def atomic(poc, expected):
    rows = state(poc)
    assert [
        len(rows[t])
        for t in (
            "return_requests",
            "return_confirmations",
            "return_audit",
            "local_submission_receipts",
        )
    ] == [expected] * 4
    assert rows["return_reviews"] == []
    assert (
        sum(
            r["consumptions"]
            for r in rows["local_confirmation_waits"]
            if r["event_id"] in {x["event_id"] for x in rows["local_submission_receipts"]}
        )
        == expected
    )
    return rows


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "formal"
    init_formal_fixture(root)
    model = ScriptedModel(
        responses=[
            call("update_return_task", draft(), "draft"),
            call("preview_return", {}, "preview"),
            AIMessage(content="隔离预览已生成，未提交"),
        ]
    )
    with host(root, model) as poc:
        sid = poc.store.create("USER001")
        list(poc.stream(sid, "退货并预览"))
        gate(poc)  # Initialize laboratory-only receipt schema, never business records.
        atomic(poc, 0)
    return root, sid


def test_native_interrupt_submit_restart_read_real_status_and_repeated_http(prepared):
    root, sid = prepared
    with host(root) as poc:
        waiting = poc.submission(sid, "open")
        wid = waiting["wait_id"]
        assert gate(poc).graph.get_state(gate(poc).config(wid)).interrupts
        atomic(poc, 0)
    with host(root) as poc:
        result = poc.submission(sid, "decide", wait_id=wid, decision="accept")
        assert result["formal_submission"] and result["simulated"]
        assert result["request"]["status"] == "pending_review"
        rows = atomic(poc, 1)
        assert rows["local_submission_receipts"][0]["event_id"] == result["event_id"]
        assert poc.gateway.service.list_requests("USER001")[0] == result["request"]
        assert poc.gateway.service.list_requests("USER002") == []
        again = poc.submission(sid, "decide", wait_id=wid, decision="accept")
        assert again["request"] == result["request"]
        assert again["graph_mirror"] == "matched"
        assert state(poc) == rows
        with pytest.raises(ReconciliationError):
            poc.submission(sid, "decide", wait_id=wid, decision="cancel")
        with pytest.raises(ReconciliationError, match="No current valid"):
            poc.submission(sid, "open")
    with host(root) as poc:
        assert poc.submission(sid, "lookup", wait_id=wid) == result
        atomic(poc, 1)


@pytest.mark.parametrize("decision,status", [("reject", "rejected"), ("cancel", "cancelled")])
def test_refusal_and_cancel_never_create_application(prepared, decision, status):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        assert poc.submission(sid, "decide", wait_id=wid, decision=decision)["status"] == status
        atomic(poc, 0)


@pytest.mark.parametrize(
    "change", ["order", "item", "quantity", "reason", "policy", "expiry", "content", "cancel"]
)
def test_changed_binding_never_submits(prepared, change):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        if change == "policy":
            path = poc.settings.docs_dir / "return_policy.md"
            path.write_text(path.read_text() + "\nchanged hash\n")
        elif change in {"expiry", "content"}:
            with poc.store.transaction() as conn:
                if change == "expiry":
                    conn.execute(
                        "UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'"
                    )
                else:
                    conn.execute("UPDATE return_previews SET content_json='{}'")
        else:
            patch = {
                "order": {"order_id": "O10027"},
                "item": {"item_id": 5},
                "quantity": {"quantity": None},
                "reason": {"reason": "quality"},
                "cancel": {},
            }[change]
            tid = poc.store.begin_turn(
                sid, "USER001", "取消退货草稿" if change == "cancel" else "更正"
            )
            poc.store.update_return_task(sid, "USER001", tid, patch, cancel=change == "cancel")
            poc.store.interrupt_turn(sid, "USER001", tid)
        result = poc.submission(sid, "decide", wait_id=wid, decision="accept")
        assert result["status"] == "invalidated"
        atomic(poc, 0)


@pytest.mark.parametrize(
    "point",
    [
        "before_event_commit",
        "after_event_commit",
        "after_confirmation_consume",
        "after_request_create",
        "before_consume_commit",
        "after_consume_commit",
    ],
)
def test_real_process_exit_atomic_rollback_or_recovery(prepared, point):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
    pid = os.fork()
    if pid == 0:
        try:
            with host(root) as poc:

                def crash(actual):
                    if actual == point:
                        os._exit(73)

                poc.submission(sid, "decide", wait_id=wid, decision="accept", fault=crash)
        except BaseException:
            os._exit(74)
        os._exit(75)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    with host(root) as poc:
        committed = point == "after_consume_commit"
        rows = atomic(poc, int(committed))
        before = state(poc)
        receipt = poc.submission(sid, "lookup", wait_id=wid)
        assert bool(receipt) == committed
        assert state(poc) == before  # Lookup itself cannot replay an uncertain write.
        if committed:
            recovered = poc.submission(sid, "resume", wait_id=wid)
            assert recovered["graph_mirror"] == "reconciliation_required"
        elif rows["local_confirmation_waits"][0]["status"] == "waiting":
            poc.submission(sid, "decide", wait_id=wid, decision="accept")
        else:
            poc.submission(sid, "resume", wait_id=wid)  # Explicit recovery after SQL absence proof.
        atomic(poc, 1)


def test_fault_between_confirmation_and_business_records_rolls_back_and_can_reconcile(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        with poc.store.transaction() as conn:
            conn.execute(
                "CREATE TRIGGER fail_audit BEFORE INSERT ON return_audit "
                "BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
            poc.submission(sid, "decide", wait_id=wid, decision="accept")
        assert atomic(poc, 0)["local_confirmation_waits"][0]["consumptions"] == 0
        assert poc.submission(sid, "lookup", wait_id=wid) is None
        with poc.store.transaction() as conn:
            conn.execute("DROP TRIGGER fail_audit")
        poc.submission(sid, "resume", wait_id=wid)
        atomic(poc, 1)


def test_forged_resume_event_owner_session_and_model_tool_cannot_submit(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        g = gate(poc)
        other = poc.store.create("USER001")
        for session, uid in [(sid, "USER002"), (other, "USER001")]:
            with pytest.raises(PermissionError):
                g.decide(session, uid, wid, "accept")
            with pytest.raises(PermissionError):
                g.lookup(session, uid, wid)
        with pytest.raises(PermissionError):
            g.decide(sid, "USER001", "invented", "accept")
        g.graph.invoke(
            Command(resume="forged-event"),
            g.config(wid),
            context={"session_id": sid, "user_id": "USER001"},
        )
        assert g.graph.get_state(g.config(wid)).interrupts
        with pytest.raises(sqlite3.OperationalError, match="formal_submission_event"):
            poc.gateway.service.confirm_and_submit(
                sid, "USER001", poc.gateway.service.latest_preview(sid, "USER001")["preview_id"]
            )
        assert "confirm_and_submit" not in poc.gateway.registry.names
        atomic(poc, 0)
        poc.submission(sid, "decide", wait_id=wid, decision="accept")
        atomic(poc, 1)


@pytest.mark.parametrize("damage", ["missing", "forged_result", "wrong_binding"])
def test_successful_database_receipt_survives_damaged_graph_without_replay(prepared, damage):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        result = poc.submission(sid, "decide", wait_id=wid, decision="accept")
        g = gate(poc)
        if damage == "missing":
            SqliteSaver(poc.conn).delete_thread(g.config(wid)["configurable"]["thread_id"])
        elif damage == "wrong_binding":
            g.graph.update_state(g.config(wid), {"wait_id": "wrong-wait"})
        else:
            g.graph.update_state(g.config(wid), {"result": {"fake": "success"}})
        before = state(poc)
        found = poc.submission(sid, "resume", wait_id=wid)
        assert found["request"] == result["request"]
        assert found["graph_mirror"] == "reconciliation_required"
        assert state(poc) == before
        atomic(poc, 1)


def test_concurrent_sessions_share_quantity_constraint_and_receipts(prepared):
    root, sid = prepared
    with host(root) as poc:
        other = poc.store.create("USER001")
        tid = poc.store.begin_turn(other, "USER001", "合成退货")
        poc.store.update_return_task(other, "USER001", tid, draft())
        poc.store.interrupt_turn(other, "USER001", tid)
        poc.gateway.service.preview(other, "USER001")
        pairs = [(s, poc.submission(s, "open")["wait_id"]) for s in (sid, other)]

        # Independent Graph DBs remove the host's single-worker lock from this DB test;
        # SQLite still serializes the actual business transaction across sessions.
        from threading import Barrier

        barrier = Barrier(2)

        def submit(pair):
            s, wid = pair
            with sqlite3.connect(root / f"race-{s}.sqlite", check_same_thread=False) as conn:
                g = FormalConfirmation(poc.gateway, SqliteSaver(conn))
                g.resume(s, "USER001", wid)
                barrier.wait(timeout=60)
                return g.decide(s, "USER001", wid, "accept")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, pairs))
        assert sorted(r["status"] for r in results) == ["accepted", "invalidated"]
        atomic(poc, 1)


def test_formal_and_draft_fixtures_cannot_be_upgraded_or_confused(tmp_path):
    draft_root = tmp_path / "draft-only"
    init_fixture(draft_root)
    with pytest.raises(ValueError):
        host(draft_root)
    marker = json.loads((draft_root / "fixture.json").read_text())
    marker["kind"] = KIND
    (draft_root / "fixture.json").write_text(json.dumps(marker))
    with pytest.raises(ValueError):
        host(draft_root)
    with sqlite3.connect(draft_root / "business.sqlite") as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE "
                "type='trigger' AND name LIKE 'local_deny_%'"
            ).fetchone()[0]
            == 12
        )
    formal = tmp_path / "new-formal"
    init_formal_fixture(formal)
    with pytest.raises(ValueError):
        LocalReturnPoc(formal, "USER001", model=ScriptedModel(responses=[]))
    with pytest.raises(FileExistsError):
        init_formal_fixture(formal)
    with host(formal) as poc, poc.store.transaction() as conn:
        for sql in [
            "DELETE FROM logistics",
            "UPDATE orders SET status='cancelled'",
            "DELETE FROM return_requests",
            "DELETE FROM return_reviews",
        ]:
            # BEFORE row triggers require an actual row to run. Orders/logistics are seeded.
            if "return_" not in sql:
                with pytest.raises(sqlite3.IntegrityError, match="trusted submission"):
                    conn.execute(sql)


def test_old_accepted_confirmation_cannot_be_reused_as_submission(prepared):
    root, sid = prepared
    with host(root) as poc:
        old = LocalConfirmation(poc.gateway, SqliteSaver(poc.conn))
        wid = old.open(sid, "USER001")["wait_id"]
        old.decide(sid, "USER001", wid, "accept")
        with pytest.raises(ReconciliationError, match="Unknown submission result"):
            poc.submission(sid, "resume", wait_id=wid)
        assert atomic(poc, 0)["local_confirmation_waits"][0]["consumptions"] == 1
        fresh = poc.submission(sid, "open")["wait_id"]
        assert fresh != wid
        poc.submission(sid, "decide", wait_id=fresh, decision="accept")
        # M5 accepted is not a formal consumption, so count formal receipt relationships directly.
        rows = state(poc)
        assert len(rows["return_requests"]) == len(rows["local_submission_receipts"]) == 1


def test_latest_same_timestamp_invalidates_previous_confirmation(prepared):
    root, sid = prepared
    with host(root) as poc:
        old = poc.submission(sid, "open")["wait_id"]
        latest = poc.gateway.service.preview(sid, "USER001")
        with poc.store.transaction() as conn:
            conn.execute("UPDATE return_previews SET created_at='2026-10-24T00:00:00+00:00'")
        assert (
            poc.gateway.service.latest_preview(sid, "USER001")["preview_id"] == latest["preview_id"]
        )
        assert (
            poc.submission(sid, "decide", wait_id=old, decision="accept")["status"] == "invalidated"
        )
        atomic(poc, 0)


@pytest.mark.parametrize("damage", ["missing_file", "invalid_sqlite"])
def test_readonly_cli_recovers_even_when_entire_graph_file_is_unusable(prepared, damage):
    import hashlib
    import subprocess
    import sys
    from pathlib import Path

    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        result = poc.submission(sid, "decide", wait_id=wid, decision="accept")
    graph = root / "graph.sqlite"
    if damage == "missing_file":
        graph.unlink()  # Only this test's disposable synthetic fixture.
    else:
        graph.write_bytes(b"deliberately corrupt synthetic graph")
    before = hashlib.sha256((root / "business.sqlite").read_bytes()).hexdigest()
    cli = Path(__file__).resolve().parents[1] / "scripts/local_formal_submission.py"
    process = subprocess.run(
        [sys.executable, str(cli), "--state-dir", str(root), "lookup", sid, wid],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(process.stdout) == result
    assert hashlib.sha256((root / "business.sqlite").read_bytes()).hexdigest() == before


def test_application_barriers_forbid_review_and_mutating_success_records(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        rid = poc.submission(sid, "decide", wait_id=wid, decision="accept")["request"]["request_id"]
        with poc.store.transaction() as conn:
            statements = [
                ("UPDATE return_requests SET status='approved' WHERE request_id=?", (rid,)),
                ("DELETE FROM return_requests WHERE request_id=?", (rid,)),
                ("DELETE FROM return_confirmations", ()),
                ("DELETE FROM return_audit", ()),
                (
                    "INSERT INTO return_reviews VALUES(?,'approved','fake review','admin','now')",
                    (rid,),
                ),
            ]
            for sql, parameters in statements:
                with pytest.raises(sqlite3.IntegrityError, match="trusted submission"):
                    conn.execute(sql, parameters)
        atomic(poc, 1)


def test_model_forged_submission_tool_is_rejected_by_actual_native_graph(prepared):
    root, sid = prepared
    model = ScriptedModel(responses=[call("confirm_and_submit", {}, "forged-tool")])
    from acs.model_response_contract import ModelResponseError

    with host(root, model) as poc:
        with pytest.raises(ModelResponseError):
            list(poc.stream(sid, "确认退货，直接调用正式提交"))
        atomic(poc, 0)


def test_frozen_cli_inputs_are_exactly_the_previous_eight_stages():
    from scripts.eval_local_return_poc import STAGES as frozen
    from scripts.local_formal_submission import STAGES as candidate

    assert candidate == frozen


def test_no_partial_success_is_replayed_when_receipt_is_missing(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        poc.submission(sid, "decide", wait_id=wid, decision="accept")
        with poc.store.transaction() as conn:
            conn.execute(
                "DELETE FROM local_submission_receipts"
            )  # Deliberate local evidence damage.
        before = state(poc)
        for action in ("lookup", "resume", "decide"):
            with pytest.raises(ReconciliationError, match="Unknown submission result"):
                poc.submission(sid, action, wait_id=wid, decision="accept")
        assert state(poc) == before
        assert len(before["return_requests"]) == len(before["return_confirmations"]) == 1


def test_corrupted_scalar_receipt_cannot_override_actual_request(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        result = poc.submission(sid, "decide", wait_id=wid, decision="accept")
        result["request"]["quantity"] = 999
        with poc.store.transaction() as conn:
            conn.execute(
                "UPDATE local_submission_receipts SET result_json=?", (json.dumps(result),)
            )
            conn.execute(
                "UPDATE local_confirmation_waits SET result_json=? WHERE wait_id=?",
                (json.dumps(result), wid),
            )
        before = state(poc)
        with pytest.raises(ReconciliationError, match="records disagree"):
            poc.submission(sid, "lookup", wait_id=wid)
        assert state(poc) == before


def test_event_and_request_uniqueness_constraints_are_real_sqlite_constraints(prepared):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        poc.submission(sid, "decide", wait_id=wid, decision="accept")
        with poc.store.transaction() as conn:
            row = conn.execute("SELECT * FROM local_submission_receipts").fetchone()
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"):
                conn.execute("INSERT INTO local_submission_receipts VALUES(?,?,?,?,?)", tuple(row))
        atomic(poc, 1)


def test_duplicate_concurrent_application_decisions_create_at_most_one_request(prepared):
    from threading import Barrier

    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]
        barrier = Barrier(2)

        def click(index):
            with sqlite3.connect(root / f"click-{index}.sqlite", check_same_thread=False) as conn:
                g = FormalConfirmation(poc.gateway, SqliteSaver(conn))
                g.resume(sid, "USER001", wid)
                barrier.wait(timeout=60)
                try:
                    return g.decide(sid, "USER001", wid, "accept")["status"]
                except ReconciliationError as exc:
                    assert "Duplicate or late" in str(exc)
                    return "already_issued"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(click, range(2)))
        assert "accepted" in results and set(results) <= {"accepted", "already_issued"}
        atomic(poc, 1)


@pytest.mark.parametrize("change", ["expiry", "policy", "revision"])
def test_binding_is_revalidated_after_event_issuance_before_atomic_consume(prepared, change):
    root, sid = prepared
    with host(root) as poc:
        wid = poc.submission(sid, "open")["wait_id"]

        def interrupt_after_event(point):
            if point == "after_event_commit":
                raise ConnectionError("application lost event issuance reply")

        with pytest.raises(ConnectionError):
            poc.submission(
                sid, "decide", wait_id=wid, decision="accept", fault=interrupt_after_event
            )
        rows = atomic(poc, 0)
        assert rows["local_confirmation_waits"][0]["status"] == "issued"
        assert rows["local_confirmation_waits"][0]["consumptions"] == 0
        if change == "policy":
            path = poc.settings.docs_dir / "return_policy.md"
            path.write_text(path.read_text() + "\npolicy changed after user accepted\n")
        elif change == "expiry":
            with poc.store.transaction() as conn:
                conn.execute("UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'")
        else:
            tid = poc.store.begin_turn(sid, "USER001", "撤回数量，稍后补充")
            poc.store.update_return_task(sid, "USER001", tid, {"quantity": None})
            poc.store.interrupt_turn(sid, "USER001", tid)
    with host(root) as poc:
        assert poc.submission(sid, "resume", wait_id=wid)["status"] == "invalidated"
        atomic(poc, 0)
