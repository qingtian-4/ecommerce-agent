"""Business tools: read-only SQL lookups plus knowledge search.

Two rules that shape this file:

1. **Identity is injected, not generated.** The LLM never supplies `user_id`.
   The registry receives the authenticated user and passes it to every tool, so
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

from .config import Settings, get_settings
from .database import BusinessDB


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
    """Holds the read-only tools and executes them with trace events."""

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
                    "购买单价、当前售价、差价，以及退货与价保期限判断。"
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
                        "documents": result.get("documents", []),
                    },
                )
            )
        events.append(TraceEvent(type="tool_result", label=name, detail={"result": result}))
        return result, events, None

    # ------------------------------------------------------------------ #
    # tool implementations
    # ------------------------------------------------------------------ #
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
        if not chunks:
            return {
                "found": False,
                "query": query,
                "retrieval_mode": self.settings.retrieval_mode,
                "reason": "知识库中没有找到相关内容",
                "documents": [],
            }
        return {
            "found": True,
            "query": query,
            "retrieval_mode": self.settings.retrieval_mode,
            "documents": [c.as_dict() for c in chunks],
        }
