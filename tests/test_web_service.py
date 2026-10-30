"""Actual FastAPI HTTP/SSE + native Graph + synthetic DB, never paid network."""

from __future__ import annotations

import json
import socket
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from acs.local_submission import init_formal_fixture
from acs.web_service import COOKIE, EventData, create_app

from .test_langgraph_poc import ScriptedModel, call
from .test_local_return_poc import draft

ORIGIN = "http://127.0.0.1:8767"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("Offline Web contracts never send HTTP to a model")
        ),
    )


@pytest.fixture
def setup(tmp_path):
    from langchain_core.messages import AIMessage

    root = tmp_path / "web"
    init_formal_fixture(root)
    models = []

    @contextmanager
    def factory():
        model = ScriptedModel(
            responses=[
                call("update_return_task", draft(), uuid4().hex),
                call("preview_return", {}, uuid4().hex),
                AIMessage(content="隔离预览已生成，尚未申请"),
            ]
        )
        models.append(model)
        yield model

    return root, factory, models


def browser(client):
    assert client.get("/").status_code == 200
    b = client.get("/api/bootstrap").json()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": b["csrf"]}
    response = client.post("/api/sessions", json={}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["session_id"], headers


def post(client, sid, headers, route, **body):
    return client.post(
        f"/api/sessions/{sid}/{route}", json={"request_key": uuid4().hex, **body}, headers=headers
    )


def done(client, sid):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = client.get(f"/api/sessions/{sid}")
        assert result.status_code == 200, result.text
        snapshot = result.json()
        if snapshot["jobs"] and not any(
            j["status"] in {"queued", "running"} for j in snapshot["jobs"]
        ):
            return snapshot
        time.sleep(0.03)
    raise AssertionError("Web job never completed")


def events(client, sid, after=0, **headers):
    response = client.get(f"/api/sessions/{sid}/events?once=true&after={after}", headers=headers)
    assert response.status_code == 200
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def counts(root):
    with sqlite3.connect(root / "business.sqlite") as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("return_requests", "return_confirmations", "return_audit")
        )


def test_http_sse_preview_confirm_sql_receipt_duplicate_and_refresh(setup):
    root, factory, models = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        assert post(client, sid, headers, "messages", text="合成退货预览").status_code == 202
        state = done(client, sid)
        assert state["preview"] and counts(root) == (0, 0, 0)
        before = sum(len(m.seen) for m in models)
        log = events(client, sid)
        assert {e["type"] for e in log} >= {
            "turn_started",
            "tool_requested",
            "tool_completed",
            "turn_completed",
        }
        assert events(client, sid) == log
        assert events(client, sid, **{"Last-Event-ID": str(log[-1]["id"])}) == []
        assert sum(len(m.seen) for m in models) == before
        assert post(client, sid, headers, "confirmation").status_code == 202
        waiting = done(client, sid)["confirmation"]
        assert waiting["status"] == "waiting"
        assert (
            post(
                client, sid, headers, "decision", wait_id=waiting["wait_id"], decision="accept"
            ).status_code
            == 202
        )
        state = done(client, sid)
        assert counts(root) == (1, 1, 1)
        assert state["requests"][0]["status"] == "pending_review"
        receipt = client.get(f"/api/sessions/{sid}/receipts/{waiting['wait_id']}").json()
        assert (
            receipt["verified"]
            and receipt["request"]["request_id"] == state["requests"][0]["request_id"]
        )
        post(client, sid, headers, "decision", wait_id=waiting["wait_id"], decision="accept")
        done(client, sid)
        assert counts(root) == (1, 1, 1)
        assert client.get("/").status_code == 200
        assert client.get("/api/bootstrap").json()["session_id"] == sid
        assert sum(len(m.seen) for m in models) == before
        verified = [e for e in events(client, sid) if e["type"] == "submission_verified"]
        assert verified and all(
            e["data"]["request_id"] == state["requests"][0]["request_id"] for e in verified
        )


