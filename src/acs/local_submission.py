"""Synthetic-only application submission. SQL receipts outrank Graph mirrors.

The fresh fixture and connection-local capability are laboratory boundaries, not
production authentication. No capability, event or submit method is a model tool.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from langgraph.checkpoint.sqlite import SqliteSaver

from .local_confirmation import LocalConfirmation
from .local_return_poc import PROMPT, LocalReturnPoc
from .local_return_tools import (
    FORMAL_FIXTURE_KIND as KIND,
)
from .local_return_tools import (
    ReconciliationError,
    _init_fixture,
    fixture_settings,
    formal_barriers,
)
from .returns import REQUEST_QUERY, ReturnService
from .sessions import ConversationBusy, SessionStore


def init_formal_fixture(root):
    """Always a NEW synthetic database; never migrate or unlock a draft fixture."""
    return _init_fixture(root, KIND, formal_barriers())


def read_submission_receipt(root, sid, uid, wait_id):
    """Independent recovery: mode=ro, no migrations, no Graph, no writes/replays."""
    settings = fixture_settings(root, kind=KIND)
    with closing(sqlite3.connect(settings.db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        SessionStore._require_owner(conn, sid, uid)
        row = conn.execute(
            "SELECT * FROM local_confirmation_waits WHERE wait_id=?", (wait_id,)
        ).fetchone()
        if row is None or (row["session_id"], row["user_id"]) != (sid, uid):
            raise PermissionError("Confirmation does not belong to this application session")
        receipt = conn.execute(
            "SELECT * FROM local_submission_receipts WHERE wait_id=?", (wait_id,)
        ).fetchone()
        request = conn.execute(
            REQUEST_QUERY + " WHERE r.preview_id=?", (row["preview_id"],)
        ).fetchone()
        if receipt is None:
            if (
                request
                or row["status"] == "accepted"
                or row["consumptions"]
                and (row["decision"] == "accept" and row["status"] != "invalidated")
            ):
                raise ReconciliationError("Unknown submission result; no automatic write replay")
            return None
        result = json.loads(receipt["result_json"])
        confirmation = conn.execute(
            "SELECT * FROM return_confirmations WHERE preview_id=?", (row["preview_id"],)
        ).fetchone()
        audits = conn.execute(
            "SELECT * FROM return_audit WHERE request_id=?", (receipt["request_id"],)
        ).fetchall()
        detail = json.loads(audits[0]["detail_json"]) if len(audits) == 1 else {}
        if not (
            row["status"] == "accepted"
            and row["decision"] == "accept"
            and row["consumptions"] == 1
            and row["event_id"] == receipt["event_id"]
            and row["preview_id"] == receipt["preview_id"]
            and json.loads(row["result_json"]) == result
            and request
            and request["request_id"] == receipt["request_id"]
            and request["session_id"] == sid
            and request["user_id"] == uid
            and request["status"] == "pending_review"
            and ReturnService._request(request) == result["request"]
            and result
            == {
                "status": "accepted",
                "wait_id": wait_id,
                "event_id": row["event_id"],
                "preview_id": row["preview_id"],
                "revision": row["revision"],
                "formal_submission": True,
                "simulated": True,
                "request": ReturnService._request(request),
            }
            and confirmation
            and confirmation["user_id"] == uid
            and len(audits) == 1
            and audits[0]["action"] == "created"
            and audits[0]["actor"] == uid
            and detail
            == {
                "preview_id": row["preview_id"],
                "revision": row["revision"],
                "policy_hash": row["policy_hash"],
                "entry": "isolated_application_interrupt",
            }
            and confirmation["confirmed_at"] == confirmation["consumed_at"] == request["created_at"]
        ):
            raise ReconciliationError("Authoritative submission records disagree; no replay")
        return result


class FormalConfirmation(LocalConfirmation):
    notice = "等待应用端确认：仅在隔离合成数据库创建模拟待审核申请，不审核或退款。"

    def __init__(self, gateway, checkpointer, *, fault=None):
        # Validate fixture identity AND exact barrier definitions on each application entry.
        fixture_settings(gateway.settings.db_path.parent, kind=KIND)
        super().__init__(gateway, checkpointer, fault=fault)
        with self.store.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS local_submission_receipts(
                event_id TEXT PRIMARY KEY NOT NULL,
                wait_id TEXT NOT NULL UNIQUE REFERENCES local_confirmation_waits(wait_id),
                preview_id TEXT NOT NULL UNIQUE REFERENCES return_previews(preview_id),
                request_id TEXT NOT NULL UNIQUE REFERENCES return_requests(request_id),
                result_json TEXT NOT NULL)""")

    @staticmethod
    def config(wait_id):
        return {"configurable": {"thread_id": "local-formal-submission:" + wait_id}}

    def _consume(self, conn, row, result):
        if row["decision"] != "accept":
            return result
        # _gate has rechecked owner, idle Turn, latest preview and full content
        # inside THIS BEGIN IMMEDIATE, including revision/policy/expiry.
        if not conn.in_transaction or row["status"] != "issued" or row["consumptions"]:
            raise ReconciliationError("Fresh unconsumed application event required")
        if conn.execute(
            "SELECT 1 FROM return_requests WHERE preview_id=?", (row["preview_id"],)
        ).fetchone():
            raise ReconciliationError("Preview already submitted; query its original receipt")
        conn.execute(
            "UPDATE local_confirmation_waits SET status='accepted',consumptions=1 WHERE wait_id=?",
            (row["wait_id"],),
        )
        self.fault("after_confirmation_consume")
        # Only this connection temporarily permits the existing business implementation.
        conn.create_function("formal_submission_event", 0, lambda: row["event_id"])
        try:
            request = self.gateway.service._confirm_and_submit(
                conn,
                row["session_id"],
                row["user_id"],
                row["preview_id"],
                entry="isolated_application_interrupt",
            )
        finally:
            conn.create_function("formal_submission_event", 0, lambda: None)
        self.fault("after_request_create")
        result = result | {"formal_submission": True, "simulated": True, "request": request}
        conn.execute(
            "INSERT INTO local_submission_receipts VALUES(?,?,?,?,?)",
            (
                row["event_id"],
                row["wait_id"],
                row["preview_id"],
                request["request_id"],
                json.dumps(result, ensure_ascii=False),
            ),
        )
        return result

    def lookup(self, sid, uid, wait_id):
        return read_submission_receipt(self.gateway.settings.db_path.parent, sid, uid, wait_id)

    def resume(self, sid, uid, wait_id):
        receipt = self.lookup(sid, uid, wait_id)
        if receipt is not None:
            try:
                state = self.graph.get_state(self.config(wait_id))
                mirror = (
                    "matched"
                    if (
                        state.values.get("result") == receipt
                        and state.values.get("wait_id") == wait_id
                        and not state.next
                    )
                    else "reconciliation_required"
                )
            except Exception:
                mirror = "unreadable"
            return receipt | {"historical_receipt": True, "reusable": False, "graph_mirror": mirror}
        # An explicit application resume can continue ONLY after the consistent SQL
        # lookup proves no committed submission. All writes remain in one transaction.
        return super().resume(sid, uid, wait_id)

    def decide(self, sid, uid, wait_id, decision):
        receipt = self.lookup(sid, uid, wait_id)
        if receipt is not None:
            if decision != "accept":
                raise ReconciliationError("Successful submission cannot be cancelled by a click")
            return self.resume(sid, uid, wait_id)
        return super().decide(sid, uid, wait_id, decision)


