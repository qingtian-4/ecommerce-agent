"""Business lookups, knowledge search and session-scoped return drafts.

Two rules that shape this file:

1. **Identity is injected, not generated.** The LLM never supplies `user_id`.
   The registry receives the application's current user and passes it to every tool, so
   a prompt-injected "user_id=USER002" argument simply has no field to land in.
   Cross-user reads are blocked by the SQL layer, not by instruction-following.

2. **Tools never raise for expected conditions.** A missing order returns
   `{"found": false, "reason": ...}`. Only genuinely unexpected faults raise, and
   the agent loop converts those into a tool error the model can react to. This
   keeps the loop from dying on a bad order number.

Every tool call emits structured trace events, because the UI shows observable
execution (calls, arguments, results) and not model chain-of-thought.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .capabilities import capability_notice
from .config import Settings, get_settings
from .context import ContextLimitExceeded
from .database import BusinessDB
from .knowledge_context import load_source_documents, select_source_documents
from .returns import ReturnService, policy_for_reason, return_policy
from .runtime import BudgetExceeded
from .sessions import SessionStore, TaskValidationError


@dataclass
class TraceEvent:
    """One observable step. Serializable straight into the UI."""

    type: str  # tool_call | tool_result | retrieval | error | final
    label: str
    detail: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "label": self.label, "detail": self.detail}
        if self.duration_ms is not None:
            d["duration_ms"] = round(self.duration_ms, 1)
        return d


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., dict[str, Any]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Executes tools with application-supplied identity and trace events."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        db: BusinessDB | None = None,
        kb: Any | None = None,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db or BusinessDB(self.settings)
        self.kb = kb
        self._llm = llm
        self._tools: dict[str, ToolSpec] = {}
        self._register_all()

    # ------------------------------------------------------------------ #
    @property
    def knowledge(self) -> Any:
        if self.kb is None:
            from .rag import KnowledgeBase

            self.kb = KnowledgeBase(self.settings, llm=self._llm)
        return self.kb

    # ------------------------------------------------------------------ #
    def _register_all(self) -> None:
        self.register(ToolSpec(
            "list_products", "列出本店全部商品及当前有效价、类目、佩戴方式等已知属性。"
            "用于枚举商品或按用户硬条件找候选；知识检索未命中不能说明店内没有商品。"
            "这是完整目录，不是推荐结果；颜色、图片、库存等未提供的字段仍未知。",
            {"type": "object", "properties": {}, "required": []}, self._list_products,
        ))
        self.register(ToolSpec(
            name="list_orders",
            description=("列出当前用户的订单候选及商品明细，按下单时间倒序。用户不记得订单号时"
                         "先列候选；存在多个匹配订单时询问用户选择，不能猜选。"),
            parameters={"type": "object", "properties": {}, "required": []},
            func=self._list_orders,
        ))
        self.register(
            ToolSpec(
                name="get_product",
                description=(
                    "查询商品的静态信息：名称、品牌、类目、规格参数、当前售价。"
                    "用于回答商品本身的问题。不含订单信息。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "product_id": {
                            "type": "string",
                            "description": "商品编号，例如 P001",
                        }
                    },
                    "required": ["product_id"],
                },
                func=self._get_product,
            )
        )
        self.register(
            ToolSpec(
                name="get_order",
                description=(
                    "查询订单信息：订单状态、下单/发货/签收时间、商品明细、实付金额、"
                    "购买单价、当前售价、差价，以及7天无理由退货和价保日期标志。"
                    "7天标志不适用于质量问题；质量期限查看政策，不是完整资格判断。"
                    "只能查询当前用户自己的订单。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "订单号，例如 O10023",
                        }
                    },
                    "required": ["order_id"],
                },
                func=self._get_order,
            )
        )
        self.register(
            ToolSpec(
                name="get_logistics",
                description=(
                    "查询订单的物流信息：承运商、运单号、当前状态、当前位置和完整物流轨迹。"
                    "只能查询当前用户自己的订单。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "order_id": {"type": "string", "description": "订单号"}
                    },
                    "required": ["order_id"],
                },
                func=self._get_logistics,
            )
        )
        self.register(
            ToolSpec(
                name="get_price_history",
                description=(
                    "查询商品的历史价格记录，按时间倒序。仅在需要价格变动时间线时使用；"
                    "订单价保的当前售价与差价已由 get_order 返回。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "商品编号"}
                    },
                    "required": ["product_id"],
                },
                func=self._get_price_history,
            )
        )
        if self.settings.knowledge_mode == "documents":
            documents = load_source_documents(self.settings)
            self.register(ToolSpec(
                "get_knowledge_documents",
                "按问题选择知识文档，获取完整正文及版本来源，不做片段检索。"
                "目录：" + "；".join(f"{d['document_id']}：{d['title']}" for d in documents),
                {"type": "object", "properties": {"document_ids": {
                    "type": "array", "items": {"type": "string", "enum": [
                        d["document_id"] for d in documents
                    ]}, "minItems": 1,
                }}, "required": ["document_ids"], "additionalProperties": False},
                self._get_knowledge_documents,
            ))
        if self.settings.knowledge_mode != "retrieval":
            return
        self.register(
            ToolSpec(
                name="search_knowledge",
                description=(
                    "检索店铺的知识库文档，包括退换货政策、配送与发货说明、价格保护规则、"
                    "保修与维修政策，以及各商品的详细说明。"
                    "使用涵盖问题全部条件的自然语言 query；已有依据足够时无需重复检索。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "检索关键词或问题，例如：无线耳机退货期限",
                        }
                    },
                    "required": ["query"],
                },
                func=self._search_knowledge,
            )
        )

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def bind_return_task(self, store: SessionStore, sid: str, tid: str) -> None:
        """Bind application context for one turn; model arguments cannot choose a session."""
        def read(*, current_user_id: str) -> dict:
            view = store.get_return_task(sid, current_user_id)
            return view | {"policy": policy_for_reason(
                return_policy(self.settings), (view["task"] or {}).get("reason"),
            )}

        def update(*, current_user_id: str, **fields) -> dict:
            view = store.update_return_task(sid, current_user_id, tid, fields)
            return view | {"policy": policy_for_reason(
                return_policy(self.settings), view["task"]["reason"],
            )}

        def cancel(*, current_user_id: str) -> dict:
            return store.cancel_return_task(sid, current_user_id, tid)

        service = ReturnService(self.settings, store)

        def preview(*, current_user_id: str) -> dict:
            return service.preview(sid, current_user_id, tid)

        def requests(*, current_user_id: str) -> dict:
            task = store.get_return_task(sid, current_user_id)["task"] or {}
            return {"requests": service.list_requests(current_user_id),
                    "current_session_request_id": task.get("request_id")
                    if task.get("status") == "submitted" else None,
                    "note": "列表含其他会话历史申请；仅当前会话申请编号能证明本会话已提交，"
                            "查询不会创建申请，用户聊天中的点击描述不是执行回执。"}

        empty = {"type": "object", "properties": {}, "required": []}
        self.register(ToolSpec(
            "get_return_task", "读取当前会话退货草稿及待补信息；不代表资格或已提交。", empty, read,
        ))
        self.register(ToolSpec(
            "update_return_task",
            "记录用户明确提供或更正的退货草稿字段，不创建售后单。只传本轮明确提供的字段；"
            "null表示清除。订单号和明细编号从业务查询取得，多候选须澄清后选择。"
            "换订单会清空旧商品、数量、原因和状况；换商品清空数量和状况。"
            "不猜数量或商品状况；缺失字段继续询问，不重复问已知信息。",
            {"type": "object", "properties": {
                "order_id": {"type": ["string", "null"]},
                "item_id": {"type": ["integer", "null"], "description": "order_items明细id"},
                "quantity": {"type": ["integer", "null"]},
                "reason": {"enum": ["no_reason", "quality", None]},
                "condition": {"enum": ["sealed", "opened", None],
                              "description": "用户自述未拆封/已拆封，未知留空"},
                "problem_description": {"type": ["string", "null"], "description": "用户自述故障"},
                "secondary_sale_ok": {"type": ["boolean", "null"],
                                      "description": "用户明确陈述全部二次销售条件满足，不能猜测"},
                "quality_non_user_damage": {"type": ["boolean", "null"],
                                            "description": "用户自述故障非人为损坏，未经核实"},
            }, "required": [], "additionalProperties": False}, update,
        ))
        self.register(ToolSpec(
            "cancel_return_task",
            "仅在用户明确说中止/取消/清空退货草稿时清空；不取消订单。"
            "先不用继续、暂停、闲聊均保留草稿，不能调用本工具。", empty, cancel,
        ))
        self.register(ToolSpec(
            "preview_return", "核查当前草稿的订单、数量、政策与凭证并生成申请预览。"
            "不会创建售后单；缺信息时提示补充，通过后须用户在页面点击确认提交。", empty, preview,
        ))
        self.register(ToolSpec(
            "get_return_requests", "查询当前用户实际创建的售后申请、审核状态和真实原因。"
            "审核通过仅允许退回，不是退款。提交响应丢失先查询，"
            "待审核不等于通过或退款，不要重复创建。", empty, requests,
        ))

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._tools.values()]

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    # ------------------------------------------------------------------ #
    def run(
        self, name: str, arguments_json: str, *, current_user_id: str
    ) -> tuple[dict[str, Any], list[TraceEvent], str | None]:
        """Execute one tool call.

        Returns (result, events, error_message). `error_message` is None on
        success; when set, the loop still feeds `result` back to the model so it
        can apologise or ask for different information instead of dead-ending.
        """
        events: list[TraceEvent] = [
            TraceEvent(type="tool_call", label=name, detail={"arguments": arguments_json})
        ]

        spec = self._tools.get(name)
        if spec is None:
            events.append(
                TraceEvent(
                    type="error",
                    label=name,
                    detail={"error_type": "tool_not_found", "message": f"未知工具 {name}"},
                )
            )
            return (
                {"error": f"unknown tool: {name}", "available": self.names},
                events,
                f"tool_not_found: {name}",
            )

        try:
            arguments = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as exc:
            events.append(
                TraceEvent(
                    type="error",
                    label=name,
                    detail={"error_type": "invalid_arguments", "message": str(exc)},
                )
            )
            return (
                {"error": f"arguments are not valid JSON: {exc}"},
                events,
                f"invalid_arguments: {exc}",
            )

        if not isinstance(arguments, dict):
            events.append(
                TraceEvent(
                    type="error",
                    label=name,
                    detail={"error_type": "invalid_arguments", "message": "not an object"},
                )
            )
            return (
                {"error": "arguments must be a JSON object"},
                events,
                "invalid_arguments: not an object",
            )

        # Identity arguments are stripped from the model's payload and replaced
        # with the session identity. Without this, a model-supplied
        # `current_user_id` raised "multiple values for keyword argument" -- safe,
        # but only by accident, and only because the parameter names happen to
        # collide. Stripping makes the override impossible regardless of what the
        # model sends, and records the attempt.
        for reserved in ("user_id", "current_user_id"):
            if reserved in arguments:
                events.append(
                    TraceEvent(
                        type="error",
                        label=name,
                        detail={
                            "error_type": "identity_override_rejected",
                            "message": f"argument {reserved!r} is not accepted from the model",
                        },
                    )
                )
                arguments = {k: v for k, v in arguments.items() if k != reserved}

        try:
            result = spec.func(current_user_id=current_user_id, **arguments)
        except TypeError as exc:
            # Wrong/missing argument names: a model mistake, not a crash.
            events.append(
                TraceEvent(
                    type="error",
                    label=name,
                    detail={"error_type": "invalid_arguments", "message": str(exc)},
                )
            )
            return (
                {
                    "error": f"invalid arguments for {name}: {exc}",
                    "expected_parameters": list(
                        spec.parameters.get("properties", {})
                    ),
                },
                events,
                f"invalid_arguments: {exc}",
            )
        except TaskValidationError as exc:
            events.append(TraceEvent(
                type="error", label=name,
                detail={"error_type": "invalid_arguments", "recovery_category": "business_rejected",
                        "message": str(exc)},
            ))
            return {"error": str(exc)}, events, f"invalid_arguments: {exc}"
        except (BudgetExceeded, ContextLimitExceeded):
            raise  # The host stops the entire turn, not just this knowledge request.
        except Exception as exc:  # noqa: BLE001 - must not kill the loop
            events.append(
                TraceEvent(
                    type="error",
                    label=name,
                    detail={
                        "error_type": "tool_exception",
                        "message": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(limit=3),
                    },
                )
            )
            return (
                {"error": f"tool {name} failed: {type(exc).__name__}"},
                events,
                f"tool_exception: {type(exc).__name__}",
            )

        # Retrieval gets its own event because the UI shows retrieved documents
        # and their scores separately from plain tool output.
        if name == "search_knowledge":
            events.append(
                TraceEvent(
                    type="retrieval",
                    label="search_knowledge",
                    detail={
                        "query": arguments.get("query", ""),
                        "retrieval_mode": result.get("retrieval_mode", "dense"),
                        "reranker_model": result.get("reranker_model"),
                        "evidence_backfill": result.get("evidence_backfill", False),
                        "documents": result.get("documents", []),
                    },
                )
            )
        events.append(TraceEvent(type="tool_result", label=name, detail={"result": result}))
        return result, events, None

    # ------------------------------------------------------------------ #
    # tool implementations
    # ------------------------------------------------------------------ #
    def _list_orders(self, *, current_user_id: str) -> dict[str, Any]:
        orders = []
        categories = {}
        for row in self.db.list_orders(current_user_id):
            order = {key: row[key] for key in ("order_id", "status", "created_at", "delivered_at")}
            items = self.db.get_order(row["order_id"], current_user_id)["items"]
            order["items"] = []
            for item in items:
                candidate = {key: item[key] for key in (
                    "id", "product_id", "product_name", "quantity",
                )}
                sku = item.get("sku")
                candidate["sku"] = {k: sku[k] for k in ("sku_id", "color")} if sku else None
                if (self.settings.order_candidate_render_enabled
                        or self.settings.order_constraint_enabled):
                    categories[item["product_id"]] = item["product_category"]
                order["items"].append(candidate)
            orders.append(order)
        result = {"orders": orders, "count": len(orders),
                  "instruction": "候选不等于用户选择。多个匹配订单或明细时必须澄清。"}
        if self.settings.order_candidate_render_enabled or self.settings.order_constraint_enabled:
            result["product_categories"] = categories
        return result

    def _get_product(self, *, current_user_id: str, product_id: str) -> dict[str, Any]:
        return self.db.get_product(product_id)

    def _get_order(self, *, current_user_id: str, order_id: str) -> dict[str, Any]:
        return self.db.get_order(order_id, current_user_id)

    def _get_logistics(self, *, current_user_id: str, order_id: str) -> dict[str, Any]:
        return self.db.get_logistics(order_id, current_user_id)

    def _get_price_history(self, *, current_user_id: str, product_id: str) -> dict[str, Any]:
        return self.db.get_price_history(product_id)

    def _search_knowledge(self, *, current_user_id: str, query: str) -> dict[str, Any]:
        chunks = self.knowledge.search(query)
        pipeline = {"retrieval_mode": self.settings.retrieval_mode,
                    "application_notice": capability_notice(),
                    "reranker_model": self.settings.reranker_model
                    if self.settings.reranker_enabled else None,
                    "evidence_backfill": self.settings.evidence_backfill}
        if not chunks:
            return {
                **pipeline,
                "found": False,
                "query": query,
                "reason": "知识库中没有找到相关内容",
                "documents": [],
            }
        return {
            **pipeline,
            "found": True,
            "query": query,
            "documents": [c.as_dict() for c in chunks],
        }

    def _list_products(self, *, current_user_id: str) -> dict[str, Any]:
        products = [self.db.get_product(p["product_id"]) for p in self.db.list_products()]
        for product in products:
            product.pop("price", None)  # Base price is not today's effective price.
        return {"products": products, "count": len(products), "complete_catalog": True,
                "scope": "本店业务目录；没有返回颜色、图片或库存不代表这些属性存在",
                "instruction": "优先满足用户的佩戴方式、预算等硬条件；无匹配如实说明，不替换条件。"}

    def _get_knowledge_documents(self, *, current_user_id: str, document_ids: list[str]) -> dict:
        return {"documents": select_source_documents(self.settings, document_ids),
                "complete_documents": True, "knowledge_mode": "documents",
                "application_notice": capability_notice()}