def test_return_card_states_and_duplicate_open_is_read_only_conflict(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        initial = client.get("/return-card").json()
        assert initial["state"] == "EXPIRED" and initial["allowed_actions"] == ["refresh"]
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        ready = client.get(f"/api/sessions/{sid}/return-card").json()
        assert ready["state"] == "READY" and ready["allowed_actions"] == ["open", "refresh"]
        opened = post(client, sid, headers, "confirmation")
        assert opened.status_code == 202
        waiting = done(client, sid)["confirmation"]
        card = client.get(f"/api/sessions/{sid}/return-card").json()
        assert card["state"] == "READY"
        assert set(("accept", "reject", "cancel")).issubset(card["allowed_actions"])
        duplicate = post(client, sid, headers, "confirmation")
        assert duplicate.status_code == 409
        assert counts(root) == (0, 0, 0)
        post(client, sid, headers, "decision", wait_id=waiting["wait_id"], decision="reject")
        done(client, sid)
        rejected = client.get(f"/api/sessions/{sid}/return-card").json()
        assert rejected["state"] == "REJECTED"
        assert rejected["allowed_actions"] == ["refresh"]
        assert post(client, sid, headers, "confirmation").status_code == 409


def test_return_card_expired_and_unknown_are_read_only(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        with sqlite3.connect(root / "business.sqlite") as conn:
            conn.execute("UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'")
            conn.commit()
        expired = client.get(f"/api/sessions/{sid}/return-card").json()
        assert expired["state"] == "EXPIRED"
        assert expired["allowed_actions"] == ["refresh"]
        assert post(client, sid, headers, "confirmation").status_code == 409

    def fault(point):
        if point == "before_consume_commit":
            raise ConnectionError("simulated lost response")

    root, factory, _ = setup
    with TestClient(create_app(root, factory, fault=fault), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
        done(client, sid)
        unknown = client.get(f"/api/sessions/{sid}/return-card").json()
        assert unknown["state"] == "UNKNOWN"
        assert unknown["allowed_actions"] == ["refresh"]
        assert counts(root) == (0, 0, 0)


@pytest.mark.parametrize("decision", ["reject", "cancel"])
def test_refuse_cancel_never_submit(setup, decision):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        waiting = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=waiting["wait_id"], decision=decision)
        assert (
            done(client, sid)["confirmation"]["status"]
            == {"reject": "rejected", "cancel": "cancelled"}[decision]
        )
        assert counts(root) == (0, 0, 0)


def test_csrf_host_credentials_extra_authority_and_cross_browser(setup):
    root, factory, _ = setup
    app = create_app(root, factory)
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = browser(client)
        for bad in (
            {},
            {"Origin": "http://evil.test", "X-CSRF-Token": headers["X-CSRF-Token"]},
            {"Origin": ORIGIN, "X-CSRF-Token": "forged"},
        ):
            assert post(client, sid, bad, "messages", text="bad").status_code == 403
        for field in ("user_id", "session_id", "thread_id", "event_id", "db_path"):
            assert (
                post(client, sid, headers, "messages", text="bad", **{field: "forged"}).status_code
                == 422
            )
        assert client.get("/api/bootstrap", headers={"Host": "evil.test"}).status_code == 403
        client.cookies.clear()
        assert client.get(f"/api/sessions/{sid}").status_code == 401
        other_sid, other_headers = browser(client)
        assert other_sid != sid
        assert client.get(f"/api/sessions/{sid}").status_code == 403
        assert post(client, sid, other_headers, "messages", text="cross").status_code == 403
        assert client.get(f"/api/sessions/{sid}/events?once=true").status_code == 403
        assert counts(root) == (0, 0, 0)


def test_request_idempotency_persistent_sse_and_service_restart(setup):
    root, factory, models = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        body = {"text": "预览", "request_key": "same-request-key"}
        first = client.post(f"/api/sessions/{sid}/messages", json=body, headers=headers).json()
        done(client, sid)
        repeat = client.post(f"/api/sessions/{sid}/messages", json=body, headers=headers).json()
        assert first["execution_id"] == repeat["execution_id"] and repeat["replayed"]
        log = events(client, sid)
        cookie = client.cookies.get(COOKIE)
        calls = sum(len(m.seen) for m in models)
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, cookie)
        assert client.get("/api/bootstrap").json()["session_id"] == sid
        assert events(client, sid) == log
        assert client.get(f"/api/sessions/{sid}").json()["preview"]
        assert sum(len(m.seen) for m in models) == calls
        assert counts(root) == (0, 0, 0)


@pytest.mark.parametrize(
    "point,expected", [("before_consume_commit", 0), ("after_consume_commit", 1)]
)
def test_commit_fault_never_claims_failure_or_success_without_sql(setup, point, expected):
    root, factory, _ = setup

    def fault(actual):
        if actual == point:
            raise ConnectionError("lost business response")

    with TestClient(create_app(root, factory, fault=fault), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        waiting = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=waiting["wait_id"], decision="accept")
        snapshot = done(client, sid)
        assert counts(root) == (expected,) * 3
        assert len(snapshot["requests"]) == expected
        log = events(client, sid)
        assert log[-1]["type"] == "turn_failed" and log[-1]["data"]["status"] == "unknown"
        assert not any(e["type"] == "submission_verified" for e in log)
        receipt = client.get(f"/api/sessions/{sid}/receipts/{waiting['wait_id']}").json()
        assert receipt["verified"] == bool(expected)


def test_public_event_allowlist_and_sensitive_text(setup):
    from pydantic import ValidationError

    root, factory, _ = setup
    with pytest.raises(ValidationError):
        EventData(authorization="secret")
    app = create_app(root, factory, secret_values=("private-key-value",))
    with TestClient(app, base_url=ORIGIN) as client:
        sid, _ = browser(client)
        app.state.engine.emit(
            sid,
            "synthetic",
            "turn_failed",
            text="private-key-value abc@example.test 13912345678 Bearer abcdef",
        )
        body = json.dumps(events(client, sid))
        assert not any(
            v in body for v in ("private-key-value", "abc@example.test", "13912345678", "abcdef")
        )


@pytest.mark.parametrize(
    "point", ["after_open_commit", "after_event_commit", "before_consume_commit"]
)
def test_confirmation_http_recovery_after_persisted_wait_or_issued_event(setup, point):
    root, factory, _ = setup
    fired = []

    def fault(actual):
        if actual == point and not fired:
            fired.append(actual)
            raise ConnectionError("injected application exit boundary")

    with TestClient(create_app(root, factory, fault=fault), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        if point != "after_open_commit":
            post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
            wait = done(client, sid)["confirmation"]
            assert wait["status"] == "issued"
        assert counts(root) == (0, 0, 0)
        cookie = client.cookies.get(COOKIE)
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, cookie)
        b = client.get("/api/bootstrap").json()
        headers = {"Origin": ORIGIN, "X-CSRF-Token": b["csrf"]}
        path = f"confirmation/{wait['wait_id']}/resume"
        assert post(client, sid, headers, path).status_code == 202
        state = done(client, sid)
        if point == "after_open_commit":
            assert state["confirmation"]["status"] == "waiting"
            assert counts(root) == (0, 0, 0)
            post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
            done(client, sid)
        assert counts(root) == (1, 1, 1)


@pytest.mark.parametrize("change", ["expiry", "revision", "policy"])
def test_http_stale_confirmation_and_cross_session_decision(setup, change):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        if change == "policy":
            p = root / "docs/return_policy.md"
            p.write_text(p.read_text() + "\npolicy changed\n")
        else:
            with app_store(root).transaction() as conn:
                if change == "expiry":
                    conn.execute(
                        "UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'"
                    )
                else:
                    task = json.loads(
                        conn.execute("SELECT data_json FROM return_tasks").fetchone()[0]
                    )
                    task["revision"] += 1
                    conn.execute("UPDATE return_tasks SET data_json=?", (json.dumps(task),))
        post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
        assert done(client, sid)["confirmation"]["status"] == "invalidated"
        assert counts(root) == (0, 0, 0)
        client.cookies.clear()
        other, other_headers = browser(client)
        assert (
            post(
                client, other, other_headers, "decision", wait_id=wait["wait_id"], decision="accept"
            ).status_code
            == 403
        )


def app_store(root):
    from acs.sessions import SessionStore

    return SessionStore(root / "business.sqlite")


def test_sql_receipt_http_works_with_corrupt_graph_and_database_unavailable_is_unknown(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
        done(client, sid)
        (root / "graph.sqlite").write_bytes(b"deliberate isolated corruption")
        route = f"/api/sessions/{sid}/receipts/{wait['wait_id']}"
        assert client.get(route).json()["verified"]
        assert (
            client.get(f"/api/sessions/{sid}").json()["requests"][0]["status"] == "pending_review"
        )
        db = root / "business.sqlite"
        backup = root / "unavailable.sqlite"
        db.rename(backup)
        try:
            response = client.get(route)
            assert response.status_code == 503
            assert "不可核实" in response.json()["detail"]
            assert not db.exists()
        finally:
            backup.rename(db)
        assert counts(root) == (1, 1, 1)


def test_service_restart_marks_crashed_job_without_automatic_execution(setup):
    root, factory, models = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        cookie = client.cookies.get(COOKIE)
        with client.app.state.engine.tx() as conn:
            conn.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,'running')",
                ("crashed", sid, "crash-key", "message", "{}"),
            )
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, cookie)
        state = client.get(f"/api/sessions/{sid}").json()
        assert state["jobs"][0]["status"] == "interrupted"
        assert events(client, sid)[-1]["data"]["reason"] == "service_interrupted"
        assert models == [] and counts(root) == (0, 0, 0)


