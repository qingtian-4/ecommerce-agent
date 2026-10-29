"""Restricted local business gateway. Native Graph never owns business facts."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .config import PROJECT_ROOT, Settings
from .cs_tools import CatalogArgs, OrderListArgs, ToolRegistry, catalog_model_view, catalog_progress
from .db_seed import build
from .langgraph_poc import EmptyArgs, OrderArgs, ProductArgs
from .returns import ReturnService, scoped_task_view
from .sessions import SessionStore

FORMAL_FIXTURE_KIND = "local-return-formal-synthetic-1"

WRITES = {"update_return_task", "cancel_return_task", "preview_return"}


class DraftArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str | None = None
    item_id: int | None = None
    quantity: int | None = None
    reason: Literal["no_reason", "quality"] | None = None
    condition: Literal["sealed", "opened"] | None = None
    problem_description: str | None = None
    secondary_sale_ok: bool | None = None
    quality_non_user_damage: bool | None = None


class SearchArgs(EmptyArgs):
    query: str
    document_id: str | None = None


TOOLS = {
    **dict.fromkeys(
        (
            "get_return_task",
            "cancel_return_task",
            "preview_return",
            "get_return_requests",
        ),
        EmptyArgs,
    ),
    "list_products": CatalogArgs,
    "list_orders": OrderListArgs,
    "get_product": ProductArgs,
    "get_price_history": ProductArgs,
    "get_order": OrderArgs,
    "get_logistics": OrderArgs,
    "update_return_task": DraftArgs,
    "search_knowledge": SearchArgs,
}


class ReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnContext:
    session_id: str
    user_id: str
    turn_id: str
    route_hint: str = ""


class BoundStore(SessionStore):
    """Reuse business implementations inside the gateway's SAME SQLite transaction.

    Constructed only after the real SessionStore migrated the isolated fixture.
    No connection is supplied by model arguments or retained in Graph State.
    """

    def __init__(self, path, connection):
        self.db_path, self.connection = path, connection

    @contextmanager
    def transaction(self):
        yield self.connection


def fixture_settings(
    root: Path, base: Settings | None = None, *, kind="local-return-draft-only-1"
) -> Settings:
    if root.is_symlink():
        raise ValueError("Fixture root must not be a symlink")
    root = root.resolve()
    if any((root / p).is_symlink() for p in ("business.sqlite", "graph.sqlite", "docs")):
        raise ValueError("Fixture paths must not be symlinks")
    marker = json.loads((root / "fixture.json").read_text())
    if marker["kind"] != kind:
        raise ValueError("Not an isolated draft fixture")
    db = root / "business.sqlite"
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("SELECT id FROM local_fixture").fetchone()[0] != marker["id"]:
            raise ValueError("Fixture identity mismatch")
        triggers = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        required = {
            f"local_deny_{t}_{a}"
            for t in ("return_requests", "return_confirmations", "return_reviews", "return_audit")
            for a in ("INSERT", "UPDATE", "DELETE")
        }
        if kind == FORMAL_FIXTURE_KIND:
            if conn.execute("SELECT kind FROM local_fixture").fetchone()[0] != kind:
                raise ValueError("Not a newly initialized formal fixture")
            required = set(formal_barriers())
            actual = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            if any(actual.get(name) != sql for name, sql in formal_barriers().items()):
                raise ValueError("Formal fixture submission barriers changed")
        elif kind != "local-return-draft-only-1":
            raise ValueError("Unknown fixture kind")
        if not required.issubset(triggers):
            raise ValueError("Fixture submission barriers are incomplete")
    return (base or Settings(_env_file=None)).model_copy(
        update={
            "db_path": db,
            "docs_dir": root / "docs",
            "index_dir": root / "index",
            "reference_date": date.fromisoformat(marker["reference_date"]),
            "retrieval_mode": "bm25",
            "reranker_enabled": False,
            "evidence_backfill": False,
        }
    )


def formal_barriers():
    barriers = {}
    for table in (
        "return_requests",
        "return_confirmations",
        "return_audit",
        "return_reviews",
        "orders",
        "order_items",
        "logistics",
    ):
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = f"formal_guard_{table}_{action}"
            allowed = action == "INSERT" and table in {
                "return_requests",
                "return_confirmations",
                "return_audit",
            }
            condition = (
                "EXISTS(SELECT 1 FROM local_confirmation_waits w "
                "WHERE w.event_id=formal_submission_event() AND w.status='accepted' "
                "AND w.decision='accept' AND w.consumptions=1)"
                if allowed
                else "0"
            )
            if table == "return_requests" and allowed:
                condition += (
                    " AND NEW.status='pending_review' AND EXISTS(SELECT 1 "
                    "FROM local_confirmation_waits w WHERE w.event_id=formal_submission_event() "
                    "AND w.preview_id=NEW.preview_id AND w.session_id=NEW.session_id "
                    "AND w.user_id=NEW.user_id)"
                )
            if table == "return_confirmations" and allowed:
                condition += (
                    " AND EXISTS(SELECT 1 FROM local_confirmation_waits w "
                    "WHERE w.event_id=formal_submission_event() "
                    "AND w.preview_id=NEW.preview_id AND w.user_id=NEW.user_id)"
                )
            if table == "return_audit" and allowed:
                condition += (
                    " AND NEW.action='created' AND EXISTS(SELECT 1 FROM return_requests r "
                    "JOIN local_confirmation_waits w ON r.preview_id=w.preview_id "
                    "WHERE w.event_id=formal_submission_event() AND r.request_id=NEW.request_id "
                    "AND NEW.actor=w.user_id)"
                )
            barriers[name] = (
                f"CREATE TRIGGER {name} BEFORE {action} ON {table} "
                f"BEGIN SELECT CASE WHEN NOT ({condition}) "
                "THEN RAISE(ABORT,'formal fixture requires trusted submission') END; END"
            )
    return barriers


def init_fixture(root: Path):
    return _init_fixture(root, "local-return-draft-only-1")


def _init_fixture(root: Path, kind, barriers=None):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    settings = Settings(_env_file=None, db_path=root / "business.sqlite")
    build(settings.db_path, reference_date=date.today())
    settings.db_path.chmod(0o600)
    store = SessionStore(settings.db_path)
    marker = {
        "kind": kind,
        "id": uuid4().hex,
        "reference_date": date.today().isoformat(),
    }
    shutil.copytree(PROJECT_ROOT / "data/docs", root / "docs")
    with store.transaction() as conn:
        conn.execute("CREATE TABLE local_fixture(id TEXT PRIMARY KEY,kind TEXT NOT NULL)")
        conn.execute("INSERT INTO local_fixture VALUES(?,?)", (marker["id"], kind))
        conn.execute(
            "CREATE TABLE local_threads(session_id TEXT PRIMARY KEY, "
            "thread_id TEXT UNIQUE NOT NULL, turn_id TEXT, offset INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE local_receipts(session_id TEXT, turn_id TEXT, "
            "call_id TEXT, name TEXT, arguments TEXT, result TEXT, error TEXT, "
            "revision_before INTEGER, revision_after INTEGER, "
            "PRIMARY KEY(session_id,turn_id,call_id))"
        )
        if barriers is not None:
            for sql in barriers.values():
                conn.execute(sql)
        else:
            for table in (
                "return_requests", "return_confirmations", "return_reviews", "return_audit"
            ):
                for action in ("INSERT", "UPDATE", "DELETE"):
                    conn.execute(
                        f"CREATE TRIGGER local_deny_{table}_{action} BEFORE {action} "
                        f'ON {table} BEGIN SELECT RAISE(ABORT,"draft fixture forbids submission"); '
                        "END"
                    )
    (root / "fixture.json").write_text(json.dumps(marker, indent=2) + "\n")
    return marker


def model_view(value):
    """Keep application authority out of model messages; preserve business claims."""
    if isinstance(value, list):
        return [model_view(v) for v in value]
    if isinstance(value, dict):
        return catalog_model_view({
            k: model_view(v)
            for k, v in value.items()
            if k not in {"user_id", "session_id", "turn_id", "current_user_id"}
        })
    return value


class LocalKnowledge:
    """Reuse the existing source chunker/BM25; no unused dense index or embedding API."""

    def __init__(self, settings):
        self.settings = settings

    def search(self, query):
        from .rag import KnowledgeBase
        from .retrieval import LexicalIndex

        chunks = KnowledgeBase(self.settings).load_documents()
        return LexicalIndex(chunks).search(query, self.settings.top_k)


class LocalReturnGateway:
    preview_notice = "隔离预览，尚未创建申请；本实验不提供提交、审核或退款。"
    application_confirmation_enabled = False

    def __init__(self, settings, *, registry=None, fault=None):
        self.settings, self.store = settings, SessionStore(settings.db_path)
        self.registry = registry or ToolRegistry(settings, kb=LocalKnowledge(settings))
        self.service = ReturnService(settings, self.store)
        self.fault = fault or (lambda point: None)

    def active(self, conn, context):
        self.store._require_owner(conn, context.session_id, context.user_id)
        row = conn.execute(
            "SELECT 1 FROM conversation_turns t JOIN local_threads g "
            "ON t.session_id=g.session_id AND t.turn_id=g.turn_id WHERE t.session_id=? "
            'AND t.turn_id=? AND t.status="running"',
            (context.session_id, context.turn_id),
        ).fetchone()
        if row is None:
            raise ReconciliationError("No current application-authorized Turn")

    def execute(
        self, name, arguments, call_id, context, *, expected_revision=None, prior_writes=()
    ):
        if name not in TOOLS:
            raise PermissionError("Tool not exposed by local experiment")
        args = TOOLS[name].model_validate(arguments).model_dump(exclude_unset=True)
        encoded = json.dumps(args, ensure_ascii=False, sort_keys=True)
        key = (context.session_id, context.turn_id, call_id)
        with self.store.transaction() as conn:
            self.active(conn, context)  # Always fresh, including replay and previews.
            row = conn.execute(
                "SELECT * FROM local_receipts WHERE session_id=? AND turn_id=? AND call_id=?", key
            ).fetchone()
            if row:
                if (row["name"], row["arguments"]) != (name, encoded):
                    raise ReconciliationError("Tool Call ID reused with different payload")
                result, error = json.loads(row["result"]), row["error"]
                replayed = True
            else:
                before = (self.store._task(conn, context.session_id) or {}).get("revision", 0)
                if name in WRITES and expected_revision is not None:
                    expected = expected_revision
                    for previous_id in prior_writes:
                        previous = conn.execute(
                            "SELECT revision_before,revision_after FROM "
                            "local_receipts WHERE session_id=? AND turn_id=? AND call_id=?",
                            (context.session_id, context.turn_id, previous_id),
                        ).fetchone()
                        if previous is None or previous["revision_before"] != expected:
                            raise ReconciliationError("Incomplete batch receipt chain")
                        expected = previous["revision_after"]
                    if before != expected:
                        raise ReconciliationError("Draft changed after model decision")
                registry = ToolRegistry(
                    self.settings, db=self.registry.db, kb=self.registry.kb, llm=self.registry._llm
                )
                registry.bind_return_task(
                    BoundStore(self.settings.db_path, conn), context.session_id, context.turn_id
                )
                conn.execute("SAVEPOINT local_business")
                result, _, error = registry.run(name, encoded, current_user_id=context.user_id)
                if name == "preview_return" and result.get("ready"):
                    result = result | {"notice": self.preview_notice}
                if error:
                    conn.execute("ROLLBACK TO local_business")
                conn.execute("RELEASE local_business")
                after = (self.store._task(conn, context.session_id) or {}).get("revision", 0)
                conn.execute(
                    "INSERT INTO local_receipts VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        *key,
                        name,
                        encoded,
                        json.dumps(result, ensure_ascii=False),
                        error,
                        before,
                        after,
                    ),
                )
                self.fault("before_commit")
                replayed = False
        self.fault("after_commit")  # Can lose delivery; durable receipt remains authoritative.
        if name == "preview_return" and result.get("ready"):
            latest = self.service.latest_preview(context.session_id, context.user_id)
            if not latest or latest["preview_id"] != result["preview_id"]:
                raise ReconciliationError("Recorded preview is now stale; no automatic re-preview")
        current_task = (self.store.get_return_task(context.session_id, context.user_id)
                        if name in WRITES else None)
        if current_task is not None:
            scoped = scoped_task_view(self.settings, current_task, context.user_id)
            if scoped["policy"].get("applicable") is False:
                current_task = scoped
        return model_view(
            {
                "result": result,
                "error": error,
                "replayed": replayed,
                "current_task": current_task,
            }
        )

    def catalog_queries(self, sid, uid):
        with closing(self.store._connect()) as conn:
            self.store._require_owner(conn, sid, uid)
            return catalog_progress([dict(r) for r in conn.execute(
                "SELECT name,arguments,result,error FROM local_receipts WHERE session_id=? "
                "AND turn_id=(SELECT turn_id FROM local_threads WHERE session_id=?) ORDER BY rowid",
                (sid, sid))])

    def state(self, sid, uid):
        with closing(self.store._connect()) as conn:
            self.store._require_owner(conn, sid, uid)
            preview_count = conn.execute(
                "SELECT COUNT(*) FROM return_previews WHERE session_id=?", (sid,)
            ).fetchone()[0]
        catalogs = self.catalog_queries(sid, uid)
        return model_view(
            {
                "task": scoped_task_view(self.settings, self.store.get_return_task(sid, uid), uid),
                "preview": self.service.latest_preview(sid, uid),
                "submission_enabled": False,
                "preview_record_count": preview_count,
                **({"catalog_queries": catalogs} if catalogs else {}),
                **(
                    {"application_confirmation_enabled": True}
                    if self.application_confirmation_enabled
                    else {}
                ),
            }
        )
