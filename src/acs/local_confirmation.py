"""Application-only native Interrupt gate. Never creates a formal return record.

Business bindings/events/consumption receipts are authoritative in business SQLite;
Graph owns only the wait position. No model tool or chat message enters this API.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .local_return_tools import ReconciliationError
from .returns import return_policy
from .sessions import ConversationBusy

TERMINAL = {"accepted", "rejected", "cancelled", "invalidated"}


class ConfirmationState(TypedDict, total=False):
    wait_id: str
    result: dict
    retry: bool


class LocalConfirmation:
    def __init__(self, gateway, checkpointer, *, fault=None):
        self.gateway, self.store = gateway, gateway.store
        self.fault = fault or (lambda point: None)
        with self.store.transaction() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS local_confirmation_waits(
                wait_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
                preview_id TEXT NOT NULL, revision INTEGER NOT NULL, policy_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL, status TEXT NOT NULL,
                event_id TEXT UNIQUE, decision TEXT, result_json TEXT,
                consumptions INTEGER NOT NULL DEFAULT 0 CHECK(consumptions BETWEEN 0 AND 1))""")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS local_one_confirmation_wait
                ON local_confirmation_waits(session_id) WHERE status IN ('waiting','issued')""")
        graph = StateGraph(ConfirmationState, context_schema=dict)
        graph.add_node("application_confirmation", self._gate)
        graph.add_edge(START, "application_confirmation")
        graph.add_conditional_edges(
            "application_confirmation",
            lambda state: "application_confirmation" if state.get("retry") else END,
        )
        self.graph = graph.compile(checkpointer=checkpointer)

    @staticmethod
    def config(wait_id):
        return {"configurable": {"thread_id": "local-confirmation:" + wait_id}}

    def _row(self, conn, sid, uid, wait_id):
        self.store._require_owner(conn, sid, uid)
        row = conn.execute(
            "SELECT * FROM local_confirmation_waits WHERE wait_id=?", (wait_id,)
        ).fetchone()
        if row is None or (row["session_id"], row["user_id"]) != (sid, uid):
            raise PermissionError("Confirmation does not belong to this application session")
        return row

    def _valid(self, conn, row):
        task = self.store._task(conn, row["session_id"])
        preview = conn.execute(
            "SELECT * FROM return_previews WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
            (row["session_id"],),
        ).fetchone()
        policy = return_policy(self.gateway.settings)
        if (
            not task
            or task["status"] != "draft"
            or task["revision"] != row["revision"]
            or not preview
            or preview["preview_id"] != row["preview_id"]
            or preview["revision"] != row["revision"]
            or preview["policy_hash"] != row["policy_hash"]
            or policy["hash"] != row["policy_hash"]
            or preview["expires_at"] != row["expires_at"]
            or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
        ):
            return False
        content, reasons = self.gateway.service._content(
            conn, row["session_id"], row["user_id"], policy
        )
        return not reasons and content == json.loads(preview["content_json"])

    def _idle(self, conn, sid):
        if conn.execute(
            "SELECT 1 FROM conversation_turns WHERE session_id=? AND status='running'", (sid,)
        ).fetchone():
            raise ConversationBusy("Finish or reconcile the current chat Turn before confirming")

    @staticmethod
    def _result(row, status):
        return {
            "status": status,
            "wait_id": row["wait_id"],
            "event_id": row["event_id"],
            "preview_id": row["preview_id"],
            "revision": row["revision"],
            "formal_submission": False,
        }

    def _invalidate(self, conn, row):
        result = self._result(row, "invalidated")
        conn.execute(
            "UPDATE local_confirmation_waits SET status='invalidated',result_json=?,"
            "consumptions=CASE WHEN event_id IS NULL THEN 0 ELSE 1 END WHERE wait_id=?",
            (json.dumps(result), row["wait_id"]),
        )
        return result

    def pending(self, sid, uid):
        """Recover an open reply lost before the application learned its wait ID."""
        with self.store.transaction() as conn:
            self.store._require_owner(conn, sid, uid)
            row = conn.execute(
                "SELECT wait_id FROM local_confirmation_waits WHERE session_id=? "
                "AND user_id=? AND status IN ('waiting','issued')",
                (sid, uid),
            ).fetchone()
        return self.resume(sid, uid, row["wait_id"]) if row else {"status": "none"}

    def open(self, sid, uid):
        with self.store.transaction() as conn:
            self.store._require_owner(conn, sid, uid)
            self._idle(conn, sid)
            pending = conn.execute(
                "SELECT * FROM local_confirmation_waits WHERE session_id=? "
                "AND status IN ('waiting','issued')",
                (sid,),
            ).fetchone()
            if pending:
                raise ConversationBusy("Resolve the existing confirmation wait first")
            # BEGIN IMMEDIATE prevents any concurrent draft mutation during this binding.
            preview = self.gateway.service.latest_preview(sid, uid)
            if not preview:
                raise ReconciliationError("No current valid preview to confirm")
            row = conn.execute(
                "SELECT * FROM return_previews WHERE preview_id=?", (preview["preview_id"],)
            ).fetchone()
            wait_id = uuid4().hex
            conn.execute(
                "INSERT INTO local_confirmation_waits "
                "(wait_id,session_id,user_id,preview_id,revision,policy_hash,expires_at,status) "
                "VALUES(?,?,?,?,?,?,?,'waiting')",
                (
                    wait_id,
                    sid,
                    uid,
                    row["preview_id"],
                    row["revision"],
                    row["policy_hash"],
                    row["expires_at"],
                ),
            )
        self.fault("after_open_commit")
        return self.resume(sid, uid, wait_id)

    def decide(self, sid, uid, wait_id, decision):
        if decision not in {"accept", "reject", "cancel"}:
            raise ValueError("Application decision must be accept, reject or cancel")
        # Only this trusted application API issues an event. Graph resume values alone
        # cannot create an event, and the model never sees this function as a tool.
        with self.store.transaction() as conn:
            row = self._row(conn, sid, uid, wait_id)
            self._idle(conn, sid)
            if row["status"] != "waiting" or row["event_id"]:
                raise ReconciliationError("Duplicate or late application decision")
            snapshot = self.graph.get_state(self.config(wait_id))
            if not snapshot.interrupts or snapshot.values.get("wait_id") != wait_id:
                raise ReconciliationError("Resume the durable application wait before deciding")
            if decision == "accept" and not self._valid(conn, row):
                self._invalidate(conn, row)
            else:
                conn.execute(
                    "UPDATE local_confirmation_waits SET event_id=?,decision=?,"
                    "status='issued' WHERE wait_id=?",
                    (uuid4().hex, decision, wait_id),
                )
            self.fault("before_event_commit")
        self.fault("after_event_commit")
        return self.resume(sid, uid, wait_id)

    def _gate(self, state, runtime):
        sid, uid = runtime.context["session_id"], runtime.context["user_id"]
        with self.store.transaction() as conn:
            row = self._row(conn, sid, uid, state["wait_id"])
            if row["status"] in TERMINAL:
                return {"result": json.loads(row["result_json"]), "retry": False}
            payload = {
                k: row[k]
                for k in ("wait_id", "preview_id", "revision", "policy_hash", "expires_at")
            }
            payload["notice"] = "等待应用端显式确认；即使接受也不创建正式申请。"
        event_id = interrupt(payload)  # MUST NOT catch GraphInterrupt or consume before pause.
        with self.store.transaction() as conn:
            row = self._row(conn, sid, uid, state["wait_id"])
            if not row["event_id"] or event_id != row["event_id"]:
                # A rejected resume starts a fresh native interrupt task. Raising
                # would retain the forged resume in this failed task's cache.
                return {"retry": True}
            if row["status"] in TERMINAL:
                return {"result": json.loads(row["result_json"]), "retry": False}
            if row["status"] != "issued":
                raise ReconciliationError("Application event is not ready for consumption")
            self._idle(conn, sid)
            if row["decision"] == "accept" and not self._valid(conn, row):
                result = self._invalidate(conn, row)
            else:
                status = {"accept": "accepted", "reject": "rejected", "cancel": "cancelled"}[
                    row["decision"]
                ]
                result = self._result(row, status)
                conn.execute(
                    "UPDATE local_confirmation_waits SET status=?,result_json=?,"
                    "consumptions=1 WHERE wait_id=?",
                    (status, json.dumps(result), row["wait_id"]),
                )
            self.fault("before_consume_commit")
        self.fault("after_consume_commit")
        return {"result": result, "retry": False}

    def resume(self, sid, uid, wait_id):
        with self.store.transaction() as conn:
            row = self._row(conn, sid, uid, wait_id)
            self._idle(conn, sid)
            if row["status"] == "waiting" and not self._valid(conn, row):
                self._invalidate(conn, row)
                row = self._row(conn, sid, uid, wait_id)
            row = dict(row)
        config = self.config(wait_id)
        snapshot = self.graph.get_state(config)
        if snapshot.values and snapshot.values.get("wait_id") != wait_id:
            raise ReconciliationError("Confirmation Graph binding mismatch")
        if not snapshot.values and row["status"] != "waiting" and row["event_id"]:
            raise ReconciliationError("Confirmation Graph missing for an issued or consumed event")
        if snapshot.values and row["status"] in TERMINAL and not snapshot.next:
            if snapshot.values.get("result") != json.loads(row["result_json"]):
                raise ReconciliationError("Confirmation Graph and business receipt differ")
            with self.store.transaction() as conn:
                current = self._row(conn, sid, uid, wait_id)
                valid = self._valid(conn, current)
            return json.loads(row["result_json"]) | {
                "historical_receipt": True,
                "current_binding_valid": valid,
                "reusable": False,
            }
        context = {"session_id": sid, "user_id": uid}
        if not snapshot.values:
            inputs = {"wait_id": wait_id}
        elif row["status"] == "issued":
            inputs = Command(resume=row["event_id"])
        elif row["status"] == "waiting" and snapshot.interrupts:
            return {
                "status": "waiting",
                "wait_id": wait_id,
                "interrupt": snapshot.interrupts[0].value,
                "formal_submission": False,
            }
        else:
            inputs = None
        self.graph.invoke(inputs, config, context=context, durability="sync")
        final = self.graph.get_state(config)
        if final.interrupts:
            return {
                "status": "waiting",
                "wait_id": wait_id,
                "interrupt": final.interrupts[0].value,
                "formal_submission": False,
            }
        result = final.values.get("result")
        if final.next or result is None:
            raise ReconciliationError("Confirmation Graph has no durable result")
        # A checkpoint claiming acceptance is never sufficient business evidence.
        with self.store.transaction() as conn:
            receipt = self._row(conn, sid, uid, wait_id)
            if receipt["status"] not in TERMINAL or json.loads(receipt["result_json"]) != result:
                raise ReconciliationError("Confirmation Graph and business receipt differ")
        return result