def test_terminal_event_and_job_status_are_atomic_in_same_web_transaction(setup):
    root, factory, _ = setup
    app = create_app(root, factory)
    original = app.state.engine._emit
    seen = []

    def observe(conn, sid, jid, kind, **data):
        if kind in {"turn_completed", "turn_failed"}:
            status = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
            assert status in {"completed", "failed", "interrupted"}
            seen.append(status)
        return original(conn, sid, jid, kind, **data)

    app.state.engine._emit = observe
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        assert seen == ["completed"]


@pytest.mark.parametrize(
    "point,expected", [("before_consume_commit", 0), ("after_consume_commit", 1)]
)
def test_actual_service_process_exit_http_confirmation_recovers_from_sql(setup, point, expected):
    import os
    import signal

    root, factory, models = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        token = client.cookies.get(COOKIE)
    pid = os.fork()
    if pid == 0:
        signal.alarm(90)

        def crash(actual):
            if actual == point:
                os._exit(73)

        try:
            with TestClient(create_app(root, factory, fault=crash), base_url=ORIGIN) as client:
                client.cookies.set(COOKIE, token)
                post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
                while True:
                    time.sleep(0.1)
        except BaseException:
            os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    before = len(models)
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, token)
        state = client.get(f"/api/sessions/{sid}").json()
        assert state["jobs"][-1]["status"] == "interrupted"
        assert len(state["requests"]) == expected
        assert counts(root) == (expected,) * 3
        receipt = client.get(f"/api/sessions/{sid}/receipts/{wait['wait_id']}").json()
        assert receipt["verified"] == bool(expected)
        assert len(models) == before
        if not expected:
            assert (
                post(client, sid, headers, f"confirmation/{wait['wait_id']}/resume").status_code
                == 202
            )
            done(client, sid)
        assert counts(root) == (1, 1, 1)


