"""Loopback-only synthetic Demo. Durable jobs own execution; SSE only reads events."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .answer_grounding import collect_evidence, review_answer
from .local_return_tools import LocalReturnGateway, ReconciliationError, fixture_settings
from .local_submission import KIND, FormalReturnPoc, read_submission_receipt
from .model_response_contract import ModelResponseError
from .response_evidence import ResponseEvidence
from .runtime import BudgetExceeded

COOKIE = "v3_demo"
ASSETS = Path(__file__).with_name("web_assets")
UID = "USER001"  # Synthetic application identity, never accepted from HTTP input.


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MessageBody(StrictBody):
    text: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(pattern=r"^[a-zA-Z0-9-]{8,64}$")
    read_only: bool = False


class ActionBody(StrictBody):
    request_key: str = Field(pattern=r"^[a-zA-Z0-9-]{8,64}$")


class DecisionBody(ActionBody):
    wait_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    decision: str = Field(pattern=r"^(accept|reject|cancel)$")


class ReturnCard(StrictBody):
    """The only UI state contract; SQL is authoritative for terminal success."""

    state: str = Field(pattern=r"^(READY|REJECTED|EXPIRED|UNKNOWN|PENDING_REVIEW)$")
    status: str = Field(pattern=r"^(READY|REJECTED|EXPIRED|UNKNOWN|PENDING_REVIEW)$")
    allowed_actions: list[str]
    reason: str
    preview: dict | None = None
    confirmation: dict | None = None
    request: dict | None = None
    authoritative: bool


class EventData(StrictBody):
    """Complete public event allowlist. Never a raw Graph update or tool payload."""

    status: str | None = None
    name: str | None = None
    call_id: str | None = None
    text: str | None = None
    reason: str | None = None
    preview_id: str | None = None
    wait_id: str | None = None
    revision: int | None = None
    request_id: str | None = None
    message_id: str | None = None
    emitted_at: str | None = None


EVENTS = {
    "turn_started",
    "tool_requested",
    "tool_completed",
    "business_state_updated",
    "confirmation_required",
    "submission_pending",
    "submission_verified",
    "turn_completed",
    "turn_failed",
    "answer_delta",
    "answer_discarded",
    "answer_completed",
    "answer_checked",
}


def scrub(text, secret_values=()):
    text = str(text)
    for value in secret_values:
        if value:
            text = text.replace(value, "[已隐藏]")
    for pattern in (
        r"(?i)(?:sk-|Bearer\s+)[A-Za-z0-9_.-]+",
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"(?i)(?:authorization|api[_-]?key)\s*[:=]\s*\S+",
    ):
        text = re.sub(pattern, "[已隐藏]", text)
    return text


def pick(value, keys):
    return {k: value[k] for k in keys if k in value}


def public_content(content):
    result = pick(
        content,
        (
            "order_id",
            "item_id",
            "product_name",
            "quantity",
            "reason",
            "condition",
            "secondary_sale_ok",
            "claims_verified",
        ),
    )
    result["policy"] = pick(
        content.get("policy", {}), ("window_days", "shipping_payer", "hash", "starting_point")
    )
    return result


class PublicAnswer:
    """Temporary text projection, never a business or completed-message receipt.

    Withhold a sensitive-value window AND unfinished words. This allows redaction
    across SDK chunks (keys, phones and emails) before any public text is emitted.
    """

    def __init__(self, message_id, secret_values):
        self.message_id, self.secret_values = message_id, secret_values
        self.raw, self.visible, self.blocked = "", "", False
        self.hold = max([64, *(len(v) for v in secret_values if v)])

    def add(self, text):
        if self.blocked or not isinstance(text, str):
            return None
        self.raw += text
        safe = scrub(self.raw, self.secret_values)
        boundaries = list(re.finditer(r"[\s，。！？；、]", safe[:max(0, len(safe)-self.hold)]))
        value = safe[:boundaries[-1].end()] if boundaries else ""
        if value and value != self.visible:
            self.visible = value
            return value
        return None


class AnswerStreamCallback(BaseCallbackHandler):
    """Observe native model callbacks without changing Graph/tool scheduling."""

    raise_error = True
    run_inline = True

    def __init__(self, engine, sid, jid):
        self.engine, self.sid, self.jid = engine, sid, jid
        self.answers = {}
        self.published = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.answers[run_id] = PublicAnswer(f"answer-{len(self.answers)+1}",
                                            self.engine.secret_values)

    def discard(self, reason):
        for answer in self.answers.values():
            self._discard(answer, reason)

    def _discard(self, answer, reason):
        if answer.visible and not answer.blocked:
            self.engine.emit(self.sid, self.jid, "answer_discarded",
                             message_id=answer.message_id, reason=reason, status="discarded")
        answer.blocked = True

    def on_llm_new_token(self, token, *, run_id, chunk=None, **kwargs):
        answer = self.answers.get(run_id)
        message = getattr(chunk, "message", None)
        if answer is None or not isinstance(message, AIMessageChunk):
            return
        if message.tool_call_chunks or message.invalid_tool_calls:
            self._discard(answer, "tool_phase")
        elif value := answer.add(message.content):
            self.publish(answer, value)

    def publish(self, answer, value, *, flush=False):
        previous, finished = self.published.get(answer.message_id, (0, 0))
        added = len(value) - previous
        # Persist useful batches, not every SDK token. Measure the interval AFTER
        # the preceding durable write so slow disks cannot defeat coalescing.
        if added > 0 and (not previous or flush or added >= 64
                          or (added >= 16 and time.monotonic() - finished >= .12)):
            self.engine.emit(self.sid, self.jid, "answer_delta", text=value,
                             message_id=answer.message_id, status="provisional")
            self.published[answer.message_id] = (len(value), time.monotonic())

    def on_llm_end(self, response, *, run_id, **kwargs):
        for batch in response.generations:
            for generation in batch:
                message = getattr(generation, "message", None)
                if message and (message.tool_calls or message.invalid_tool_calls):
                    self._discard(self.answers[run_id], "tool_phase")
        answer = self.answers[run_id]
        if not answer.blocked:
            self.publish(answer, answer.visible, flush=True)

    def on_llm_error(self, error, *, run_id, **kwargs):
        if run_id in self.answers:
            self._discard(self.answers[run_id], "execution_incomplete")

    def complete(self, text):
        answer = next(reversed(self.answers.values()), None)
        self.engine.emit(self.sid, self.jid, "answer_completed",
                         message_id=answer.message_id if answer else "answer-final",
                         text=text, status="completed")


class WebEngine:
    def __init__(
        self, root, model_factory, *, fault=None, secret_values=(), answer_streaming=False
    ):
        self.root, self.model_factory = root.resolve(), model_factory
        self.fault, self.secret_values = fault, secret_values
        self.answer_streaming = answer_streaming
        self.settings = fixture_settings(root, kind=KIND)
        self.gateway = LocalReturnGateway(self.settings)
        self.store = self.gateway.store
        with closing(self.store._connect()) as conn:
            identity = [str(v) for row in conn.execute("SELECT name,phone FROM users") for v in row]
        self.secret_values = (*secret_values, *identity)
        self.db = self.root / "web.sqlite"
        self.lock_file = None
        self.wal_anchor = None

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.db, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    @contextmanager
    def read(self):
        # SSE/auth/history must never acquire the single writer reservation.
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True,
                                     timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            yield conn

    def start(self):
        self.lock_file = (self.root / "web-worker.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Keep one connection open for the worker lifetime. Otherwise closing
            # each tiny transaction also checkpoints/deletes WAL on the last close.
            self.wal_anchor = sqlite3.connect(self.db, check_same_thread=False)
            self.wal_anchor.execute("PRAGMA journal_mode=WAL")
            with self.tx() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS browsers(token_hash TEXT PRIMARY KEY, "
                    "csrf TEXT NOT NULL,session_id TEXT UNIQUE)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,session_id TEXT, "
                    "request_key TEXT,kind TEXT,payload TEXT,status TEXT, "
                    "UNIQUE(session_id,request_key))"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "session_id TEXT,job_id TEXT,kind TEXT,data TEXT)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS answer_presentations("
                    "session_id TEXT,turn_id TEXT,raw_sha256 TEXT,text TEXT,"
                    "PRIMARY KEY(session_id,turn_id))"
                )
                for row in conn.execute("SELECT * FROM jobs WHERE status IN ('queued','running')"):
                    self._emit(
                        conn,
                        row["session_id"],
                        row["id"],
                        "turn_failed",
                        reason="service_interrupted",
                        status="interrupted",
                    )
                conn.execute(
                    "UPDATE jobs SET status='interrupted' WHERE status IN ('queued','running')"
                )
            self.db.chmod(0o600)
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v3-demo")
        except BaseException:
            if self.wal_anchor is not None:
                self.wal_anchor.close()
            self.lock_file.close()
            raise

    def stop(self):
        self.executor.shutdown(wait=True)  # Don't close Graph/SDK while a job still owns it.
        self.wal_anchor.close()
        self.lock_file.close()

    def browser(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.read() as conn:
            row = conn.execute("SELECT * FROM browsers WHERE token_hash=?", (digest,)).fetchone()
        return dict(row) if row else None

    def bootstrap(self):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO browsers VALUES(?,?,NULL)",
                (hashlib.sha256(token.encode()).hexdigest(), csrf),
            )
        return token

    def own(self, browser, sid):
        if not sid or browser["session_id"] != sid:
            raise HTTPException(403, "当前浏览器无权访问该会话")
        with closing(
            sqlite3.connect(self.settings.db_path.as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            self.store._require_owner(conn, sid, UID)

    def create_session(self, browser):
        with self.tx() as conn:
            row = conn.execute(
                "SELECT session_id FROM browsers WHERE token_hash=?", (browser["token_hash"],)
            ).fetchone()
            if row[0]:
                return row[0]
            sid = self.store.create(UID)
            conn.execute(
                "UPDATE browsers SET session_id=? WHERE token_hash=?", (sid, browser["token_hash"])
            )
            return sid

    def _emit(self, conn, sid, job, kind, **data):
        if kind not in EVENTS:
            raise ValueError("Unknown public event")
        data["emitted_at"] = datetime.now(UTC).isoformat()
        payload = EventData(**data).model_dump(exclude_none=True)
        for key, value in payload.items():
            if isinstance(value, str):
                payload[key] = scrub(value, self.secret_values)
        conn.execute(
            "INSERT INTO events(session_id,job_id,kind,data) VALUES(?,?,?,?)",
            (sid, job, kind, json.dumps(payload, ensure_ascii=False)),
        )

    def emit(self, sid, job, kind, **data):
        with self.tx() as conn:
            self._emit(conn, sid, job, kind, **data)

    def submit(self, sid, key, kind, payload, *, guard=None):
        encoded = json.dumps(payload, sort_keys=True)
        with self.tx() as conn:
            old = conn.execute(
                "SELECT * FROM jobs WHERE session_id=? AND request_key=?", (sid, key)
            ).fetchone()
            if old:
                if (old["kind"], old["payload"]) != (kind, encoded):
                    raise HTTPException(409, "请求标识已用于其他内容")
                return {"execution_id": old["id"], "status": old["status"], "replayed": True}
            if conn.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
                raise HTTPException(409, "演示服务正在执行任务，请等待回执")
            if guard is not None:
                guard()
            jid = uuid4().hex
            conn.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,'queued')", (jid, sid, key, kind, encoded)
            )
        self.executor.submit(self.execute, jid, sid, kind, payload)
        return {"execution_id": jid, "status": "queued", "replayed": False}

    @staticmethod
    def offline_model():
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

        class NoModel(FakeMessagesListChatModel):
            def bind_tools(self, *args, **kwargs):
                return self

        return NoModel(responses=[])

    def execute(self, jid, sid, kind, payload):
        final = {}
        stream = AnswerStreamCallback(self, sid, jid) if self.answer_streaming else None

        try:
            with self.tx() as conn:
                conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
            factory = (
                self.model_factory
                if kind in {"message", "resume"}
                else (lambda: nullcontext(self.offline_model()))
            )
            read_only = bool(payload.get("read_only")) or (
                kind in {"message", "resume"} and self.return_card(sid)["state"] == "UNKNOWN"
            )
            with (
                factory() as model,
                FormalReturnPoc(self.root, UID, model=model, fault=self.fault,
                                stream_callback=stream, read_only=read_only) as poc,
            ):
                if kind in {"message", "resume"}:
                    self.emit(sid, jid, "turn_started", status="running")
                    if kind == "resume":
                        binding = poc._binding(sid)
                        if binding["turn_id"]:
                            self.expect_answer_review(sid, binding["turn_id"])
                    for event in poc.stream(sid, payload.get("text")):
                        if event.type == "started":
                            self.expect_answer_review(sid, event.run_id)
                        if event.type == "updates":
                            for update in event.detail.values():
                                if not isinstance(update, dict):
                                    continue
                                for message in update.get("messages", []):
                                    if isinstance(message, AIMessage):
                                        for call in message.tool_calls:
                                            self.emit(
                                                sid,
                                                jid,
                                                "tool_requested",
                                                name=call["name"],
                                                call_id=call["id"],
                                            )
                                    if isinstance(message, ToolMessage):
                                        self.emit(
                                            sid,
                                            jid,
                                            "tool_completed",
                                            name=message.name,
                                            call_id=message.tool_call_id,
                                            status=message.status,
                                        )
                        if event.type == "completed":
                            if self.fault:
                                self.fault("before_answer_review")
                            text = self.present_answer(sid, event.run_id, jid,
                                                       event.detail["answer"])
                            if stream:
                                stream.complete(text)
                            self.emit(sid, jid, "business_state_updated", status="read_current")
                            final = {"status": "completed", "text": text}
                else:
                    if kind == "decide":
                        self.emit(sid, jid, "submission_pending", status="checking")
                    result = poc.submission(
                        sid,
                        "open" if kind == "open" else "resume" if kind == "recover" else "decide",
                        wait_id=payload.get("wait_id"),
                        decision=payload.get("decision"),
                        fault=self.fault,
                    )
                    if result["status"] == "waiting":
                        self.emit(
                            sid,
                            jid,
                            "confirmation_required",
                            status="waiting",
                            wait_id=result["wait_id"],
                            preview_id=result["interrupt"]["preview_id"],
                        )
                    elif result.get("formal_submission"):
                        verified = read_submission_receipt(self.root, sid, UID, result["wait_id"])
                        self.emit(
                            sid,
                            jid,
                            "submission_verified",
                            status="pending_review",
                            request_id=verified["request"]["request_id"],
                        )
                    else:
                        self.emit(sid, jid, "business_state_updated", status=result["status"])
                    final = {"status": result["status"]}
            with self.tx() as conn:
                conn.execute("UPDATE jobs SET status='completed' WHERE id=?", (jid,))
                self._emit(conn, sid, jid, "turn_completed", **final)
        except Exception as exc:
            if stream:
                stream.discard("execution_incomplete")
            # Exceptions/raw SDK/Graph messages stay private; HTTP failure is not proof
            # of business failure. Client must query authoritative SQL before retrying.
            private = self.root / ".private-evidence"
            private.mkdir(mode=0o700, exist_ok=True)
            evidence = private / f"web-error-{jid}.json"
            evidence.write_text(
                json.dumps(
                    {
                        "execution_id": jid,
                        "error_type": type(exc).__name__,
                        "message": scrub(str(exc), self.secret_values),
                    }
                )
            )
            evidence.chmod(0o600)
            reason, status = "query_authoritative_state", "unknown"
            if kind in {"message", "resume"}:
                reason, status = "conversation_incomplete", "failed"
                if isinstance(exc, ModelResponseError):
                    reason = "model_response_rejected"
                budget = next((error for error in (
                    exc, exc.__cause__, getattr(exc.__cause__, "__cause__", None))
                    if isinstance(error, BudgetExceeded)), None)
                if budget:
                    code, status = str(budget), "stopped"
                    reason = ("budget_exhausted" if code.endswith("_limit") or code in {
                        "episode_deadline", "model_timeout"} else "model_response_rejected"
                        if code == "adapter_protocol_error" else "model_receipt_unverified"
                        if code in {"usage_missing", "invalid_usage", "state_uncertain",
                                    "model_request_failed", "model_interrupted"}
                        else "execution_stopped")
            with self.tx() as conn:
                conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (jid,))
                self._emit(
                    conn,
                    sid,
                    jid,
                    "turn_failed",
                    status=status,
                    reason=reason,
                )

    def events(self, sid, after):
        with self.read() as conn:
            return [
                {
                    "id": r["id"],
                    "execution_id": r["job_id"],
                    "type": r["kind"],
                    "data": json.loads(r["data"]),
                }
                for r in conn.execute(
                    "SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT 100",
                    (sid, after),
                )
            ]

    def expect_answer_review(self, sid, tid):
        # Recorded before native model execution: a crash after native finish must
        # never misclassify this new raw answer as an unchecked legacy answer.
        with self.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO answer_presentations VALUES(?,?,NULL,NULL)",
                         (sid, tid))

    def present_answer(self, sid, tid, jid, raw):
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        with self.read() as conn:
            row = conn.execute("SELECT * FROM answer_presentations WHERE session_id=? "
                               "AND turn_id=?", (sid, tid)).fetchone()
        if row:
            if row["raw_sha256"] is not None and row["raw_sha256"] != fingerprint:
                raise ReconciliationError("Answer presentation no longer matches raw message")
            if row["text"] is not None:
                return row["text"]
        with self.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO answer_presentations VALUES(?,?,NULL,NULL)",
                         (sid, tid))
            conn.execute("UPDATE answer_presentations SET raw_sha256=? WHERE session_id=? "
                         "AND turn_id=?", (fingerprint, sid, tid))
        evidence = collect_evidence(self.gateway, self.root, sid, UID, tid)
        review = review_answer(raw, evidence)
        # Preserve the original privately BEFORE publishing a corrected presentation.
        digest = ResponseEvidence(self.root / ".private-evidence",
                                  secrets=self.secret_values).save(uuid4().hex, {
            "kind": "answer_grounding", "session_id": sid, "turn_id": tid,
            "raw_answer": raw, "raw_sha256": fingerprint,
            "evidence": evidence.model_dump(), "review": review.model_dump(),
        })
        predicates = sorted({f.predicate for f in review.findings})
        with self.tx() as conn:
            conn.execute("UPDATE answer_presentations SET text=? WHERE session_id=? "
                         "AND turn_id=? AND raw_sha256=?", (review.text, sid, tid, fingerprint))
            self._emit(conn, sid, jid, "answer_checked",
                       status="corrected" if any(f.action == "corrected" for f in review.findings)
                       else "unverified" if predicates else "scoped_check",
                       reason=",".join(predicates) if predicates else "scoped_rules_only",
                       call_id=digest)
        return review.text

    def display_history(self, sid):
        with closing(self.store._connect()) as conn:
            self.store._require_owner(conn, sid, UID)
            turns = conn.execute("SELECT turn_id,user_text,status,answer FROM "
                                 "conversation_turns WHERE session_id=? ORDER BY sequence",
                                 (sid,)).fetchall()
        with self.read() as conn:
            displays = {r["turn_id"]: r for r in conn.execute(
                "SELECT * FROM answer_presentations WHERE session_id=?", (sid,))}
        history = []
        for turn in turns:
            history.append({"role": "user", "content": turn["user_text"]})
            if turn["status"] != "running":
                display = displays.get(turn["turn_id"])
                if (display and display["raw_sha256"] is not None
                        and display["raw_sha256"] != hashlib.sha256(
                            turn["answer"].encode()).hexdigest()):
                    raise ReconciliationError("Answer presentation hash mismatch")
                history.append({"role": "assistant", "content": (
                    display["text"] if display["text"] is not None else
                    "此回答的业务表述尚未完成核验，请先只读查询业务状态。"
                ) if display else turn["answer"],
                    "review_status": "reviewed" if display and display["text"] is not None
                    else "pending" if display else "legacy_unchecked"})
        return history

    def snapshot(self, sid):
        # A completed job implies the following SQL/display reads are already durable.
        # Reading jobs last could pair a new terminal status with an earlier history.
        with self.read() as conn:
            jobs = [dict(r) for r in conn.execute(
                "SELECT id,kind,status FROM jobs WHERE session_id=? ORDER BY rowid", (sid,))]
        state = self.gateway.state(sid, UID)
        task = state["task"].get("task") or {}
        preview = state["preview"]
        result = {
            "draft": pick(
                task,
                (
                    "status",
                    "revision",
                    "order_id",
                    "item_id",
                    "quantity",
                    "reason",
                    "condition",
                    "secondary_sale_ok",
                ),
            ),
            "preview": (
                {
                    "preview_id": preview["preview_id"],
                    "expires_at": preview["expires_at"],
                    "content": public_content(preview["content"]),
                }
                if preview
                else None
            ),
            "history": self.display_history(sid),
            "confirmation": None,
            "requests": [],
            "orders": [
                pick(o, ("order_id", "status", "total_amount")) | {
                    "items": [pick(i, ("id", "product_id", "product_name", "quantity"))
                              for i in self.gateway.registry.db.get_order(
                                  o["order_id"], UID).get("items", [])]}
                for o in self.gateway.registry.db.list_orders(UID)
            ],
            "products": [],
            "jobs": jobs,
        }
        with closing(self.store._connect()) as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            waits = (
                conn.execute(
                    "SELECT * FROM local_confirmation_waits WHERE session_id=? ORDER BY rowid DESC",
                    (sid,),
                ).fetchall()
                if ("local_confirmation_waits" in tables)
                else []
            )
        for row in waits:
            if row["status"] == "accepted":
                try:
                    receipt = read_submission_receipt(self.root, sid, UID, row["wait_id"])
                    if receipt:
                        result["requests"].append(pick(receipt["request"], (
                            "request_id", "status", "order_id", "item_id", "quantity", "created_at"
                        )))
                except ReconciliationError:
                    result["submission_query_status"] = "unknown"
            if result["confirmation"] is None:
                result["confirmation"] = pick(dict(row), ("wait_id", "status", "preview_id"))
        # A concurrent tab can refresh between business reads. Never pair a wait
        # for a different preview with the currently displayed preview's consent UI.
        wait = result["confirmation"]
        if (
            wait
            and wait["status"] == "waiting"
            and (not preview or wait["preview_id"] != preview["preview_id"])
        ):
            wait["status"] = "refresh_required"
        with closing(self.store._connect()) as conn:
            seen = set()
            for row in conn.execute("SELECT result FROM local_receipts WHERE session_id=? "
                                    "AND name='get_product' AND error IS NULL ORDER BY rowid DESC",
                                    (sid,)):
                product = json.loads(row["result"])
                if product.get("found") and product["product_id"] not in seen:
                    seen.add(product["product_id"])
                    result["products"].append(pick(product, (
                        "product_id", "name", "current_price", "currency", "attributes")))

        return self.clean(result)

    def clean(self, value):
        """Shared recursive public boundary for both snapshots and return cards."""
        if isinstance(value, str):
            return scrub(value, self.secret_values)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {k: self.clean(v) for k, v in value.items()}
        return value

    def return_card(self, sid):
        """Read-only projection; no Graph, migrations, event consumption or new jobs."""
        with closing(
            sqlite3.connect(self.settings.db_path.as_uri() + "?mode=ro", uri=True)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            self.store._require_owner(conn, sid, UID)
            task = self.store._task(conn, sid) or {}
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            row = (
                conn.execute(
                    "SELECT * FROM local_confirmation_waits WHERE session_id=? "
                    "AND user_id=? ORDER BY rowid DESC LIMIT 1", (sid, UID)
                ).fetchone()
                if "local_confirmation_waits" in tables else None
            )
            wait = dict(row) if row else None
        public_wait = (
            pick(wait, ("wait_id", "status", "preview_id", "revision", "expires_at", "decision"))
            if wait else None
        )
        public_preview = None

        def card(state_name, reason, actions, *, request=None, authoritative=False):
            return self.clean(ReturnCard(
                state=state_name, status=state_name, allowed_actions=actions, reason=reason,
                preview=public_preview, confirmation=public_wait, request=request,
                authoritative=authoritative,
            ).model_dump())

        # Verify the complete receipt before consulting live preview/policy or Graph.
        if wait and wait["status"] == "accepted":
            try:
                receipt = read_submission_receipt(self.root, sid, UID, wait["wait_id"])
            except (ReconciliationError, ValueError, KeyError, TypeError):
                return card("UNKNOWN", "submission_result_unknown", ["refresh"])
            if receipt is None:
                return card("UNKNOWN", "submission_result_unknown", ["refresh"])
            if task.get("request_id") == receipt["request"]["request_id"]:
                return card(
                    "PENDING_REVIEW", "submission_verified", ["refresh"],
                    request=pick(receipt["request"], (
                        "request_id", "status", "order_id", "item_id", "quantity", "created_at"
                    )), authoritative=True,
                )

        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            jobs = conn.execute(
                "SELECT kind,payload,status FROM jobs WHERE session_id=? ORDER BY rowid DESC",
                (sid,),
            ).fetchall()
        for job in jobs:
            payload = json.loads(job["payload"])
            relevant = (
                job["kind"] in {"decide", "recover"}
                and wait and payload.get("wait_id") == wait["wait_id"]
            ) or (job["kind"] == "open" and (not wait or wait["status"] == "waiting"))
            if relevant:
                if job["status"] != "completed":
                    reason = (
                        "operation_in_progress" if job["status"] in {"queued", "running"}
                        else "submission_result_unknown"
                    )
                    return card("UNKNOWN", reason, ["refresh"])
                break
        if wait and wait["status"] == "issued":
            return card("UNKNOWN", "submission_result_unknown", ["refresh"])

        preview = self.gateway.service.latest_preview(sid, UID)
        if preview:
            public_preview = {
                "preview_id": preview["preview_id"], "expires_at": preview["expires_at"],
                "content": public_content(preview["content"]),
            }
        same_preview = bool(preview and wait and preview["preview_id"] == wait["preview_id"])
        if wait and wait["status"] == "waiting":
            if same_preview:
                return card("READY", "awaiting_application_decision", [
                    "accept", "reject", "cancel", "refresh"
                ])
            # Explicitly closing a stale wait uses the existing M6 cancel endpoint.
            # It cannot accept, create a request, or consume a different preview.
            return card("EXPIRED", "preview_expired_or_stale", ["cancel", "refresh"])
        if wait and (same_preview or not preview):
            if wait["status"] in {"rejected", "cancelled"}:
                return card("REJECTED", "confirmation_rejected", ["refresh"])
            if wait["status"] == "invalidated":
                return card("EXPIRED", "preview_expired_or_stale", ["refresh"])
        if preview:
            # Keep the SQL terminal decision available to reconcile a lost HTTP
            # reply, but do not bind its actions to this different new preview.
            if public_wait:
                public_wait = public_wait | {"is_current": False}
            return card("READY", "preview_available", ["open", "refresh"])
        return card("EXPIRED", "no_valid_preview", ["refresh"])


def create_app(
    root, model_factory, *, origin="http://127.0.0.1:8767", fault=None, secret_values=(),
    runtime_mode="custom",
    answer_streaming=False,
    budget_notice=None,
):
    modes = {
        "fixed_script": {
            "label": "固定脚本 · 免费离线测试",
            "notice": "当前不是 DeepSeek 自由对话，仅响应演示用例。查询展示真实工具结果；"
            "SSE只显示工具执行进度，正文完成后整段显示，不是逐字流式。",
        },
        "deepseek": {
            "label": "DeepSeek · 受限真实模型",
            "notice": "当前使用真实模型，会产生费用并接受预算保护。"
            "SSE只显示工具执行进度，正文完成后整段显示，不是逐字流式。",
        },
        "custom": {
            "label": "测试模型 · 非生产客服",
            "notice": "当前使用服务端配置的测试模型。"
            "SSE只显示工具执行进度，正文不是逐字流式。",
        },
    }
    if runtime_mode not in modes:
        raise ValueError("Unknown server runtime mode")
    runtime_info = dict(
        mode=runtime_mode, answer_streaming=answer_streaming, progress_streaming=True,
        **modes[runtime_mode],
    )
    if answer_streaming:
        runtime_info["notice"] = (
            ("真实DeepSeek，受3元/24次请求预算保护。" if runtime_mode == "deepseek"
             else "流式测试模型。")
            + "正文来自模型实时片段；生成中的文字仅为草稿，可能撤回。业务提交只看权威SQL回执。"
        )
    if budget_notice:
        runtime_info["notice"] += budget_notice
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path:
        raise ValueError("Demo requires a loopback HTTP origin")
    engine = WebEngine(Path(root), model_factory, fault=fault, secret_values=secret_values,
                       answer_streaming=answer_streaming)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(engine.start)
        try:
            yield
        finally:
            await run_in_threadpool(engine.stop)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host") != parsed.netloc:
            return JSONResponse({"detail": "仅允许本机同源访问"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "禁止跨站请求"}, status_code=403)
        if len(await request.body()) > 16384:
            return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail": "请求格式无效或包含未允许的字段"}, 422)

    @app.exception_handler(sqlite3.Error)
    async def db_unavailable(request, exc):
        if request.url.path.endswith("/return-card"):
            return JSONResponse(ReturnCard(
                state="UNKNOWN", status="UNKNOWN", allowed_actions=["refresh"],
                reason="database_unavailable", authoritative=False,
            ).model_dump(), 503)
        return JSONResponse({"detail": "业务状态暂不可核实，请保留现场并查询；不要重复提交"}, 503)

    @app.exception_handler(PermissionError)
    async def permission(request, exc):
        return JSONResponse({"detail": "无权访问"}, 403)

    @app.exception_handler(ReconciliationError)
    async def uncertain_result(request, exc):
        return JSONResponse({"detail": "业务回执暂不可核实，请只读查询，不要重复提交"}, 503)

    def auth(request, sid=None, mutate=False):
        browser = engine.browser(request.cookies.get(COOKIE))
        if browser is None:
            raise HTTPException(401, "请从演示首页建立浏览器会话")
        if mutate and (
            request.headers.get("origin") != origin
            or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), browser["csrf"])
        ):
            raise HTTPException(403, "会话凭证或CSRF校验失败")
        if sid is not None:
            engine.own(browser, sid)
        return browser

    @app.get("/")
    def page(request: Request):
        response = FileResponse(ASSETS / "index.html")
        if engine.browser(request.cookies.get(COOKIE)) is None:
            response.set_cookie(
                COOKIE,
                engine.bootstrap(),
                httponly=True,
                samesite="strict",
                max_age=86400,
                path="/",
            )
        return response

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js", "style.css"}:
            raise HTTPException(404)
        return FileResponse(ASSETS / name)

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        browser = auth(request)
        return {
            "csrf": browser["csrf"], "session_id": browser["session_id"], "runtime": runtime_info
        }

    @app.post("/api/sessions")
    def session(request: Request, body: StrictBody):
        return {"session_id": engine.create_session(auth(request, mutate=True))}

    @app.get("/api/sessions/{sid}")
    def snapshot(request: Request, sid: str):
        auth(request, sid)
        return engine.snapshot(sid)

    @app.get("/return-card")
    def return_card(request: Request):
        browser = auth(request)
        if browser["session_id"] is None:
            return ReturnCard(
                state="EXPIRED",
                status="EXPIRED",
                allowed_actions=[],
                reason="session_not_created",
                authoritative=False,
            )
        return engine.return_card(browser["session_id"])

    @app.get("/api/sessions/{sid}/return-card")
    def session_return_card(request: Request, sid: str):
        auth(request, sid)
        return engine.return_card(sid)

    @app.post("/api/sessions/{sid}/messages", status_code=202)
    def message(request: Request, sid: str, body: MessageBody):
        auth(request, sid, True)
        if not body.text.strip():
            raise HTTPException(422, "消息不能为空")
        return engine.submit(sid, body.request_key, "message",
                             {"text": body.text, "read_only": body.read_only})

    @app.post("/api/sessions/{sid}/resume", status_code=202)
    def resume(request: Request, sid: str, body: ActionBody):
        auth(request, sid, True)
        return engine.submit(sid, body.request_key, "resume", {})

    @app.post("/api/sessions/{sid}/confirmation", status_code=202)
    def open_wait(request: Request, sid: str, body: ActionBody):
        auth(request, sid, True)
        def can_open():
            if "open" not in engine.return_card(sid)["allowed_actions"]:
                raise HTTPException(409, "当前预览没有可打开的确认等待，请刷新业务状态")

        return engine.submit(sid, body.request_key, "open", {}, guard=can_open)

    @app.post("/api/sessions/{sid}/decision", status_code=202)
    def decide(request: Request, sid: str, body: DecisionBody):
        auth(request, sid, True)
        with closing(engine.store._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM local_confirmation_waits WHERE wait_id=? "
                "AND session_id=? AND user_id=?",
                (body.wait_id, sid, UID),
            ).fetchone()
            if row is None:
                raise HTTPException(403, "确认等待不属于当前会话")

        def known_result():
            if engine.return_card(sid)["state"] == "UNKNOWN":
                raise HTTPException(409, "原确认结果未核实，请只读查询，不要再次确认")

        return engine.submit(
            sid, body.request_key, "decide", {"wait_id": body.wait_id, "decision": body.decision},
            guard=known_result,
        )

    @app.post("/api/sessions/{sid}/confirmation/{wait_id}/resume", status_code=202)
    def recover_confirmation(request: Request, sid: str, wait_id: str, body: ActionBody):
        auth(request, sid, True)
        with closing(engine.store._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM local_confirmation_waits WHERE wait_id=? "
                "AND session_id=? AND user_id=?",
                (wait_id, sid, UID),
            ).fetchone()
            if row is None:
                raise HTTPException(403, "确认等待不属于当前会话")
        return engine.submit(sid, body.request_key, "recover", {"wait_id": wait_id})

    @app.get("/api/sessions/{sid}/receipts/{wait_id}")
    def receipt(request: Request, sid: str, wait_id: str):
        auth(request, sid)
        result = read_submission_receipt(engine.root, sid, UID, wait_id)
        return {
            "verified": bool(result),
            "request": pick(
                result["request"], ("request_id", "status", "order_id", "item_id", "quantity")
            )
            if result
            else None,
        }

    @app.get("/api/sessions/{sid}/events")
    async def events(request: Request, sid: str, after: int = 0, once: bool = False):
        auth(request, sid)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
            if cursor < 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(422, "无效事件序号") from exc

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = await run_in_threadpool(engine.events, sid, cursor)
                for row in rows:
                    cursor = row["id"]
                    yield (
                        f"id: {cursor}\nevent: {row['type']}\n"
                        f"data: {json.dumps(row, ensure_ascii=False)}\n\n"
                    )
                if once:
                    break
                if not rows:
                    yield ": waiting\n\n"
                    await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    return app
