"""Application-controlled returns: source policy, explicit consent and atomic booking."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .config import Settings
from .database import business_today, item_sku
from .return_rules import delivery_days, read_return_policy, within_window
from .sessions import ConversationBusy, SessionStore, TaskValidationError, _now

REQUEST_QUERY = ("SELECT r.*,v.decision AS review_decision,v.reason AS review_reason,"
                 "v.reviewer,v.reviewed_at FROM return_requests r "
                 "LEFT JOIN return_reviews v USING(request_id)")


def return_policy(settings: Settings) -> dict:
    try:
        return read_return_policy(settings)
    except ValueError as exc:
        raise TaskValidationError(str(exc)) from exc


def policy_for_reason(policy: dict, reason: str | None) -> dict:
    """Expose the relevant section; no-reason conditions must not leak into quality evidence."""
    conditions = policy["secondary_sale_conditions"] if reason == "no_reason" else (
        [policy["quality_condition"]] if reason == "quality" else []
    )
    return {"source": policy["source"], "version": policy["version"], "hash": policy["hash"],
            "reason": reason, "window_days": policy["windows"].get(reason),
            "shipping_payer": policy["shipping"].get(reason),
            "starting_point": "签收日", "required_conditions": conditions,
            "requires_uploaded_media": reason == "quality"}


class ReturnService:
    def __init__(self, settings: Settings, store: SessionStore | None = None):
        self.settings = settings
        self.store = store or SessionStore(settings.db_path)

    @staticmethod
    def _check_active(conn, sid: str, tid: str | None) -> None:
        active = conn.execute("SELECT turn_id FROM conversation_turns WHERE session_id=? "
                              "AND status='running'", (sid,)).fetchone()
        if active and active[0] != tid:
            raise ConversationBusy("会话正在处理请求，请稍后操作")

    def _content(self, conn, sid: str, uid: str, policy: dict) -> tuple[dict, list[str]]:
        task = self.store._task(conn, sid)
        if not task or task["status"] != "draft":
            return {}, ["请先在对话中准备一份未提交的退货草稿"]
        missing = [f for f in ("order_id", "item_id", "quantity", "reason") if not task.get(f)]
        if missing:
            return {}, ["草稿还缺订单、商品、数量或原因，请先补充"]
        row = conn.execute(
            "SELECT o.status,o.delivered_at,i.quantity AS purchased,i.product_id,"
            "i.purchase_price,p.name AS product_name FROM orders o "
            "JOIN order_items i ON i.order_id=o.order_id JOIN products p USING(product_id) "
            "WHERE o.order_id=? AND o.user_id=? AND i.id=?",
            (task["order_id"], uid, task["item_id"]),
        ).fetchone()
        if row is None:
            return {}, ["订单或商品不属于当前用户"]
        reasons, today = [], business_today(self.settings)
        if row["status"] != "delivered" or not row["delivered_at"]:
            reasons.append("订单尚未签收或已取消，不能创建退货申请")
        else:
            days = delivery_days(row["delivered_at"], today)
            window = policy["windows"].get(task["reason"])
            if not within_window(days, window):
                reasons.append("签收日期无效或已超出对应退货期限")
        reserved = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM return_requests "
                                "WHERE item_id=? AND status IN ('pending_review','approved')",
                                (task["item_id"],)).fetchone()[0]
        if (type(task["quantity"]) is not int or task["quantity"] <= 0
                or task["quantity"] > row["purchased"] - reserved):
            reasons.append("申请数量超过购买量或剩余可申请数量")
        evidence = None
        if task["reason"] == "no_reason" and task.get("secondary_sale_ok") is not True:
            reasons.append("请确认政策中的二次销售条件；商品状况仍待人工审核")
        if task["reason"] == "quality":
            if task.get("quality_non_user_damage") is False:
                reasons.append("用户自述人为损坏，不符合质量问题退货条件")
            elif (not task.get("problem_description")
                  or task.get("quality_non_user_damage") is not True):
                reasons.append("请补充故障说明及非人为损坏声明")
            media = conn.execute("SELECT evidence_id,media_type,sha256,length(data) AS size "
                                 "FROM return_evidence WHERE evidence_id=? AND session_id=?",
                                 (task.get("evidence_id"), sid)).fetchone()
            if media is None:
                reasons.append("质量问题需用户上传照片或视频，当前凭证缺失")
            else:
                evidence = dict(media) | {"source": "user_upload", "verified": False}
        content = {"order_id": task["order_id"], "item_id": task["item_id"],
                   "product_id": row["product_id"], "product_name": row["product_name"],
                   "sku": item_sku(conn, task["item_id"]),
                   "quantity": task["quantity"], "reason": task["reason"],
                   "condition": task.get("condition"),
                   "problem_description": task.get("problem_description"),
                   "secondary_sale_ok": task.get("secondary_sale_ok"),
                   "quality_non_user_damage": task.get("quality_non_user_damage"),
                   "evidence": evidence, "claims_verified": False,
                   "delivered_at": row["delivered_at"], "checked_on": today.isoformat(),
                   "policy": policy_for_reason(policy, task["reason"])}
        return content, reasons

    def preview(self, sid: str, uid: str, tid: str | None = None) -> dict:
        with self.store.transaction() as conn:
            self.store._require_owner(conn, sid, uid)
            self._check_active(conn, sid, tid)
            policy = return_policy(self.settings)
            content, reasons = self._content(conn, sid, uid, policy)
            if reasons:
                return {"ready": False, "reasons": reasons, "content": content}
            task = self.store._task(conn, sid)
            pid = uuid4().hex
            expires = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
            conn.execute("INSERT INTO return_previews VALUES(?,?,?,?,?,?,?)",
                         (pid, sid, task["revision"], json.dumps(content, ensure_ascii=False),
                          policy["hash"], expires, _now()))
            return {"ready": True, "preview_id": pid, "content": content,
                    "expires_at": expires,
                    "notice": "尚未创建申请。请在页面检查并确认；之后仍需审核，不是退款。"}

    def latest_preview(self, sid: str, uid: str) -> dict | None:
        with closing(self.store._connect()) as conn:
            self.store._require_owner(conn, sid, uid)
            row = conn.execute("SELECT * FROM return_previews WHERE session_id=? "
                               "ORDER BY created_at DESC,rowid DESC LIMIT 1", (sid,)).fetchone()
            task = self.store._task(conn, sid)
            if (row is None or not task or task["status"] != "draft"
                    or task["revision"] != row["revision"]):
                return None
            policy = return_policy(self.settings)
            _, reasons = self._content(conn, sid, uid, policy)
            if (reasons or row["policy_hash"] != policy["hash"]
                    or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)):
                return None
            return {"ready": True, "preview_id": row["preview_id"],
                    "content": json.loads(row["content_json"]), "expires_at": row["expires_at"]}

    @staticmethod
    def _request(row) -> dict:
        return {k: row[k] for k in ("request_id", "order_id", "item_id", "quantity", "status",
                                   "created_at")} | {"content": json.loads(row["content_json"]),
            "review": {"decision": row["review_decision"], "reason": row["review_reason"],
                       "reviewer": row["reviewer"], "reviewed_at": row["reviewed_at"]}
            if row["reviewed_at"] else None}

    def list_requests(self, uid: str) -> list[dict]:
        with closing(self.store._connect()) as conn:
            return [self._request(r) for r in conn.execute(
                REQUEST_QUERY + " WHERE r.user_id=? ORDER BY r.created_at DESC", (uid,),
            )]

    def authenticate_reviewer(self, username: str, password: str) -> bool:
        """Used only by Gradio's server-side login, not a customer/model input."""
        configured = self.settings.admin_password.get_secret_value()
        return bool(self.settings.admin_username and configured) and (
            hmac.compare_digest(username.encode(), self.settings.admin_username.encode())
            and hmac.compare_digest(password.encode(), configured.encode())
        )

    def _require_reviewer(self, reviewer: str | None) -> None:
        # reviewer comes from authenticated gr.Request.username, never a form field.
        if (not self.settings.admin_username or not self.settings.admin_password.get_secret_value()
                or reviewer != self.settings.admin_username):
            raise PermissionError("需要已认证的管理员身份")

    def list_for_review(self, *, reviewer: str | None,
                        status: str = "pending_review") -> list[dict]:
        self._require_reviewer(reviewer)
        if status not in {"pending_review", "approved", "rejected", "all"}:
            raise TaskValidationError("审核列表状态不合法")
        with closing(self.store._connect()) as conn:
            rows = conn.execute(REQUEST_QUERY + " WHERE (?='all' OR r.status=?) "
                                "ORDER BY r.created_at,r.request_id", (status, status)).fetchall()
            return [self._request(r) | {"user_id": r["user_id"]} for r in rows]

    def get_for_review(self, rid: str, *, reviewer: str | None) -> dict:
        self._require_reviewer(reviewer)
        with closing(self.store._connect()) as conn:
            row = conn.execute(REQUEST_QUERY + " WHERE r.request_id=?", (rid,)).fetchone()
            if row is None:
                raise TaskValidationError("申请不存在，请刷新列表")
            order = conn.execute(
                "SELECT o.order_id,o.user_id,o.status,o.created_at,o.delivered_at,"
                "i.product_id,i.quantity,i.purchase_price FROM orders o "
                "JOIN order_items i ON i.order_id=o.order_id WHERE o.order_id=? AND i.id=?",
                (row["order_id"], row["item_id"]),
            ).fetchone()
            audits = [{"action": a["action"], "actor": a["actor"],
                       "detail": json.loads(a["detail_json"]), "created_at": a["created_at"]}
                      for a in conn.execute("SELECT * FROM return_audit WHERE request_id=? "
                                            "ORDER BY created_at,audit_id", (rid,))]
            return self._request(row) | {"user_id": row["user_id"],
                                         "current_order": dict(order), "audit": audits}

    def review_evidence(self, rid: str, *, reviewer: str | None) -> dict | None:
        detail = self.get_for_review(rid, reviewer=reviewer)
        evidence = detail["content"].get("evidence")
        if not evidence:
            return None
        with closing(self.store._connect()) as conn:
            row = conn.execute("SELECT e.* FROM return_evidence e JOIN return_requests r "
                               "ON r.session_id=e.session_id WHERE r.request_id=? "
                               "AND e.evidence_id=?", (rid, evidence["evidence_id"])).fetchone()
            if (row is None or row["sha256"] != evidence["sha256"]
                    or hashlib.sha256(row["data"]).hexdigest() != evidence["sha256"]):
                raise TaskValidationError("原始凭证缺失或校验失败，请核实后再审核")
            return {k: row[k] for k in ("media_type", "data", "sha256")}

    def review(self, rid: str, decision: str, reason: str, *, reviewer: str | None) -> dict:
        self._require_reviewer(reviewer)
        if decision not in {"approved", "rejected"}:
            raise TaskValidationError("只能审核通过或拒绝申请")
        if not isinstance(reason, str) or not 0 < len(reason.strip()) <= 1000:
            raise TaskValidationError("请填写1到1000字的审核原因")
        reason = reason.strip()
        with closing(self.store._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM return_requests WHERE request_id=?",
                               (rid,)).fetchone()
            if row is None:
                raise TaskValidationError("申请不存在，请刷新列表")
            if row["status"] != "pending_review":
                raise TaskValidationError("申请已经审核，不能重复审核或修改结论，请刷新查询")
            now = _now()
            conn.execute("UPDATE return_requests SET status=? WHERE request_id=?", (decision, rid))
            conn.execute("INSERT INTO return_reviews VALUES(?,?,?,?,?)",
                         (rid, decision, reason, reviewer, now))
            conn.execute("INSERT INTO return_audit VALUES(?,?,?,?,?,?)", (
                uuid4().hex, rid, "reviewed", reviewer,
                json.dumps({"from_status": "pending_review", "to_status": decision,
                            "reason": reason, "entry": "authenticated_review_ui"},
                           ensure_ascii=False), now,
            ))
            return self._request(conn.execute(REQUEST_QUERY + " WHERE r.request_id=?",
                                              (rid,)).fetchone())

    def confirm_and_submit(self, sid: str, uid: str, pid: str, tid: str | None = None) -> dict:
        """Only called by the explicit UI button, never exposed as a model tool."""
        with self.store.transaction() as conn:
            return self._confirm_and_submit(conn, sid, uid, pid, tid)

    def _confirm_and_submit(self, conn, sid, uid, pid, tid=None, *, entry="explicit_ui_button"):
        """Borrow an application-owned transaction; never commit or open a connection."""
        if not conn.in_transaction:
            raise RuntimeError("Submission requires an active business transaction")
        self.store._require_owner(conn, sid, uid)
        self._check_active(conn, sid, tid)
        row = conn.execute(REQUEST_QUERY + " WHERE r.preview_id=? "
                           "AND r.session_id=? AND r.user_id=?", (pid, sid, uid)).fetchone()
        if row:
            return self._request(row)
        preview = conn.execute("SELECT * FROM return_previews WHERE preview_id=? "
                               "AND session_id=?", (pid, sid)).fetchone()
        if preview is None:
            raise TaskValidationError("申请预览不存在，请重新生成预览")
        task = self.store._task(conn, sid)
        if not task or task["status"] != "draft" or task["revision"] != preview["revision"]:
            raise TaskValidationError("草稿已经更改或中止，必须重新生成预览并确认")
        if datetime.fromisoformat(preview["expires_at"]) <= datetime.now(UTC):
            raise TaskValidationError("预览已过期，请重新核查并生成预览")
        policy = return_policy(self.settings)
        if policy["hash"] != preview["policy_hash"]:
            raise TaskValidationError("政策已变化，请重新生成预览并确认")
        content, reasons = self._content(conn, sid, uid, policy)
        if reasons:
            raise TaskValidationError("；".join(reasons))
        original = json.loads(preview["content_json"])
        if any(content.get(k) != original.get(k) for k in (
            "order_id", "item_id", "product_id", "product_name", "delivered_at",
            "sku",
            "quantity", "reason", "condition", "problem_description",
            "secondary_sale_ok", "quality_non_user_damage", "evidence",
        )):
            raise TaskValidationError("申请内容已经更改，请重新预览")
        request_id, now = "R" + uuid4().hex, _now()
        conn.execute("INSERT INTO return_confirmations VALUES(?,?,?,?)", (pid, uid, now, now))
        conn.execute("INSERT INTO return_requests VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (request_id, pid, sid, uid, task["order_id"], task["item_id"],
                      task["quantity"], "pending_review",
                      json.dumps(content, ensure_ascii=False), now))
        conn.execute("INSERT INTO return_audit VALUES(?,?,?,?,?,?)",
                     (uuid4().hex, request_id, "created", uid,
                      json.dumps({"preview_id": pid, "revision": task["revision"],
                                  "policy_hash": policy["hash"],
                                  "entry": entry}),
                      now))
        task.update(status="submitted", request_id=request_id)
        conn.execute("UPDATE return_tasks SET data_json=?,updated_at=? WHERE session_id=?",
                     (json.dumps(task, ensure_ascii=False), now, sid))
        return self._request(conn.execute(REQUEST_QUERY + " WHERE r.request_id=?",
                                          (request_id,)).fetchone())

    def attach_evidence(self, sid: str, uid: str, tid: str, data: bytes) -> dict:
        if not isinstance(data, bytes) or not 0 < len(data) <= 10 * 1024 * 1024:
            raise TaskValidationError("照片或视频须为不超过10MB的上传文件")
        if len(data) >= 16 and data[4:8] == b"ftyp":
            media_type = "video/mp4"
        else:
            try:
                with Image.open(io.BytesIO(data)) as picture:
                    media_type = {"PNG": "image/png", "JPEG": "image/jpeg",
                                  "WEBP": "image/webp"}.get(picture.format)
                    picture.verify()
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise TaskValidationError("只接收PNG/JPEG/WebP照片或MP4视频") from exc
            if not media_type:
                raise TaskValidationError("不支持此照片格式")
        with closing(self.store._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self.store._require_owner(conn, sid, uid)
            self._check_active(conn, sid, tid)
            turn = conn.execute("SELECT user_text FROM conversation_turns WHERE session_id=? "
                                "AND turn_id=? AND status='running'", (sid, tid)).fetchone()
            task = self.store._task(conn, sid)
            if not turn or not task or task["status"] != "draft" or task["reason"] != "quality":
                raise TaskValidationError("请先选择质量问题退货草稿，再上传凭证")
            eid, digest, now = uuid4().hex, hashlib.sha256(data).hexdigest(), _now()
            conn.execute("INSERT INTO return_evidence VALUES(?,?,?,?,?,?)",
                         (eid, sid, media_type, data, digest, now))
            task["evidence_id"] = eid
            task["revision"] += 1
            task["sources"]["evidence_id"] = {"turn_id": tid, "user_text": turn["user_text"],
                                               "type": "user_upload", "sha256": digest,
                                               "updated_at": now}
            conn.execute("UPDATE return_tasks SET data_json=?,updated_at=? WHERE session_id=?",
                         (json.dumps(task, ensure_ascii=False), now, sid))
            return {"evidence_id": eid, "media_type": media_type, "size": len(data),
                    "sha256": digest, "verified": False}

    def clear_evidence(self, sid: str, uid: str, tid: str) -> dict:
        with closing(self.store._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self.store._require_owner(conn, sid, uid)
            turn = conn.execute("SELECT 1 FROM conversation_turns WHERE session_id=? "
                                "AND turn_id=? AND status='running'", (sid, tid)).fetchone()
            task = self.store._task(conn, sid)
            if not turn or not task or task["status"] != "draft":
                raise TaskValidationError("只能撤下当前未提交草稿的凭证，不能改变已提交的申请")
            task.pop("evidence_id", None)
            task["sources"].pop("evidence_id", None)
            task["revision"] += 1
            conn.execute("UPDATE return_tasks SET data_json=?,updated_at=? WHERE session_id=?",
                         (json.dumps(task, ensure_ascii=False), _now(), sid))
            return {"removed_from_draft": True, "note": "原上传记录保留，当前草稿需要重新上传凭证"}