def test_fixed_demo_repeated_inputs_have_fresh_ids_and_restore_history_cursor():
    from langchain_core.messages import HumanMessage, ToolMessage
    from scripts.v3_web_demo import STAGES, FixedDemoModel

    first_user = HumanMessage(content=STAGES[0], id="first-turn")
    first = FixedDemoModel().invoke([first_user])
    second_user = HumanMessage(content=STAGES[0], id="second-turn")
    repeated = FixedDemoModel().invoke([first_user, first, second_user])
    assert first.tool_calls[0]["id"] != repeated.tool_calls[0]["id"]
    resumed = FixedDemoModel().invoke(
        [first_user, first, ToolMessage(content="{}", tool_call_id=first.tool_calls[0]["id"])]
    )
    assert resumed.tool_calls[0]["name"] == "search_knowledge"
    assert resumed.tool_calls[0]["id"] != first.tool_calls[0]["id"]


def test_mismatched_snapshot_cannot_enable_accepting_another_preview(setup, monkeypatch):
    root, factory, _ = setup
    app = create_app(root, factory)
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        stale = app.state.engine.gateway.state(sid, "USER001")
        app.state.engine.gateway.service.preview(sid, "USER001")
        post(client, sid, headers, "confirmation")
        latest = done(client, sid)
        assert latest["preview"]["preview_id"] != stale["preview"]["preview_id"]
        monkeypatch.setattr(app.state.engine.gateway, "state", lambda *a: stale)
        response = client.get(f"/api/sessions/{sid}").json()
        assert response["confirmation"]["preview_id"] != response["preview"]["preview_id"]
        assert response["confirmation"]["status"] == "refresh_required"
        assert counts(root) == (0, 0, 0)


