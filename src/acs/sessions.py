"""SQLite conversation persistence with ownership and one active turn per session."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .database import item_sku
from .runtime import RunBudget


class ConversationBusy(RuntimeError):
    pass


class TaskValidationError(ValueError):
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
            if version > 5:
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
            conn.execute("CREATE TABLE IF NOT EXISTS return_tasks ("
                         "session_id TEXT PRIMARY KEY REFERENCES conversations(session_id),"
                         "data_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("INSERT OR IGNORE INTO acs_migrations VALUES(2, ?)", (_now(),))
            conn.execute("CREATE TABLE IF NOT EXISTS return_evidence ("
                         "evidence_id TEXT PRIMARY KEY, session_id TEXT NOT NULL "
                         "REFERENCES conversations(session_id), media_type TEXT NOT NULL,"
                         "data BLOB NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS return_previews ("
                         "preview_id TEXT PRIMARY KEY, session_id TEXT NOT NULL "
                         "REFERENCES conversations(session_id), revision INTEGER NOT NULL,"
                         "content_json TEXT NOT NULL, policy_hash TEXT NOT NULL,"
                         "expires_at TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS return_confirmations ("
                         "preview_id TEXT PRIMARY KEY REFERENCES return_previews(preview_id),"
                         "user_id TEXT NOT NULL REFERENCES users(user_id),"
                         "confirmed_at TEXT NOT NULL, consumed_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS return_requests ("
                         "request_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE "
                         "REFERENCES return_previews(preview_id), session_id TEXT NOT NULL "
                         "REFERENCES conversations(session_id), user_id TEXT NOT NULL "
                         "REFERENCES users(user_id), order_id TEXT NOT NULL "
                         "REFERENCES orders(order_id),"
                         "item_id INTEGER NOT NULL REFERENCES order_items(id),"
                         "quantity INTEGER NOT NULL "
                         "CHECK(typeof(quantity)='integer' AND quantity>0),"
                         "status TEXT NOT NULL "
                         "CHECK(status IN ('pending_review','approved','rejected')),"
                         "content_json TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS return_audit ("
                         "audit_id TEXT PRIMARY KEY, request_id TEXT NOT NULL "
                         "REFERENCES return_requests(request_id), action TEXT NOT NULL,"
                         "actor TEXT NOT NULL, detail_json TEXT NOT NULL,"
                         "created_at TEXT NOT NULL)")
            for operation in ("INSERT", "UPDATE"):
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS return_quantity_{operation.lower()} "
                             f"BEFORE {operation} ON return_requests "
                             "WHEN NEW.status IN ('pending_review','approved') BEGIN "
                             "SELECT CASE WHEN NEW.quantity + COALESCE((SELECT SUM(quantity) "
                             "FROM return_requests WHERE item_id=NEW.item_id "
                             "AND status IN ('pending_review','approved') "
                             "AND request_id<>NEW.request_id),0) > "
                             "(SELECT quantity FROM order_items WHERE id=NEW.item_id) "
                             "THEN RAISE(ABORT,'return quantity exceeded') END; END")
            conn.execute("INSERT OR IGNORE INTO acs_migrations VALUES(3, ?)", (_now(),))
            conn.execute("CREATE TABLE IF NOT EXISTS return_reviews ("
                         "request_id TEXT PRIMARY KEY REFERENCES return_requests(request_id),"
                         "decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),"
                         "reason TEXT NOT NULL CHECK(length(trim(reason))>0),"
                         "reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL)")
            conn.execute("CREATE TRIGGER IF NOT EXISTS return_status_transition "
                         "BEFORE UPDATE OF status ON return_requests "
                         "WHEN NEW.status<>OLD.status AND (OLD.status<>'pending_review' "
                         "OR NEW.status NOT IN ('approved','rejected')) "
                         "BEGIN SELECT RAISE(ABORT,'illegal return status transition'); END")
            conn.execute("INSERT OR IGNORE INTO acs_migrations VALUES(4, ?)", (_now(),))
            conn.execute("CREATE TABLE IF NOT EXISTS product_skus ("
                         "sku_id TEXT PRIMARY KEY, product_id TEXT NOT NULL "
                         "REFERENCES products(product_id), wearing_type TEXT, anc INTEGER "
                         "CHECK(anc IN (0,1)), color TEXT, availability TEXT NOT NULL "
                         "CHECK(availability IN ('available','unavailable','unknown')))")
            conn.execute("CREATE TABLE IF NOT EXISTS order_item_skus ("
                         "item_id INTEGER PRIMARY KEY REFERENCES order_items(id),"
                         "sku_id TEXT NOT NULL REFERENCES product_skus(sku_id))")
            conn.execute("INSERT OR IGNORE INTO acs_migrations VALUES(5, ?)", (_now(),))

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

    @staticmethod
    def _task(conn, sid: str) -> dict | None:
        row = conn.execute("SELECT data_json FROM return_tasks WHERE session_id=?",
                           (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _task_view(conn, task: dict | None, user_id: str) -> dict:
        order, missing = None, []
        if task and task["status"] != "cancelled":
            missing = [f for f in ("order_id", "item_id", "quantity", "reason") if task[f] is None]
            if task["reason"] == "no_reason" and task.get("secondary_sale_ok") is not True:
                missing.append("secondary_sale_ok")
            if task["reason"] == "quality":
                if not task["problem_description"]:
                    missing.append("problem_description")
                if task.get("quality_non_user_damage") is not True:
                    missing.append("quality_non_user_damage")
                if not task.get("evidence_id"):
                    missing.append("quality_evidence")
            if task["order_id"]:
                row = conn.execute("SELECT order_id,status,delivered_at FROM orders "
                                   "WHERE order_id=? AND user_id=?",
                                   (task["order_id"], user_id)).fetchone()
                if row:
                    order = dict(row)
                    order["items"] = [dict(r) for r in conn.execute(
                        "SELECT i.id,i.product_id,i.quantity,p.name AS product_name "
                        "FROM order_items i JOIN products p USING(product_id) WHERE order_id=?",
                        (task["order_id"],),
                    )]
                    for item in order["items"]:
                        item["sku"] = item_sku(conn, item["id"])
        submitted = bool(task and task["status"] == "submitted")
        return {"task": task, "order": order, "missing_fields": [] if submitted else missing,
                "can_submit": False,
                "note": "已创建申请，后续状态以申请记录为准，尚未退款。" if submitted else
                        "草稿未经审核；生成预览后须通过界面确认提交，尚未创建售后单或退款。",
                "queried_at": _now()}

    def get_return_task(self, sid: str, user_id: str) -> dict:
        with closing(self._connect()) as conn:
            self._require_owner(conn, sid, user_id)
            return self._task_view(conn, self._task(conn, sid), user_id)

    def update_return_task(self, sid: str, user_id: str, tid: str, patch: dict,
                           *, cancel: bool = False, only_if_absent: bool = False,
                           budget: RunBudget | None = None) -> dict:
        """Save model-extracted claims with their original user turn, not verified eligibility."""
        fields = ("order_id", "item_id", "quantity", "reason", "condition", "problem_description",
                  "secondary_sale_ok", "quality_non_user_damage")
        if set(patch) - set(fields) or (not patch and not cancel):
            raise TaskValidationError("草稿字段无效或为空")
        if only_if_absent and (cancel or set(patch) != {"order_id"}
                               or not isinstance(patch["order_id"], str)):
            raise TaskValidationError("受限补存只允许订单字段，不能取消或补充其他字段")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if budget is not None:
                budget.remaining()
            self._require_owner(conn, sid, user_id)
            turn = conn.execute("SELECT user_text FROM conversation_turns WHERE session_id=? "
                                "AND turn_id=? AND status='running'", (sid, tid)).fetchone()
            if turn is None:
                raise TaskValidationError("草稿只能由当前进行中的会话轮次更新")
            if cancel:
                # Quoted, hypothetical or qualified actions are not whole-utterance consent.
                text = turn["user_text"].strip().rstrip("。.!！")
                explicit = re.fullmatch(r"(?:请)?(?:取消|中止|清空)(?:当前|这个|这份|我的)?"
                                        r"(?:退货草稿|退货|草稿)", text)
                if not explicit and text != "中止这个退货草稿，不要取消订单":
                    raise TaskValidationError(
                        "暂停话题不清空草稿。确需中止请明确说“中止退货草稿”。"
                    )
            previous = self._task(conn, sid)
            if only_if_absent and previous is not None:
                satisfied = (previous["status"] == "draft"
                             and previous["order_id"] == patch["order_id"])
                return self._task_view(conn, previous, user_id) | {
                    "state_write": "satisfied" if satisfied else "conflict",
                }
            if previous and previous["status"] == "submitted":
                raise TaskValidationError("申请已经提交，不能修改或用中止草稿撤销；"
                                          "请查询结果，新申请请新建会话。")
            if cancel and (previous is None or previous["status"] == "cancelled"):
                return self._task_view(conn, previous, user_id)
            task = previous if previous and previous["status"] == "draft" else {
                **dict.fromkeys(fields), "sources": {},
                "revision": previous["revision"] if previous else 0,
            }
            task["status"] = "cancelled" if cancel else "draft"
            clear = set(fields) if cancel else set()
            if "order_id" in patch and patch["order_id"] != task["order_id"]:
                clear.update(set(fields) - {"order_id"})
            elif "item_id" in patch and patch["item_id"] != task["item_id"]:
                clear.update({"quantity", "condition", "problem_description",
                              "secondary_sale_ok", "quality_non_user_damage"})
            if "reason" in patch and patch["reason"] != task["reason"]:
                clear.update({"condition", "problem_description",
                              "secondary_sale_ok", "quality_non_user_damage"})
            if clear:
                task.pop("evidence_id", None)
                task["sources"].pop("evidence_id", None)
            for field in clear:
                task[field] = None
                task["sources"].pop(field, None)
            for field, value in patch.items():
                task[field] = value
                if value is None:
                    task["sources"].pop(field, None)
                else:
                    task["sources"][field] = {"turn_id": tid, "user_text": turn["user_text"],
                                              "type": "user_claim", "updated_at": _now()}
            if task["order_id"] is not None and (not isinstance(task["order_id"], str)
                    or not conn.execute("SELECT 1 FROM orders WHERE order_id=? AND user_id=?",
                                        (task["order_id"], user_id)).fetchone()):
                raise TaskValidationError("订单不存在或不属于当前用户")
            item = None
            if task["item_id"] is not None:
                if type(task["item_id"]) is not int:
                    raise TaskValidationError("商品明细编号必须为整数")
                item = conn.execute("SELECT quantity FROM order_items WHERE id=? AND order_id=?",
                                    (task["item_id"], task["order_id"])).fetchone()
                if item is None:
                    raise TaskValidationError("商品明细不属于已选择订单，请先确认订单和商品")
            if task["quantity"] is not None and (
                type(task["quantity"]) is not int or task["quantity"] <= 0
                or item is None or task["quantity"] > item["quantity"]
            ):
                raise TaskValidationError("退货数量必须为正整数，先选择商品且不能超过购买数量")
            if task["reason"] not in (None, "no_reason", "quality"):
                raise TaskValidationError("退货原因须为no_reason或quality")
            if task["condition"] not in (None, "sealed", "opened"):
                raise TaskValidationError("商品状况须为sealed或opened；不清楚时留空")
            for field in ("secondary_sale_ok", "quality_non_user_damage"):
                if task.get(field) is not None and type(task[field]) is not bool:
                    raise TaskValidationError("商品条件声明必须为布尔值或空")
            description = task["problem_description"]
            if description is not None and (not isinstance(description, str)
                                            or not description.strip() or len(description) > 1000):
                raise TaskValidationError("问题描述须为1到1000字的文本")
            if budget is not None:
                budget.remaining()
            task["revision"] += 1
            conn.execute("INSERT INTO return_tasks VALUES(?,?,?) ON CONFLICT(session_id) "
                         "DO UPDATE SET data_json=excluded.data_json,"
                         "updated_at=excluded.updated_at",
                         (sid, json.dumps(task, ensure_ascii=False), _now()))
            view = self._task_view(conn, task, user_id)
            return view | {"state_write": "created"} if only_if_absent else view

    def cancel_return_task(self, sid: str, user_id: str, tid: str) -> dict:
        return self.update_return_task(sid, user_id, tid, {}, cancel=True)