class FormalReturnPoc(LocalReturnPoc):
    fixture_kind = KIND
    preview_description = (
        "核查当前草稿、政策与数量并生成隔离预览；模型不能提交，"
        "只有应用确认可创建模拟待审核申请，不审核或退款。"
    )

    def __init__(self, root, user_id, *, model, registry=None, fault=None):
        super().__init__(root, user_id, model=model, registry=registry, fault=fault)
        self.gateway.application_confirmation_enabled = True
        self.gateway.preview_notice = (
            "隔离预览，尚未创建申请；模型不能提交，只有应用确认可创建模拟待审核申请；"
            "不执行审核、退款或真实平台办理。"
        )

    prompt = PROMPT.replace(
        "本实验没有确认提交、审核、退款、换货、真实人工转接入口，不能声称已申请或已退款。",
        "模型没有提交、审核或退款工具。仅应用显式确认可创建隔离模拟待审核申请；以业务回执为准。",
    )

    def submission(self, sid, action, *, wait_id=None, decision=None, fault=None):
        """Trusted local application surface, absent from ToolRegistry and chat."""
        if not self.run_lock.acquire(blocking=False):
            raise ConversationBusy("Local worker is executing another application operation")
        try:
            if action == "lookup":
                return read_submission_receipt(self.root, sid, self.user_id, wait_id)
            gate = FormalConfirmation(self.gateway, SqliteSaver(self.conn), fault=fault)
            if action == "open":
                return gate.open(sid, self.user_id)
            if action == "pending":
                return gate.pending(sid, self.user_id)
            if action == "resume":
                return gate.resume(sid, self.user_id, wait_id)
            if action == "decide":
                return gate.decide(sid, self.user_id, wait_id, decision)
            raise ValueError("Unknown formal submission application action")
        finally:
            self.run_lock.release()