def test_fixed_demo_http_resume_continues_after_committed_tool_receipt_without_replay(setup):
    from scripts.v3_web_demo import STAGES, offline_factory

    root, _, _ = setup
    fired = []

    def fault(point):
        if point == "after_commit" and not fired:
            fired.append(point)
            raise ConnectionError("lost ToolMessage delivery after receipt commit")

    app = create_app(root, offline_factory, fault=fault)
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text=STAGES[0])
        assert done(client, sid)["jobs"][-1]["status"] == "failed"
        assert post(client, sid, headers, "resume").status_code == 202
        state = done(client, sid)
        assert state["jobs"][-1]["status"] == "completed"
        assert state["draft"]["order_id"] == "O10024" and state["draft"]["revision"] == 1
        with app_store(root).transaction() as conn:
            receipts = conn.execute("SELECT call_id,name FROM local_receipts").fetchall()
        assert len(receipts) == 3 and len({r["call_id"] for r in receipts}) == 3
        assert counts(root) == (0, 0, 0)


def test_formal_model_capability_projection_matches_application_confirmation(setup):
    from langchain_core.messages import SystemMessage, ToolMessage

    root, factory, models = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
        seen = models[-1].seen[-1]
        facts = [
            json.loads(m.text.removeprefix("当前应用核验事实："))
            for m in seen
            if isinstance(m, SystemMessage) and m.text.startswith("当前应用核验事实：")
        ][-1]
        assert facts["submission_enabled"] is False
        assert facts["application_confirmation_enabled"] is True
        receipts = [json.loads(m.content) for m in seen if isinstance(m, ToolMessage)]
        notice = next(r["result"]["notice"] for r in receipts if r["result"].get("ready"))
        assert "应用确认" in notice and "不提供提交" not in notice
        assert counts(root) == (0, 0, 0)


def test_preview_replay_preserves_its_authoritative_historical_receipt(setup):
    from acs.local_return_tools import TurnContext
    from acs.local_submission import FormalReturnPoc
    from acs.web_service import WebEngine

    root, _, _ = setup
    with FormalReturnPoc(root, "USER001", model=WebEngine.offline_model()) as poc:
        sid = poc.store.create("USER001")
        tid = poc.store.begin_turn(sid, "USER001", "准备退货草稿")
        with poc.store.transaction() as conn:
            conn.execute("INSERT INTO local_threads VALUES(?,?,?,0)", (sid, uuid4().hex, tid))
        context = TurnContext(sid, "USER001", tid)
        poc.gateway.execute("update_return_task", draft(), "draft", context, expected_revision=0)
        poc.gateway.execute("preview_return", {}, "preview", context, expected_revision=1)
        with poc.store.transaction() as conn:
            row = conn.execute(
                "SELECT result FROM local_receipts WHERE session_id=? AND call_id='preview'", (sid,)
            ).fetchone()
            previous = json.loads(row[0])
            previous["notice"] = "历史运行的能力说明，不改写原始回执"
            conn.execute(
                "UPDATE local_receipts SET result=? WHERE session_id=? AND call_id='preview'",
                (json.dumps(previous), sid),
            )
        replay = poc.gateway.execute("preview_return", {}, "preview", context, expected_revision=1)
        assert replay["replayed"] and replay["result"] == previous


def test_memory_api_lists_and_deletes_only_current_user_facts(setup):
    from acs.memory import MemoryStore

    root, factory, _ = setup
    app = create_app(root, factory)
    store = MemoryStore(app.state.engine.settings.db_path)
    visible = store.add_fact("USER001", "偏好入耳式")
    store.add_fact("USER002", "不应对USER001可见")
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = browser(client)
        response = client.get(f"/api/sessions/{sid}/memory")
        assert response.status_code == 200
        facts = response.json()["facts"]
        assert [fact["text"] for fact in facts] == ["偏好入耳式"]
        assert "不应对USER001可见" not in json.dumps(facts, ensure_ascii=False)

        deleted = client.delete(f"/api/sessions/{sid}/memory/{visible.fact_id}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.get(f"/api/sessions/{sid}/memory").json()["facts"] == []

def test_v3_memory_is_injected_into_model_context(setup, monkeypatch):
    from acs.memory import MemoryStore

    monkeypatch.setenv("ACS_V3_MEMORY", "1")
    root, factory, models = setup
    app = create_app(root, factory)
    MemoryStore(app.state.engine.settings.db_path).add_fact(
        "USER001", "偏好开放式耳机"
    )
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = browser(client)
        post(client, sid, headers, "messages", text="预览")
        done(client, sid)
    sent = "\n".join(str(getattr(message, "content", "")) for message in models[-1].seen[0])
    assert "偏好开放式耳机" in sent
