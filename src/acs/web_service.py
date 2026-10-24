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
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing, contextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .local_return_tools import LocalReturnGateway, fixture_settings
from .local_submission import KIND, FormalReturnPoc, read_submission_receipt

COOKIE = "v3_demo"
ASSETS = Path(__file__).with_name("web_assets")
UID = "USER001"  # Synthetic application identity, never accepted from HTTP input.


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MessageBody(StrictBody):
    text: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(pattern=r"^[a-zA-Z0-9-]{8,64}$")


class ActionBody(StrictBody):
    request_key: str = Field(pattern=r"^[a-zA-Z0-9-]{8,64}$")


class DecisionBody(ActionBody):
    wait_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    decision: str = Field(pattern=r"^(accept|reject|cancel)$")


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


class WebEngine:
    def __init__(self, root, model_factory, *, fault=None, secret_values=()):
        self.root, self.model_factory = root.resolve(), model_factory
        self.fault, self.secret_values = fault, secret_values
        self.settings = fixture_settings(root, kind=KIND)
        self.gateway = LocalReturnGateway(self.settings)
        self.store = self.gateway.store
        with closing(self.store._connect()) as conn:
            identity = [str(v) for row in conn.execute("SELECT name,phone FROM users") for v in row]
        self.secret_values = (*secret_values, *identity)
        self.db = root / "web.sqlite"
        self.lock_file = None

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.db, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def start(self):
        self.lock_file = (self.root / "web-worker.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
            self.lock_file.close()
            raise

    def stop(self):
        self.executor.shutdown(wait=True)  # Don't close Graph/SDK while a job still owns it.
        self.lock_file.close()

    def browser(self, token):
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.tx() as conn:
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

    def submit(self, sid, key, kind, payload):
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
        try:
            with self.tx() as conn:
                conn.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
            factory = (
                self.model_factory
                if kind in {"message", "resume"}
                else (lambda: nullcontext(self.offline_model()))
            )
            with (
                factory() as model,
                FormalReturnPoc(self.root, UID, model=model, fault=self.fault) as poc,
            ):
                if kind in {"message", "resume"}:
                    self.emit(sid, jid, "turn_started", status="running")
                    for event in poc.stream(sid, payload.get("text")):
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
                            self.emit(sid, jid, "business_state_updated", status="read_current")
                            final = {"status": "completed", "text": event.detail["answer"]}
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
            with self.tx() as conn:
                conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (jid,))
                self._emit(
                    conn,
                    sid,
                    jid,
                    "turn_failed",
                    status="unknown",
                    reason="query_authoritative_state",
                )

    def events(self, sid, after):
        with self.tx() as conn:
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

    def snapshot(self, sid):
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
            "history": [pick(m, ("role", "content")) for m in self.store.load(sid, UID)["history"]],
            "confirmation": None,
            "requests": [],
            "orders": [
                pick(o, ("order_id", "status", "total_amount"))
                for o in self.gateway.registry.db.list_orders(UID)
            ],
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
                receipt = read_submission_receipt(self.root, sid, UID, row["wait_id"])
                request = receipt["request"]
                result["requests"].append(
                    pick(
                        request,
                        ("request_id", "status", "order_id", "item_id", "quantity", "created_at"),
                    )
                )
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
        with self.tx() as conn:
            result["jobs"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,kind,status FROM jobs WHERE session_id=? ORDER BY rowid", (sid,)
                )
            ]

        # All nested strings, including user chat echoes, cross the same redaction boundary.
        def clean(value):
            if isinstance(value, str):
                return scrub(value, self.secret_values)
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            return value

        return clean(result)


def create_app(
    root, model_factory, *, origin="http://127.0.0.1:8767", fault=None, secret_values=()
):
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path:
        raise ValueError("Demo requires a loopback HTTP origin")
    engine = WebEngine(Path(root), model_factory, fault=fault, secret_values=secret_values)

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
        return JSONResponse({"detail": "业务状态暂不可核实，请保留现场并查询；不要重复提交"}, 503)

    @app.exception_handler(PermissionError)
    async def permission(request, exc):
        return JSONResponse({"detail": "无权访问"}, 403)

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
        return {"csrf": browser["csrf"], "session_id": browser["session_id"]}

    @app.post("/api/sessions")
    def session(request: Request, body: StrictBody):
        return {"session_id": engine.create_session(auth(request, mutate=True))}

    @app.get("/api/sessions/{sid}")
    def snapshot(request: Request, sid: str):
        auth(request, sid)
        return engine.snapshot(sid)

    @app.post("/api/sessions/{sid}/messages", status_code=202)
    def message(request: Request, sid: str, body: MessageBody):
        auth(request, sid, True)
        if not body.text.strip():
            raise HTTPException(422, "消息不能为空")
        return engine.submit(sid, body.request_key, "message", {"text": body.text})

    @app.post("/api/sessions/{sid}/resume", status_code=202)
    def resume(request: Request, sid: str, body: ActionBody):
        auth(request, sid, True)
        return engine.submit(sid, body.request_key, "resume", {})

    @app.post("/api/sessions/{sid}/confirmation", status_code=202)
    def open_wait(request: Request, sid: str, body: ActionBody):
        auth(request, sid, True)
        return engine.submit(sid, body.request_key, "open", {})

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
        return engine.submit(
            sid, body.request_key, "decide", {"wait_id": body.wait_id, "decision": body.decision}
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
