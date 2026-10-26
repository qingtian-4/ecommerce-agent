"""Additive product UI projections and feedback; original submission APIs stay authoritative."""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .web_service import COOKIE, UID, ActionBody, create_app, pick

FRONTEND = Path(__file__).resolve().parents[2] / "frontend-v3"


class FeedbackBody(ActionBody):
    turn_id: str
    vote: Literal["helpful", "unhelpful"]
    reason: Literal["incorrect", "incomplete", "unclear", "other"] | None = None


def install_shop(app, *, origin="http://127.0.0.1:8767"):
    """Extend the SAME app/engine/identity and SQLite, never another business runtime."""
    engine = app.state.engine
    old_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with old_lifespan(app):
            with engine.tx() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS ui_feedback("
                    "id INTEGER PRIMARY KEY,session_id TEXT,turn_id TEXT,vote TEXT,"
                    "reason TEXT,request_key TEXT,created_at TEXT,"
                    "UNIQUE(session_id,request_key))"
                )
            yield

    app.router.lifespan_context = lifespan

    # Share the existing engine's credential lookup and authoritative ownership check.
    # The original closure isn't exported; these same origin/CSRF checks apply ONLY
    # to the new feedback route. Original confirmation routes are not reimplemented.
    def access(request, sid, write=False):
        browser = engine.browser(request.cookies.get(COOKIE))
        if browser is None:
            raise HTTPException(401, "请先打开客服首页")
        if write and (
            request.headers.get("origin") != origin
            or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), browser["csrf"])
        ):
            raise HTTPException(403, "会话凭证或CSRF校验失败")
        engine.own(browser, sid)

    def order(order_id):
        result = engine.gateway.registry.db.get_order(order_id, UID)
        if not result.get("found"):
            raise HTTPException(404, "订单不存在或无权访问")
        return engine.clean(
            pick(
                result,
                ("order_id", "status", "created_at", "delivered_at", "shipped_at", "total_amount"),
            )
            | {
                "items": [
                    pick(
                        item,
                        (
                            "id",
                            "product_id",
                            "product_name",
                            "quantity",
                            "purchase_price",
                            "product_category",
                        ),
                    )
                    for item in result["items"]
                ]
            }
        )

    original_page = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/")

    @app.get("/shop")
    def page(request: Request):
        original = original_page(request)
        response = FileResponse(FRONTEND / "index.html")
        for name, value in original.raw_headers:
            if name.lower() == b"set-cookie":
                response.raw_headers.append((name, value))
        return response

    app.mount("/shop/assets", StaticFiles(directory=FRONTEND), name="shop-assets")

    @app.get("/api/sessions/{sid}/orders")
    def orders(
        request: Request,
        sid: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(3, ge=1, le=10),
    ):
        access(request, sid)
        rows = engine.gateway.registry.db.list_orders(UID)
        start = (page - 1) * page_size
        return {
            "orders": [order(r["order_id"]) for r in rows[start : start + page_size]],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
        }

    @app.get("/api/sessions/{sid}/orders/{oid}")
    def detail(request: Request, sid: str, oid: str):
        access(request, sid)
        return order(oid)

    @app.get("/api/sessions/{sid}/orders/{oid}/logistics")
    def logistics(request: Request, sid: str, oid: str):
        access(request, sid)
        order(oid)  # Data-layer ownership check before any logistics lookup.
        result = engine.gateway.registry.db.get_logistics(oid, UID)
        return engine.clean(
            pick(
                result,
                ("found", "order_id", "carrier", "status", "trace", "latest", "reason", "today"),
            )
        )

    def turns(sid):
        with closing(engine.store._connect()) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT turn_id,user_text,status FROM conversation_turns "
                    "WHERE session_id=? ORDER BY sequence",
                    (sid,),
                )
            ]

    @app.get("/api/sessions/{sid}/messages")
    def messages(request: Request, sid: str):
        access(request, sid)
        rows = turns(sid)
        history = engine.display_history(sid)  # Existing grounding/hash gate, never raw SDK.
        if len(history) != sum(1 if r["status"] == "running" else 2 for r in rows):
            raise HTTPException(409, "对话状态正在更新，请只读刷新")
        with engine.tx() as conn:
            feedback = {
                r["turn_id"]: pick(dict(r), ("vote", "reason"))
                for r in conn.execute(
                    "SELECT * FROM ui_feedback WHERE session_id=? ORDER BY id", (sid,)
                )
            }
        result, index = [], 0
        with closing(engine.store._connect()) as conn:
            for row in rows:
                user = history[index]
                if user["role"] != "user" or user["content"] != row["user_text"]:
                    raise HTTPException(409, "消息关联正在更新，请只读刷新")
                result.append(
                    user | {"turn_id": row["turn_id"], "message_id": row["turn_id"] + ":user"}
                )
                index += 1
                if row["status"] == "running":
                    continue
                answer = history[index]
                index += 1
                cards = {"orders": [], "products": [], "logistics": []}
                for receipt in conn.execute(
                    "SELECT name,result,error FROM local_receipts "
                    "WHERE session_id=? AND turn_id=? ORDER BY rowid",
                    (sid, row["turn_id"]),
                ):
                    if receipt["error"]:
                        continue
                    value, name = json.loads(receipt["result"]), receipt["name"]
                    if name == "list_orders":
                        cards["orders"] = value.get("orders", [])
                    elif name == "get_order" and value.get("found"):
                        cards["orders"] = [
                            pick(value, ("order_id", "status", "created_at", "items"))
                        ]
                    elif name == "list_products":
                        cards["products"] = value.get("products", [])
                    elif name == "get_product" and value.get("found"):
                        cards["products"] = [
                            pick(
                                value,
                                ("product_id", "name", "current_price", "attributes", "category"),
                            )
                        ]
                    elif name == "get_logistics":
                        cards["logistics"] = [
                            pick(
                                value, ("found", "order_id", "carrier", "status", "trace", "reason")
                            )
                        ]
                # Do not forward complete get_order items (which can contain private fields).
                cards["orders"] = [
                    pick(o, ("order_id", "status", "created_at"))
                    | {
                        "items": [
                            pick(i, ("id", "product_id", "product_name", "quantity"))
                            for i in o.get("items", [])
                        ]
                    }
                    for o in cards["orders"]
                ]
                cards["products"] = [
                    pick(p, ("product_id", "name", "current_price", "attributes", "category"))
                    for p in cards["products"]
                ]
                complete = (
                    row["status"] == "completed" and answer.get("review_status") == "reviewed"
                )
                result.append(
                    answer
                    | {
                        "turn_id": row["turn_id"],
                        "message_id": row["turn_id"] + ":assistant",
                        "complete": complete,
                        "cards": engine.clean(cards),
                        "feedback": feedback.get(row["turn_id"]),
                    }
                )
        return {
            "messages": result,
            "human_service": {
                "available": False,
                "message": "人工坐席尚未接入，无法转接或创建人工工单。",
            },
        }

    @app.post("/api/sessions/{sid}/feedback")
    def feedback(request: Request, sid: str, body: FeedbackBody):
        access(request, sid, True)
        if (body.vote == "unhelpful") != (body.reason is not None):
            raise HTTPException(422, "没用反馈必须选择原因，有用反馈不接受原因")
        public = messages(request, sid)
        if not any(
            m.get("turn_id") == body.turn_id and m.get("complete") for m in public["messages"]
        ):
            raise HTTPException(404, "只能评价当前会话已完成且核验过的客服回答")
        with engine.tx() as conn:
            old = conn.execute(
                "SELECT * FROM ui_feedback WHERE session_id=? AND request_key=?",
                (sid, body.request_key),
            ).fetchone()
            if old:
                if (old["turn_id"], old["vote"], old["reason"]) != (
                    body.turn_id,
                    body.vote,
                    body.reason,
                ):
                    raise HTTPException(409, "反馈标识已用于其他内容")
            else:
                conn.execute(
                    "INSERT INTO ui_feedback VALUES(NULL,?,?,?,?,?,?)",
                    (
                        sid,
                        body.turn_id,
                        body.vote,
                        body.reason,
                        body.request_key,
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return {"persisted": True, "vote": body.vote, "reason": body.reason}

    return app


def create_shop_app(root, model_factory, *, origin="http://127.0.0.1:8767", **kwargs):
    return install_shop(create_app(root, model_factory, origin=origin, **kwargs), origin=origin)
