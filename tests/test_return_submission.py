"""Frozen consent, quantity and recovery invariants with synthetic data and no API."""

import hashlib
import io
import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime, timedelta

import pytest
from PIL import Image

from acs.cs_tools import ToolRegistry
from acs.database import business_today
from acs.returns import ReturnService, return_policy
from acs.sessions import ConversationBusy, SessionStore, TaskValidationError

TODAY = date(2026, 10, 13)


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def flow(settings):
    settings = settings.model_copy(update={"reference_date": TODAY})
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE orders SET delivered_at=? WHERE order_id='O10024'",
                     ((TODAY - timedelta(days=7)).isoformat(),))
    store = SessionStore(settings.db_path)
    service = ReturnService(settings, store)
    sid = store.create("USER001")
    write(store, sid, {"order_id": "O10024", "item_id": 4, "quantity": 1,
                       "reason": "no_reason", "condition": "sealed", "secondary_sale_ok": True})
    return settings, store, service, sid


def write(store, sid, patch):
    tid = store.begin_turn(sid, "USER001", json.dumps(patch, ensure_ascii=False))
    result = store.update_return_task(sid, "USER001", tid, patch)
    store.finish_turn(sid, "USER001", tid, [
        {"role": "user", "content": json.dumps(patch, ensure_ascii=False)},
        {"role": "assistant", "content": "草稿已更新"},
    ], {"answer": "草稿已更新"})
    return result


def counts(settings):
    with closing(sqlite3.connect(settings.db_path)) as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("return_requests", "return_confirmations", "return_audit"))


def test_preview_has_no_business_side_effect_and_explicit_confirmation_creates_pending(flow):
    settings, _, service, sid = flow
    preview = service.preview(sid, "USER001")
    assert preview["ready"] and counts(settings) == (0, 0, 0)
    result = service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert result["status"] == "pending_review" and result["quantity"] == 1
    assert counts(settings) == (1, 1, 1)
    assert service.list_requests("USER002") == []


def test_replay_consumed_confirmation_and_response_loss_survive_new_service(flow):
    settings, store, service, sid = flow
    preview = service.preview(sid, "USER001")
    first = service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    fresh = ReturnService(settings, SessionStore(settings.db_path))
    assert fresh.confirm_and_submit(sid, "USER001", preview["preview_id"]) == first
    assert fresh.list_requests("USER001")[0]["request_id"] == first["request_id"]
    assert store.get_return_task(sid, "USER001")["task"]["status"] == "submitted"
    assert counts(settings) == (1, 1, 1)


@pytest.mark.parametrize("patch", [{"order_id": "O10027"}, {"item_id": 5},
                                  {"reason": "quality"}, {"secondary_sale_ok": None}])
