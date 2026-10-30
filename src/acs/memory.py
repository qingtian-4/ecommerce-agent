"""User-scoped, source-tagged memory for the customer-service agent.

The MVP has three stores:

- facts: explicit preferences/profile facts (durable, with source and expiry);
- summaries: a compact per-session summary;
- episodes: task outcomes worth recalling in a later session.

Memory is never authority: current tool/database facts win, and a memory can be
deleted by the user.  There is deliberately no vector database in this MVP.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

FACT_KINDS = {"preference", "profile", "fact"}
FACT_TTL_DAYS = 180
EPISODE_TTL_DAYS = 90
MAX_FACT_CHARS = 300
MAX_SUMMARY_CHARS = 900
PII = re.compile(r"1[3-9]\d{9}|\d{17}[\dXx]|[\w.+-]+@[\w-]+\.[\w.]+")
FACT_PATTERNS = (
    re.compile(r"(?:请?记住|以后都?按|以后都?用|以后推荐|以后只)[：:,，\s]*(.{2,200})"),
    re.compile(r"(?:我偏好|我喜欢|我习惯|我一般)[：:,，\s]*(.{2,200})"),
    re.compile(r"(?:我叫|称呼我)[：:,，\s]*(.{1,40})"),
)
EPISODE_TOOLS = {
    "list_orders",
    "get_order",
    "get_logistics",
    "list_products",
    "get_product",
    "search_knowledge",
    "get_return_task",
    "update_return_task",
    "cancel_return_task",
    "preview_return",
    "get_return_requests",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _expires(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _clip(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    return value[:limit]


def extract_facts(user_text: str) -> list[tuple[str, str]]:
    """Extract only explicit, non-PII preference/profile statements."""
    text = (user_text or "").strip()
    if not text or PII.search(text):
        return []
    found: list[tuple[str, str]] = []
    for pattern in FACT_PATTERNS:
        for match in pattern.finditer(text):
            value = _clip(match.group(1), MAX_FACT_CHARS)
            if len(value) < 2:
                continue
            kind = (
                "profile"
                if "我叫" in match.group(0) or "称呼我" in match.group(0)
                else "preference"
            )
            item = (kind, value)
            if item not in found:
                found.append(item)
    # "记住我偏好入耳式" matches both the explicit-memory pattern and the
    # preference pattern; keep the longer enclosing statement, not its fragment.
    return [
        item
        for item in found
        if not any(
            other != item
            and other[0] == item[0]
            and item[1] in other[1]
            and len(other[1]) > len(item[1])
            for other in found
        )
    ]


@dataclass(frozen=True)
class MemoryFact:
    fact_id: str
    user_id: str
    kind: str
    text: str
    source_session_id: str | None
    source_turn_id: str | None
    confidence: float
    created_at: str
    expires_at: str | None


class MemoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_fact(
        self,
        user_id: str,
        text: str,
        *,
        kind: str = "preference",
        source_session_id: str | None = None,
        source_turn_id: str | None = None,
        confidence: float = 1.0,
        ttl_days: int = FACT_TTL_DAYS,
    ) -> MemoryFact:
        value = _clip(text, MAX_FACT_CHARS)
        if kind not in FACT_KINDS:
            raise ValueError("unknown memory kind")
        if len(value) < 2:
            raise ValueError("memory text is too short")
        if PII.search(value):
            raise ValueError("refuse to store PII")
        now, expires = _now(), _expires(ttl_days)
        fact_id = uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO memory_facts "
                "(fact_id,user_id,kind,text,source_session_id,source_turn_id,confidence,"
                "created_at,updated_at,expires_at,deleted_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,NULL) "
                "ON CONFLICT(user_id,kind,text) DO UPDATE SET "
                "updated_at=excluded.updated_at, expires_at=excluded.expires_at,"
                "deleted_at=NULL, confidence=excluded.confidence,"
                "source_session_id=excluded.source_session_id,"
                "source_turn_id=excluded.source_turn_id",
                (
                    fact_id,
                    user_id,
                    kind,
                    value,
                    source_session_id,
                    source_turn_id,
                    confidence,
                    now,
                    now,
                    expires,
                ),
            )
            row = conn.execute(
                "SELECT * FROM memory_facts WHERE user_id=? AND kind=? AND text=?",
                (user_id, kind, value),
            ).fetchone()
        return _fact(row)

    def list_facts(
        self,
        user_id: str,
        *,
        limit: int = 20,
        include_expired: bool = False,
    ) -> list[MemoryFact]:
        where = "user_id=? AND deleted_at IS NULL"
        params: list[Any] = [user_id]
        if not include_expired:
            where += " AND (expires_at IS NULL OR expires_at>?)"
            params.append(_now())
        params.append(limit)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_facts WHERE {where} "
                "ORDER BY updated_at DESC, fact_id LIMIT ?",
                params,
            ).fetchall()
        return [_fact(row) for row in rows]

    def delete_fact(self, user_id: str, fact_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "UPDATE memory_facts SET deleted_at=? "
                "WHERE fact_id=? AND user_id=? AND deleted_at IS NULL",
                (_now(), fact_id, user_id),
            )
        return cur.rowcount == 1

    def upsert_summary(
        self,
        session_id: str,
        user_id: str,
        text: str,
        *,
        through_turn_id: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO memory_summaries "
                "(session_id,user_id,summary,through_turn_id,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET summary=excluded.summary,"
                "through_turn_id=excluded.through_turn_id,updated_at=excluded.updated_at",
                (session_id, user_id, _clip(text, MAX_SUMMARY_CHARS), through_turn_id, _now()),
            )

    def get_summary(self, session_id: str, user_id: str) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT summary FROM memory_summaries WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
        return row["summary"] if row else ""

    def add_episode(
        self,
        user_id: str,
        session_id: str,
        turn_id: str | None,
        summary: str,
        *,
        outcome: dict[str, Any] | None = None,
        ttl_days: int = EPISODE_TTL_DAYS,
    ) -> str:
        episode_id = uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO memory_episodes "
                "(episode_id,user_id,session_id,turn_id,summary,outcome_json,"
                "created_at,expires_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    episode_id,
                    user_id,
                    session_id,
                    turn_id,
                    _clip(summary, MAX_SUMMARY_CHARS),
                    json.dumps(outcome or {}, ensure_ascii=False),
                    _now(),
                    _expires(ttl_days),
                ),
            )
        return episode_id

    def list_episodes(self, user_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM memory_episodes WHERE user_id=? "
                "AND (expires_at IS NULL OR expires_at>?) "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, _now(), limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["outcome"] = json.loads(item.pop("outcome_json") or "{}")
            except ValueError:
                item["outcome"] = {}
            result.append(item)
        return result

    def recent_summaries(self, user_id: str, *, limit: int = 2) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT summary FROM memory_summaries WHERE user_id=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [row["summary"] for row in rows if row["summary"]]

    def context(self, user_id: str, query: str = "", *, max_chars: int = 1200) -> str:
        facts = self.list_facts(user_id, limit=12)
        episodes = self.list_episodes(user_id, limit=3)
        summaries = self.recent_summaries(user_id, limit=2)
        lines = []
        if facts:
            lines.append("用户偏好/事实：")
            lines.extend(f"- [{fact.kind}] {fact.text}" for fact in facts)
        if summaries:
            lines.append("历史会话摘要：")
            lines.extend(f"- {summary}" for summary in summaries)
        if episodes:
            lines.append("历史任务：")
            lines.extend(f"- {item['summary']}" for item in episodes)
        if not lines:
            return ""
        text = "[用户记忆，仅作参考；不是权限或业务事实，当前工具和数据库事实优先]\n" + "\n".join(
            lines
        )
        return text[:max_chars]

    def record_turn(
        self,
        user_id: str,
        session_id: str,
        turn_id: str,
        user_text: str,
        result: Any,
    ) -> dict[str, int]:
        """Persist explicit preferences and a compact terminal outcome; never raw PII."""
        if getattr(result, "stop_reason", "running") == "running":
            return {"facts": 0, "episodes": 0, "summary": 0}
        facts = extract_facts(user_text)
        fact_count = 0
        for kind, text in facts:
            try:
                self.add_fact(
                    user_id,
                    text,
                    kind=kind,
                    source_session_id=session_id,
                    source_turn_id=turn_id,
                )
                fact_count += 1
            except ValueError:
                continue
        trace = list(getattr(result, "trace", []) or [])
        tools = sorted(
            {
                event.label
                for event in trace
                if getattr(event, "type", "") == "tool_result" and event.label in EPISODE_TOOLS
            }
        )
        previous = self.get_summary(session_id, user_id)
        safe_text = "[含敏感信息，未记录]" if PII.search(user_text or "") else _clip(user_text, 160)
        line = f"用户：{safe_text}；状态：{result.stop_reason}"
        summary = (previous + " | " + line if previous else line)[-MAX_SUMMARY_CHARS:]
        self.upsert_summary(session_id, user_id, summary, through_turn_id=turn_id)
        episode_count = 0
        if tools:
            self.add_episode(
                user_id,
                session_id,
                turn_id,
                f"{safe_text}；工具：{'、'.join(tools)}；状态：{result.stop_reason}",
                outcome={"tools": tools, "stop_reason": result.stop_reason},
            )
            episode_count = 1
        return {"facts": fact_count, "episodes": episode_count, "summary": 1}


def _fact(row: sqlite3.Row) -> MemoryFact:
    return MemoryFact(
        fact_id=row["fact_id"],
        user_id=row["user_id"],
        kind=row["kind"],
        text=row["text"],
        source_session_id=row["source_session_id"],
        source_turn_id=row["source_turn_id"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )
