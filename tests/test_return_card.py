"""Additional card projection contracts; M6 remains the business authority."""

import json
import sqlite3
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient

from acs.web_service import COOKIE, create_app

from .test_web_service import ORIGIN, browser, counts, done, post
from .test_web_service import setup as setup


def prepare(client):
    sid, headers = browser(client)
    assert post(client, sid, headers, "messages", text="预览").status_code == 202
    done(client, sid)
    return sid, headers


def card(client, sid):
    response = client.get(f"/api/sessions/{sid}/return-card")
    assert response.status_code == 200, response.text
    return response.json()


def test_open_original_key_replays_after_wait_exists(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = prepare(client)
        url = f"/api/sessions/{sid}/confirmation"
        body = {"request_key": uuid4().hex}
        first = client.post(url, json=body, headers=headers)
        assert first.status_code == 202
        done(client, sid)
        repeat = client.post(url, json=body, headers=headers)
        assert repeat.status_code == 202, repeat.text
        assert repeat.json()["execution_id"] == first.json()["execution_id"]
        assert repeat.json()["replayed"]
        assert post(client, sid, headers, "confirmation").status_code == 409
        with sqlite3.connect(root / "business.sqlite") as c:
            assert c.execute("SELECT COUNT(*) FROM local_confirmation_waits").fetchone()[0] == 1
        assert counts(root) == (0, 0, 0)


def test_new_preview_after_rejection_is_not_blocked_by_historical_wait(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = prepare(client)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="reject")
        done(client, sid)
        assert card(client, sid)["state"] == "REJECTED"
        post(client, sid, headers, "messages", text="再次生成新预览")
        done(client, sid)
        latest = card(client, sid)
        assert latest["state"] == "READY" and "open" in latest["allowed_actions"]
        assert latest["confirmation"]["is_current"] is False
        assert latest["confirmation"]["status"] == "rejected"
        assert latest["confirmation"]["preview_id"] != latest["preview"]["preview_id"]
        assert counts(root) == (0, 0, 0)


def test_card_sanitizes_fields_and_get_never_writes_sql_or_graph(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, _ = prepare(client)
        engine = client.app.state.engine
        # Exercise the same recursive redaction boundary used by snapshot/events.
        engine.secret_values += ("SoundCore",)

        def dump():
            result = {}
            for filename in ("business.sqlite", "web.sqlite", "graph.sqlite"):
                with sqlite3.connect(root / filename) as c:
                    result[filename] = list(c.iterdump())
            return result

        before = dump()
        result = card(client, sid)
        assert "SoundCore" not in json.dumps(result)
        assert client.get("/return-card").status_code == 200
        assert before == dump()


def test_running_and_crashed_decision_cannot_offer_accept_again(setup):
    root, factory, _ = setup
    reached, release = Event(), Event()

    def fault(point):
        if point == "before_event_commit":
            reached.set()
            assert release.wait(30)
            raise ConnectionError("application event response lost")

    app = create_app(root, factory, fault=fault)
    with TestClient(app, base_url=ORIGIN) as client:
        sid, headers = prepare(client)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        try:
            post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
            assert reached.wait(20)
            result = card(client, sid)
            assert result["state"] == "UNKNOWN"
            assert result["allowed_actions"] == ["refresh"]
        finally:
            release.set()
        done(client, sid)
        assert card(client, sid)["state"] == "UNKNOWN"
        assert post(
            client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept"
        ).status_code == 409
        assert counts(root) == (0, 0, 0)


def test_sql_inconsistency_is_unknown_and_graph_corruption_does_not_hide_success(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = prepare(client)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        post(client, sid, headers, "decision", wait_id=wait["wait_id"], decision="accept")
        done(client, sid)
        (root / "graph.sqlite").write_bytes(b"unreadable graph mirror")
        result = card(client, sid)
        assert result["state"] == "PENDING_REVIEW" and result["authoritative"]
        assert counts(root) == (1, 1, 1)
        # Damage only an isolated synthetic idempotency receipt, without disabling barriers.
        with sqlite3.connect(root / "business.sqlite") as c:
            c.execute("UPDATE local_submission_receipts SET result_json='{}'")
        result = card(client, sid)
        assert result["state"] == "UNKNOWN" and result["request"] is None
        assert result["allowed_actions"] == ["refresh"]
        assert counts(root) == (1, 1, 1)


def test_database_unavailable_card_is_explicit_unknown_and_does_not_create_db(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, _ = prepare(client)
        db = root / "business.sqlite"
        held = root / "business-held.sqlite"
        db.rename(held)
        try:
            for route in ("/return-card", f"/api/sessions/{sid}/return-card"):
                response = client.get(route)
                assert response.status_code == 503
                assert response.json()["state"] == "UNKNOWN"
                assert response.json()["allowed_actions"] == ["refresh"]
                assert not db.exists()
        finally:
            held.rename(db)
        assert counts(root) == (0, 0, 0)


def test_card_cross_browser_and_business_owner_changes_are_denied(setup):
    root, factory, _ = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = prepare(client)
        cookie = client.cookies.get(COOKIE)
        client.cookies.clear()
        other, other_headers = browser(client)
        assert other != sid
        assert client.get(f"/api/sessions/{sid}/return-card").status_code == 403
        assert post(client, sid, other_headers, "confirmation").status_code == 403
        client.cookies.set(COOKIE, cookie)
        # The synthetic server identity is fixed. Changing database ownership cannot
        # grant it access to a different real business user, even with a valid cookie.
        with sqlite3.connect(root / "business.sqlite") as conn:
            conn.execute("UPDATE conversations SET user_id='USER002' WHERE session_id=?", (sid,))
        assert client.get(f"/api/sessions/{sid}/return-card").status_code == 403
        assert client.get("/return-card").status_code == 403
        assert post(client, sid, headers, "confirmation").status_code == 403
        assert counts(root) == (0, 0, 0)


def test_interrupted_decision_restores_unknown_without_auto_dispatch(setup):
    root, factory, models = setup
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        sid, headers = prepare(client)
        post(client, sid, headers, "confirmation")
        wait = done(client, sid)["confirmation"]
        cookie = client.cookies.get(COOKIE)
        # Durable queued work without a worker models exit before the application
        # event commits. Service startup marks it interrupted; it cannot auto-replay.
        with client.app.state.engine.tx() as conn:
            conn.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,'queued')",
                (uuid4().hex, sid, uuid4().hex, "decide", json.dumps({
                    "wait_id": wait["wait_id"], "decision": "accept"
                })),
            )
    seen = sum(len(m.seen) for m in models)
    with TestClient(create_app(root, factory), base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, cookie)
        result = card(client, sid)
        assert result["state"] == "UNKNOWN" and result["allowed_actions"] == ["refresh"]
        assert counts(root) == (0, 0, 0)
        assert sum(len(m.seen) for m in models) == seen