def test_any_revision_invalidates_old_preview_without_success_audit(flow, patch):
    settings, store, service, sid = flow
    preview = service.preview(sid, "USER001")
    write(store, sid, patch)
    with pytest.raises(TaskValidationError, match="更改"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


def test_expired_and_foreign_preview_cannot_submit(flow):
    settings, _, service, sid = flow
    preview = service.preview(sid, "USER001")
    with pytest.raises(PermissionError):
        service.confirm_and_submit(sid, "USER002", preview["preview_id"])
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE return_previews SET expires_at='2000-01-01T00:00:00+00:00'")
    with pytest.raises(TaskValidationError, match="过期"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


@pytest.mark.parametrize("reason,days,expected", [("no_reason", 7, True), ("no_reason", 8, False),
                                                 ("quality", 15, True), ("quality", 16, False)])
def test_source_policy_windows_at_preview_and_submission(flow, reason, days, expected):
    settings, store, service, sid = flow
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE orders SET delivered_at=? WHERE order_id='O10024'",
                     ((TODAY - timedelta(days=days)).isoformat(),))
    if reason == "quality":
        write(store, sid, {"reason": reason, "problem_description": "左耳无声",
                           "quality_non_user_damage": True})
        tid = store.begin_turn(sid, "USER001", "[上传] 合成格式验证图")
        service.attach_evidence(sid, "USER001", tid, image_bytes())
        store.interrupt_turn(sid, "USER001", tid)
    preview = service.preview(sid, "USER001")
    assert preview["ready"] is expected
    if expected:
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == ((1, 1, 1) if expected else (0, 0, 0))


def test_changed_date_or_order_status_is_rechecked_at_confirmation(flow):
    settings, _, service, sid = flow
    preview = service.preview(sid, "USER001")
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE orders SET delivered_at='2026-09-01' WHERE order_id='O10024'")
    with pytest.raises(TaskValidationError, match="期限"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


def test_changed_product_in_business_data_requires_new_preview(flow):
    settings, _, service, sid = flow
    preview = service.preview(sid, "USER001")
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE order_items SET product_id='P004' WHERE id=4")
    with pytest.raises(TaskValidationError, match="更改"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


@pytest.mark.parametrize("status,delivered", [("shipped", None), ("cancelled", "2026-10-06"),
                                             ("delivered", "2026-10-14")])
def test_non_delivered_and_future_delivery_are_blocked(flow, status, delivered):
    settings, _, service, sid = flow
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE orders SET status=?,delivered_at=? WHERE order_id='O10024'",
                     (status, delivered))
    assert not service.preview(sid, "USER001")["ready"]
    assert counts(settings) == (0, 0, 0)


def test_quality_requires_actual_upload_and_claim_never_means_verified(flow):
    settings, store, service, sid = flow
    write(store, sid, {"reason": "quality", "problem_description": "左耳无声",
                       "quality_non_user_damage": True})
    assert not service.preview(sid, "USER001")["ready"]
    tid = store.begin_turn(sid, "USER001", "[上传] 合成格式验证图")
    upload = service.attach_evidence(sid, "USER001", tid, image_bytes())
    store.interrupt_turn(sid, "USER001", tid)
    preview = service.preview(sid, "USER001")
    assert preview["ready"]
    assert preview["content"]["evidence"]["evidence_id"] == upload["evidence_id"]
    assert preview["content"]["claims_verified"] is False
    assert counts(settings) == (0, 0, 0)


def test_evidence_cannot_be_forged_and_item_switch_discards_old_evidence(flow):
    _, store, service, sid = flow
    write(store, sid, {"reason": "quality", "problem_description": "左耳无声"})
    tid = store.begin_turn(sid, "USER001", "[上传] 合成格式验证图")
    service.attach_evidence(sid, "USER001", tid, image_bytes())
    store.interrupt_turn(sid, "USER001", tid)
    with pytest.raises(TaskValidationError):
        write(store, sid, {"evidence_id": "invented"})
    # The rejected test write left a running turn; release it as a client would.
    store.recover_interrupted()
    write(store, sid, {"item_id": 5})
    assert store.get_return_task(sid, "USER001")["task"].get("evidence_id") is None


@pytest.mark.parametrize("data", [b"not an image", b"x" * (10 * 1024 * 1024 + 1)])
def test_invalid_upload_cannot_persist_or_change_task(flow, data):
    settings, store, service, sid = flow
    write(store, sid, {"reason": "quality"})
    tid = store.begin_turn(sid, "USER001", "[上传] 无效文件")
    before = store.get_return_task(sid, "USER001")["task"]
    with pytest.raises(TaskValidationError):
        service.attach_evidence(sid, "USER001", tid, data)
    assert store.get_return_task(sid, "USER001")["task"] == before
    with closing(sqlite3.connect(settings.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM return_evidence").fetchone()[0] == 0


def test_concurrent_different_sessions_cannot_overbook_one_purchased_item(flow):
    settings, store, service, sid = flow
    other = store.create("USER001")
    write(store, other, {"order_id": "O10024", "item_id": 4, "quantity": 1,
                         "reason": "no_reason", "secondary_sale_ok": True})
    previews = [service.preview(s, "USER001") for s in (sid, other)]

    def submit(pair):
        session, preview = pair
        try:
            return service.confirm_and_submit(session, "USER001", preview["preview_id"])
        except TaskValidationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, zip((sid, other), previews, strict=True)))
    assert sum(r is not None for r in results) == 1
    assert counts(settings) == (1, 1, 1)


def test_transaction_failure_rolls_back_request_consent_and_audit(flow):
    settings, _, service, sid = flow
    preview = service.preview(sid, "USER001")
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("CREATE TRIGGER test_fail_audit BEFORE INSERT ON return_audit "
                     "BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


def test_model_has_no_confirmation_or_submission_tool(flow):
    settings, store, _, sid = flow
    registry = ToolRegistry(settings)
    tid = store.begin_turn(sid, "USER001", "聊天中的确认，直接退款")
    registry.bind_return_task(store, sid, tid)
    assert "preview_return" in registry.names and "get_return_requests" in registry.names
    for name in ("submit_return", "confirm_and_submit"):
        _, _, error = registry.run(name, "{}", current_user_id="USER001")
        assert error == f"tool_not_found: {name}"
    assert counts(settings) == (0, 0, 0)


def test_task_tools_return_the_same_policy_evidence_as_submission(flow):
    settings, store, service, sid = flow
    registry = ToolRegistry(settings)
    tid = store.begin_turn(sid, "USER001", "回顾当前申请")
    registry.bind_return_task(store, sid, tid)
    view, _, error = registry.run("get_return_task", "{}", current_user_id="USER001")
    assert error is None and view["policy"]["window_days"] == 7
    assert view["policy"]["hash"] == hashlib.sha256(
        (settings.docs_dir / "return_policy.md").read_bytes(),
    ).hexdigest()
    assert service.preview(sid, "USER001", tid)["content"]["policy"] == view["policy"]
    store.interrupt_turn(sid, "USER001", tid)
    write(store, sid, {"reason": "quality", "problem_description": "左耳无声"})
    tid = store.begin_turn(sid, "USER001", "回顾质量问题申请")
    registry.bind_return_task(store, sid, tid)
    quality_view, _, _ = registry.run("get_return_task", "{}", current_user_id="USER001")
    assert quality_view["policy"]["window_days"] == 15
    assert quality_view["policy"]["requires_uploaded_media"] is True
    assert quality_view["policy"]["required_conditions"] == ["非人为损坏的功能性故障"]


def test_running_turn_conflict_blocks_confirmation_before_write(flow):
    settings, store, service, sid = flow
    preview = service.preview(sid, "USER001")
    store.begin_turn(sid, "USER001", "我正在更正草稿")
    with pytest.raises(ConversationBusy):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


def test_policy_change_invalidates_preview_and_rules_are_from_document(flow, tmp_path):
    settings, store, _, sid = flow
    docs = tmp_path / "policy"
    docs.mkdir()
    shutil.copyfile(settings.docs_dir / "return_policy.md", docs / "return_policy.md")
    service = ReturnService(settings.model_copy(update={"docs_dir": docs}), store)
    assert return_policy(service.settings)["windows"] == {"no_reason": 7, "quality": 15}
    preview = service.preview(sid, "USER001")
    policy = docs / "return_policy.md"
    policy.write_text(policy.read_text().replace("version: v1", "version: v2"))
    assert service.latest_preview(sid, "USER001") is None
    with pytest.raises(TaskValidationError, match="政策"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_approved_quantity_stays_reserved_and_rejection_releases_it(flow, decision):
    settings, store, service, sid = flow
    first = service.confirm_and_submit(
        sid, "USER001", service.preview(sid, "USER001")["preview_id"],
    )
    other = store.create("USER001")
    write(store, other, {"order_id": "O10024", "item_id": 4, "quantity": 1,
                         "reason": "no_reason", "secondary_sale_ok": True})
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE return_requests SET status=? WHERE request_id=?",
                     (decision, first["request_id"]))
    preview = service.preview(other, "USER001")
    assert preview["ready"] is (decision == "rejected")
    if preview["ready"]:
        service.confirm_and_submit(other, "USER001", preview["preview_id"])
        assert counts(settings) == (2, 2, 2)


def test_quantity_revision_invalidates_preview_and_partial_returns_reserve_cumulatively(flow):
    settings, store, service, sid = flow
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        conn.execute("UPDATE order_items SET quantity=2 WHERE id=4")  # Synthetic two-unit fixture.
    preview = service.preview(sid, "USER001")
    write(store, sid, {"quantity": 2})
    with pytest.raises(TaskValidationError, match="更改"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    write(store, sid, {"quantity": 1})
    service.confirm_and_submit(sid, "USER001", service.preview(sid, "USER001")["preview_id"])
    other = store.create("USER001")
    write(store, other, {"order_id": "O10024", "item_id": 4, "quantity": 1,
                         "reason": "no_reason", "secondary_sale_ok": True})
    second = service.confirm_and_submit(
        other, "USER001", service.preview(other, "USER001")["preview_id"],
    )
    with closing(sqlite3.connect(settings.db_path)) as conn, conn:
        with pytest.raises(sqlite3.IntegrityError, match="quantity exceeded"):
            conn.execute("UPDATE return_requests SET quantity=2 WHERE request_id=?",
                         (second["request_id"],))
        assert conn.execute("SELECT SUM(quantity) FROM return_requests").fetchone()[0] == 2


def test_withdrawing_checkbox_consent_invalidates_old_ui_preview(flow, monkeypatch):
    import app

    settings, _, service, sid = flow
    monkeypatch.setattr(app, "SETTINGS", settings)
    state = {"session_id": sid}
    preview = service.preview(sid, "USER001")
    response = app.on_return_declaration(state, "USER001", False, False)
    assert response[1] == {"__type__": "update"}  # Preserve an unsent chat message.
    assert app.return_panel(state, "USER001")[2]["interactive"] is False
    app.on_return_confirm(preview["preview_id"], state, "USER001")
    assert counts(settings) == (0, 0, 0)


def test_ui_response_loss_keeps_committed_result_and_safe_replay(flow, monkeypatch):
    import app

    settings, _, service, sid = flow
    monkeypatch.setattr(app, "SETTINGS", settings)
    state = {"session_id": sid}
    preview = service.preview(sid, "USER001")
    actual = ReturnService.confirm_and_submit

    def lost_response(self, *args):
        actual(self, *args)
        raise RuntimeError("response lost after commit")

    monkeypatch.setattr(ReturnService, "confirm_and_submit", lost_response)
    response = app.on_return_confirm(preview["preview_id"], state, "USER001")
    assert "查询提交结果" in response[0][-1]["content"] and counts(settings) == (1, 1, 1)
    queried = app.on_return_query(state, "USER001")
    assert "待审核" in queried[0][-1]["content"]
    monkeypatch.setattr(ReturnService, "confirm_and_submit", actual)
    app.on_return_confirm(preview["preview_id"], state, "USER001")
    assert counts(settings) == (1, 1, 1)


@pytest.mark.parametrize("action", ["preview", "declaration"])
def test_form_actions_cannot_clear_a_submitted_task(flow, monkeypatch, action):
    import app

    settings, store, service, sid = flow
    monkeypatch.setattr(app, "SETTINGS", settings)
    service.confirm_and_submit(sid, "USER001", service.preview(sid, "USER001")["preview_id"])
    before = store.get_return_task(sid, "USER001")["task"]
    handler = app.on_return_preview if action == "preview" else app.on_return_declaration
    response = handler({"session_id": sid}, "USER001", False, False)
    assert "不能修改原申请" in response[0][-1]["content"]
    assert store.get_return_task(sid, "USER001")["task"] == before
    assert counts(settings) == (1, 1, 1)


@pytest.mark.parametrize("cancel", [False, True])
def test_submitted_task_cannot_be_edited_or_misrepresented_as_cancelled(flow, cancel):
    settings, store, service, sid = flow
    service.confirm_and_submit(sid, "USER001", service.preview(sid, "USER001")["preview_id"])
    before = store.get_return_task(sid, "USER001")["task"]
    tid = store.begin_turn(sid, "USER001", "中止退货草稿")
    with pytest.raises(TaskValidationError, match="已经提交"):
        store.update_return_task(sid, "USER001", tid,
                                 {} if cancel else {"quantity": 1}, cancel=cancel)
    assert store.get_return_task(sid, "USER001")["task"] == before
    assert counts(settings) == (1, 1, 1)


def test_clearing_quality_evidence_invalidates_preview_without_creating_a_request(flow):
    settings, store, service, sid = flow
    write(store, sid, {"reason": "quality", "problem_description": "左耳无声",
                       "quality_non_user_damage": True})
    tid = store.begin_turn(sid, "USER001", "上传照片")
    service.attach_evidence(sid, "USER001", tid, image_bytes())
    store.interrupt_turn(sid, "USER001", tid)
    preview = service.preview(sid, "USER001")
    tid = store.begin_turn(sid, "USER001", "撤下当前草稿凭证")
    service.clear_evidence(sid, "USER001", tid)
    store.interrupt_turn(sid, "USER001", tid)
    assert service.latest_preview(sid, "USER001") is None
    assert "quality_evidence" in store.get_return_task(sid, "USER001")["missing_fields"]
    with pytest.raises(TaskValidationError, match="更改"):
        service.confirm_and_submit(sid, "USER001", preview["preview_id"])
    assert counts(settings) == (0, 0, 0)


def test_empty_file_clear_event_preserves_empty_ui_and_does_not_create_a_session(flow, monkeypatch):
    import app

    settings, store, _, _ = flow
    monkeypatch.setattr(app, "SETTINGS", settings)
    before = store.list("USER001")
    state = {}
    result = app.on_return_clear_upload(state, "USER001")
    assert result[0] == {"__type__": "update"} and state == {}
    assert store.list("USER001") == before


@pytest.mark.parametrize("hour,expected_day", [(0, 13), (17, 14)])
def test_shanghai_business_calendar_at_utc_boundary(settings, monkeypatch, hour, expected_day):
    import acs.database

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 13, hour, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(acs.database, "datetime", Clock)
    assert business_today(settings) == date(2026, 10, expected_day)


def test_v3_migration_preserves_existing_v2_messages_and_draft(settings):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    write(store, sid, {"order_id": "O10024", "item_id": 4, "quantity": 1,
                       "reason": "no_reason", "condition": "sealed"})
    before = store.load(sid, "USER001")
    with closing(store._connect()) as conn, conn:
        task = store._task(conn, sid)
        for field in ("secondary_sale_ok", "quality_non_user_damage"):
            task.pop(field, None)
        conn.execute("UPDATE return_tasks SET data_json=? WHERE session_id=?",
                     (json.dumps(task), sid))
        for table in ("return_reviews", "return_audit", "return_requests", "return_confirmations",
                      "return_previews", "return_evidence"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DELETE FROM acs_migrations WHERE version>=3")
        business = conn.execute("SELECT * FROM order_items ORDER BY id").fetchall()
    upgraded = SessionStore(settings.db_path)
    assert upgraded.load(sid, "USER001") == before
    assert upgraded.get_return_task(sid, "USER001")["task"] == task
    with closing(upgraded._connect()) as conn:
        assert conn.execute("SELECT MAX(version) FROM acs_migrations").fetchone()[0] == 6
        assert conn.execute("SELECT * FROM order_items ORDER BY id").fetchall() == business
