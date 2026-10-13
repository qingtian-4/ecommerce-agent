"""Application-controlled returns: source policy, explicit consent and atomic booking."""

from __future__ import annotations

import hashlib
import io
import json
import re
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .config import Settings
from .database import business_today
from .sessions import ConversationBusy, SessionStore, TaskValidationError, _now


def return_policy(settings: Settings) -> dict:
    path = settings.docs_dir / "return_policy.md"
    data = path.read_bytes()
    text = data.decode("utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    windows, shipping = {}, {}
    for key, heading in (("no_reason", "一、七天无理由退货"), ("quality", "二、质量问题退货")):
        section = next((s for s in sections if s.startswith(heading)), "")
        match = re.search(r"签收之日起 \*\*(\d+) 天内\*\*", section)
        table_name = "七天无理由退货" if key == "no_reason" else "质量问题退货"
        table = re.search(rf"\| {table_name} \| (\d+) 天 \| 签收日 \| ([^|]+) \|", text)
        if not match or not table or match[1] != table[1]:
            raise TaskValidationError("退货政策的期限结构不一致，需核对政策后再办理")
        windows[key] = int(match[1])
        shipping[key] = table[2].strip()
    conditions = re.search(r"不影响二次销售指：\s*(.*?)\n\*\*退货运费", text, re.DOTALL)
    quality = next(s for s in sections if s.startswith("二、质量问题退货"))
    quality_condition = re.search(r"若商品出现(.+?)，可选择退货", quality)
    if not conditions or not quality_condition or "质量问题需上传照片或视频" not in text:
        raise TaskValidationError("政策条件格式变化，请核对后台规则与政策")
    version = re.search(r"^version: (.+)$", text, re.MULTILINE)
    if version is None:
        raise TaskValidationError("退货政策缺少版本，不能生成申请预览")
    return {"hash": hashlib.sha256(data).hexdigest(), "windows": windows, "shipping": shipping,
            "secondary_sale_conditions": [s.strip("- ;；") for s in conditions[1].splitlines()
                                          if s.strip()], "quality_condition": quality_condition[1],
            "source": "return_policy.md",
            "version": version[1]}


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
            try:
                days = (today - date.fromisoformat(row["delivered_at"][:10])).days
            except ValueError:
                days = -1
            window = policy["windows"].get(task["reason"])
            if window is None or not 0 <= days <= window:
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
        with closing(self.store._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
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
                               "ORDER BY created_at DESC LIMIT 1", (sid,)).fetchone()
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
                                   "created_at")} | {"content": json.loads(row["content_json"])}

    def list_requests(self, uid: str) -> list[dict]:
        with closing(self.store._connect()) as conn:
            return [self._request(r) for r in conn.execute(
                "SELECT * FROM return_requests WHERE user_id=? ORDER BY created_at DESC", (uid,),
            )]

    def confirm_and_submit(self, sid: str, uid: str, pid: str, tid: str | None = None) -> dict:
        """Only called by the explicit UI button, never exposed as a model tool."""
        with closing(self.store._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self.store._require_owner(conn, sid, uid)
            self._check_active(conn, sid, tid)
            row = conn.execute("SELECT * FROM return_requests WHERE preview_id=? "
                               "AND session_id=? AND user_id=?", (pid, sid, uid)).fetchone()
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
            if any(content[k] != original[k] for k in (
                "order_id", "item_id", "product_id", "product_name", "delivered_at",
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
                                      "entry": "explicit_ui_button"}),
                          now))
            task.update(status="submitted", request_id=request_id)
            conn.execute("UPDATE return_tasks SET data_json=?,updated_at=? WHERE session_id=?",
                         (json.dumps(task, ensure_ascii=False), now, sid))
            return self._request(conn.execute("SELECT * FROM return_requests WHERE request_id=?",
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
