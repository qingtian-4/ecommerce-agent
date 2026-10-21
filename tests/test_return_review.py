"""Review authorization, final states, quantities and atomic evidence with no API."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from pydantic import SecretStr

from acs.cs_tools import ToolRegistry
from acs.returns import ReturnService
from acs.sessions import SessionStore, TaskValidationError

from .test_return_submission import flow as flow
from .test_return_submission import image_bytes, write


@pytest.fixture
def pending(flow):
    settings, store, _, sid = flow
    settings = settings.model_copy(update={"admin_username": "reviewer",
                                           "admin_password": SecretStr("synthetic-review-pass")})
    service = ReturnService(settings, store)
    request = service.confirm_and_submit(
        sid, "USER001", service.preview(sid, "USER001")["preview_id"],
    )
    return settings, store, service, sid, request["request_id"]


def review_count(settings):
    with closing(sqlite3.connect(settings.db_path)) as conn:
        return (conn.execute("SELECT count(*) FROM return_reviews").fetchone()[0],
                conn.execute("SELECT count(*) FROM return_audit").fetchone()[0])


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_review_is_durable_customer_visible_and_preserves_business_facts(pending, decision):
    settings, store, service, _, rid = pending
    with closing(sqlite3.connect(settings.db_path)) as conn:
        before = conn.execute("SELECT * FROM orders").fetchall()
    result = service.review(rid, decision, "已核对申请材料", reviewer="reviewer")
    assert result["status"] == decision
    assert result["review"]["reason"] == "已核对申请材料"
    fresh = ReturnService(settings, SessionStore(settings.db_path))
    assert fresh.list_requests("USER001")[0] == result
    assert fresh.list_requests("USER002") == []
    assert review_count(settings) == (1, 2)
    other = store.create("USER001")
    write(store, other, {"order_id": "O10024", "item_id": 4, "quantity": 1,
                         "reason": "no_reason", "secondary_sale_ok": True})
    assert service.preview(other, "USER001")["ready"] is (decision == "rejected")
    with closing(sqlite3.connect(settings.db_path)) as conn:
        assert conn.execute("SELECT * FROM orders").fetchall() == before
        audit = conn.execute("SELECT actor,detail_json FROM return_audit "
                             "WHERE action='reviewed'").fetchone()
        assert audit[0] == "reviewer" and json.loads(audit[1])["to_status"] == decision


@pytest.mark.parametrize("actor", [None, "USER001", "unconfigured-admin"])
def test_customer_and_missing_principal_cannot_read_or_review(pending, actor):
    settings, _, service, _, rid = pending
    for action in (lambda: service.list_for_review(reviewer=actor),
                   lambda: service.get_for_review(rid, reviewer=actor),
                   lambda: service.review_evidence(rid, reviewer=actor),
                   lambda: service.review(rid, "approved", "冒充管理员", reviewer=actor)):
        with pytest.raises(PermissionError):
            action()
    assert service.list_requests("USER001")[0]["status"] == "pending_review"
    assert review_count(settings) == (0, 1)


@pytest.mark.parametrize("decision,reason", [("refunded", "审核"), ("pending_review", "审核"),
                                           ("approved", " "), ("rejected", None),
                                           ("approved", "字" * 1001)])
def test_invalid_transition_or_reason_has_no_write(pending, decision, reason):
    settings, _, service, _, rid = pending
    with pytest.raises(TaskValidationError):
        service.review(rid, decision, reason, reviewer="reviewer")
    assert review_count(settings) == (0, 1)


def test_repeat_review_and_sql_terminal_reversal_are_rejected(pending):
    settings, _, service, _, rid = pending
    service.review(rid, "approved", "材料符合", reviewer="reviewer")
    with pytest.raises(TaskValidationError, match="已经审核"):
        service.review(rid, "rejected", "再次审核", reviewer="reviewer")
    with (closing(sqlite3.connect(settings.db_path)) as conn, conn,
          pytest.raises(sqlite3.IntegrityError, match="transition")):
        conn.execute("UPDATE return_requests SET status='rejected' WHERE request_id=?", (rid,))
    assert review_count(settings) == (1, 2)


def test_concurrent_approve_and_reject_only_one_succeeds(pending):
    settings, _, service, _, rid = pending

    def decide(decision):
        try:
            return service.review(rid, decision, "并发审核", reviewer="reviewer")["status"]
        except TaskValidationError:
            return "already_reviewed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ["approved", "rejected"]))
    assert results.count("already_reviewed") == 1
    assert review_count(settings) == (1, 2)


def test_audit_failure_rolls_back_review_and_status(pending):
    settings, _, service, _, rid = pending
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("CREATE TRIGGER fail_review_audit BEFORE INSERT ON return_audit "
                     "WHEN NEW.action='reviewed' BEGIN SELECT RAISE(ABORT,'audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="audit failure"):
        service.review(rid, "approved", "材料符合", reviewer="reviewer")
    assert service.list_requests("USER001")[0]["status"] == "pending_review"
    assert review_count(settings) == (0, 1)


def test_admin_sees_original_quality_evidence_and_tampering_is_detected(flow):
    settings, store, _, sid = flow
    settings = settings.model_copy(update={"admin_username": "reviewer",
                                           "admin_password": SecretStr("synthetic-review-pass")})
    service = ReturnService(settings, store)
    write(store, sid, {"reason": "quality", "problem_description": "单耳无声",
                       "quality_non_user_damage": True})
    tid = store.begin_turn(sid, "USER001", "上传合成图")
    service.attach_evidence(sid, "USER001", tid, image_bytes())
    store.interrupt_turn(sid, "USER001", tid)
    preview = service.preview(sid, "USER001")
    rid = service.confirm_and_submit(sid, "USER001", preview["preview_id"])["request_id"]
    detail = service.get_for_review(rid, reviewer="reviewer")
    assert detail["content"]["claims_verified"] is False
    assert detail["current_order"]["order_id"] == "O10024"
    media = service.review_evidence(rid, reviewer="reviewer")
    assert media["data"] == image_bytes()
    assert hashlib.sha256(media["data"]).hexdigest() == media["sha256"]
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE return_evidence SET data=?", (b"modified",))
    with pytest.raises(TaskValidationError, match="凭证"):
        service.review_evidence(rid, reviewer="reviewer")


def test_model_has_no_review_tool_even_when_admin_credentials_configured(pending):
    settings, _, service, _, _ = pending
    assert not any("review" in name for name in ToolRegistry(settings).names
                   if name != "preview_return")
    assert service.authenticate_reviewer("reviewer", "synthetic-review-pass")
    assert not service.authenticate_reviewer("reviewer", "incorrect")
    assert not service.authenticate_reviewer("USER001", "synthetic-review-pass")
    unconfigured = ReturnService(settings.model_copy(update={"admin_password": SecretStr("")}))
    assert not unconfigured.authenticate_reviewer("reviewer", "")


def test_v4_migration_keeps_v3_pending_request_history_and_original_content(pending):
    settings, store, _, sid, _ = pending
    history = store.load(sid, "USER001")
    with closing(store._connect()) as conn, conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM return_requests")]
        conn.execute("DROP TABLE return_reviews")
        conn.execute("DROP TRIGGER return_status_transition")
        conn.execute("DELETE FROM acs_migrations WHERE version=4")
    fresh = SessionStore(settings.db_path)
    assert fresh.load(sid, "USER001") == history
    with closing(fresh._connect()) as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM return_requests")] == before
        assert conn.execute("SELECT max(version) FROM acs_migrations").fetchone()[0] == 6
        assert conn.execute("SELECT count(*) FROM return_reviews").fetchone()[0] == 0


def test_http_auth_guards_config_callbacks_and_ignores_forged_username(pending):
    import admin_app
    import gradio as gr
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    settings, _, service, _, rid = pending
    demo = admin_app.build_app(settings)
    server = gr.mount_gradio_app(FastAPI(), demo, path="/", auth=service.authenticate_reviewer)
    with TestClient(server) as client:
        assert client.get("/config").status_code == 401
        assert client.post("/gradio_api/call/approved", json={
            "data": [rid, "伪造身份"], "username": "reviewer",
        }).status_code == 401
        assert client.post("/login", data={"username": "reviewer",
                                          "password": "incorrect"}).status_code == 400
        assert client.post("/login", data={"username": "reviewer",
                                          "password": "synthetic-review-pass"}).status_code == 200
        assert client.get("/config").status_code == 200
        response = client.post("/gradio_api/call/refresh", json={"data": ["pending_review"]})
        assert response.status_code == 200
        assert service.list_requests("USER001")[0]["status"] == "pending_review"
        client.get("/logout")
        assert client.get("/config").status_code == 401


def test_unconfigured_workbench_fails_closed(settings):
    import admin_app

    with pytest.raises(RuntimeError, match="配置"):
        admin_app.build_app(settings.model_copy(update={"admin_username": "",
                                                        "admin_password": SecretStr("")}))
