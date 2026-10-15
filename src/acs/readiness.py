"""Read-only local readiness checks, with no paid provider requests."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from .config import Settings
from .knowledge_context import load_source_documents
from .rag import KnowledgeBase
from .returns import return_policy


def check_ready(settings: Settings) -> dict:
    if not settings.db_path.is_file():
        raise RuntimeError("业务数据库不存在")
    with closing(sqlite3.connect(settings.db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("数据库完整性检查失败")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )}
        required = {"users", "products", "orders", "order_items", "price_history", "logistics",
                    "conversations", "conversation_turns", "return_requests", "return_reviews",
                    "product_skus", "order_item_skus", "acs_migrations"}
        if not required <= tables:
            raise RuntimeError("数据库尚未完成迁移")
        version = conn.execute("SELECT MAX(version) FROM acs_migrations").fetchone()[0]
        if version != 5:
            raise RuntimeError("数据库版本不匹配")
    documents = load_source_documents(settings)
    return_policy(settings)
    if settings.knowledge_mode == "retrieval":
        KnowledgeBase(settings).load()
    return {"ready": True, "schema_version": version, "documents": len(documents),
            "knowledge_mode": settings.knowledge_mode, "provider_checked": False}
