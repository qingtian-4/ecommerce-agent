"""SQLite conversation persistence with ownership and one active turn per session."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class ConversationBusy(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_messages(messages: list[dict]) -> None:
    if not messages or messages[0].get("role") != "user":
        raise ValueError("一轮对话必须从用户消息开始")
    pending = set()
    for message in messages[1:]:
        if message.get("role") == "assistant":
            if pending:
                raise ValueError("工具调用与返回未完整配对")
            calls = message.get("tool_calls") or []
            ids = [call["id"] for call in calls]
            if len(set(ids)) != len(ids):
                raise ValueError("工具调用ID重复")
            pending.update(ids)
        elif message.get("role") == "tool" and message.get("tool_call_id") in pending:
            pending.remove(message["tool_call_id"])
        else:
            raise ValueError("工具消息或对话角色不合法")
    if pending or messages[-1].get("role") != "assistant" or messages[-1].get("tool_calls"):
        raise ValueError("工具调用与返回未完整配对，不能保存为完整轮次")


class SessionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def migrate(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS acs_migrations "
                         "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            version = conn.execute("SELECT MAX(version) FROM acs_migrations").fetchone()[0] or 0
            if version > 1:
                raise RuntimeError("数据库迁移版本比当前程序新，不能自动降级")
            conn.execute("CREATE TABLE IF NOT EXISTS conversations ("
                         "session_id TEXT PRIMARY KEY,"
                         "user_id TEXT NOT NULL REFERENCES users(user_id),"
                         "title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS conversation_turns ("
                         "turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL "
                         "REFERENCES conversations(session_id), sequence INTEGER NOT NULL,"
                         "user_text TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',"
                         "status TEXT NOT NULL "
                         "CHECK(status IN ('running','completed','interrupted')),"
                         "messages_json TEXT NOT NULL DEFAULT '[]', result_json TEXT,"
                         "created_at TEXT NOT NULL, finished_at TEXT,"
                         "UNIQUE(session_id, sequence))")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS conversation_one_running "
                         "ON conversation_turns(session_id) WHERE status='running'")
            conn.execute("INSERT OR IGNORE INTO acs_migrations VALUES(1, ?)", (_now(),))

    @staticmethod
    def _require_owner(conn, sid: str, user_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM conversations WHERE session_id=? AND user_id=?",
                           (sid, user_id)).fetchone()
        if row is None:
            raise PermissionError("会话不存在或无权访问")
        return row

    def create(self, user_id: str) -> str:
        sid, now = uuid4().hex, _now()
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO conversations VALUES(?, ?, ?, ?, ?)",
                         (sid, user_id, "新会话", now, now))
        return sid

    def list(self, user_id: str) -> list[dict]:
        with closing(self._connect()) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC, session_id",
                (user_id,),
            )]

    def load(self, sid: str, user_id: str) -> dict:
        with closing(self._connect()) as conn:
            self._require_owner(conn, sid, user_id)
            turns = conn.execute("SELECT * FROM conversation_turns WHERE session_id=? "
                                 "ORDER BY sequence", (sid,)).fetchall()
        history, messages, last_result = [], [], None
        for row in turns:
            history.append({"role": "user", "content": row["user_text"]})
            if row["status"] != "running":
                history.append({"role": "assistant", "content": row["answer"]})
                messages.extend(json.loads(row["messages_json"]))
                last_result = json.loads(row["result_json"])
        return {"history": history, "messages": messages, "last_result": last_result,
                "running": bool(turns and turns[-1]["status"] == "running")}

    def begin_turn(self, sid: str, user_id: str, text: str) -> str:
        tid = uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._require_owner(conn, sid, user_id)
            if conn.execute("SELECT 1 FROM conversation_turns "
                            "WHERE session_id=? AND status='running'",
                            (sid,)).fetchone():
                raise ConversationBusy("该会话仍在处理请求，请稍后重新载入")
            sequence = conn.execute("SELECT COALESCE(MAX(sequence), 0)+1 FROM conversation_turns "
                                    "WHERE session_id=?", (sid,)).fetchone()[0]
            conn.execute("INSERT INTO conversation_turns "
                         "(turn_id,session_id,sequence,user_text,status,created_at) "
                         "VALUES(?,?,?,?,?,?)",
                         (tid, sid, sequence, text, "running", _now()))
            title = text.strip()[:30] if row["title"] == "新会话" else row["title"]
            conn.execute("UPDATE conversations SET title=?, updated_at=? WHERE session_id=?",
                         (title, _now(), sid))
        return tid

    def finish_turn(self, sid: str, user_id: str, tid: str, messages: list[dict], result: dict):
        _validate_messages(messages)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, sid, user_id)
            row = conn.execute("SELECT * FROM conversation_turns WHERE turn_id=? AND session_id=? "
                               "AND status='running'", (tid, sid)).fetchone()
            if (row is None or messages[0].get("content") != row["user_text"]
                    or messages[-1].get("content") != result["answer"]):
                raise ValueError("会话轮次不存在、已完成或消息不匹配")
            conn.execute("UPDATE conversation_turns SET status='completed',answer=?,"
                         "messages_json=?,"
                         "result_json=?,finished_at=? WHERE turn_id=?",
                         (result["answer"], json.dumps(messages, ensure_ascii=False),
                          json.dumps(result, ensure_ascii=False), _now(), tid))
            conn.execute("UPDATE conversations SET updated_at=? WHERE session_id=?", (_now(), sid))

    def interrupt_turn(self, sid: str, user_id: str, tid: str,
                       answer: str = "上次请求已中断，回答未完成，请重新提问。") -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, sid, user_id)
            row = conn.execute("SELECT user_text FROM conversation_turns WHERE turn_id=? "
                               "AND session_id=? AND status='running'", (tid, sid)).fetchone()
            if row:
                messages = [{"role": "user", "content": row["user_text"]},
                            {"role": "assistant", "content": answer}]
                conn.execute("UPDATE conversation_turns SET status='interrupted', answer=?,"
                             "messages_json=?,result_json=?,finished_at=? WHERE turn_id=?",
                             (answer, json.dumps(messages, ensure_ascii=False),
                              json.dumps({"answer": answer, "stop_reason": "interrupted"},
                                         ensure_ascii=False), _now(), tid))

    def recover_interrupted(self) -> int:
        """Call once at single-process server startup, never on a per-request load."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT t.turn_id,t.session_id,c.user_id FROM conversation_turns t "
                                "JOIN conversations c USING(session_id) "
                                "WHERE status='running'").fetchall()
        for row in rows:
            self.interrupt_turn(row["session_id"], row["user_id"], row["turn_id"])
        return len(rows)
