"""Minimal, transparent function-calling agent loop. No LangGraph.

The whole control flow, deliberately:

    while step < max_steps:
        response = llm.chat(messages, tools=tool_schemas)
        if response.tool_calls:
            run each tool, append results, record trace events
        else:
            return response.text as the final answer

Why hand-written rather than a framework: the loop is the thing under study.
When a router is added later, "which path did this request take" has to be
answerable by reading this file. A graph framework would hide that in edge
declarations, and the request would not be unit-testable without executing the
whole graph.

What this loop deliberately does NOT do: no router, no planner, no reflection,
no self-critique, no long-term memory. The model decides whether to call tools,
which ones, how many, and when to stop -- that is the baseline everything later
is measured against.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Any

from .config import Settings, get_settings
from .cs_tools import ToolRegistry, TraceEvent
from .llm import LLMClient

SYSTEM_PROMPT = """你是一名专业的电商客服助手，服务于一家销售无线耳机与充电配件的店铺。

## 你拥有的工具

- `search_knowledge`：检索店铺政策与商品说明
  （退换货政策、配送说明、价格保护规则、保修政策、各商品详细参数）。
- `get_product`：查询商品本身的信息与当前售价。
- `get_order`：查询订单状态、时间、商品明细、实付金额与购买单价，
  以及当前售价、差价、退货与价保期限判断。
- `get_logistics`：查询订单的物流状态与轨迹。
- `get_price_history`：查询商品的历史价格变动。

## 必须遵守的规则

1. **政策、规则、期限、商品规格**必须来自 `search_knowledge`，不得凭记忆回答。
   业务工具的期限标志只表示日期窗口，不代表完整资格，不能代替政策检索。
2. **订单、物流、价格等实时事实**必须来自对应的业务工具，不得猜测或编造。
3. 工具没有返回的信息，就是不知道的信息。**不要编造**，可以如实告知需要进一步核实。
4. 如果用户没有提供必要信息（例如订单号），**先向用户追问**，不要自行假设一个订单号。
5. **只能查询当前用户自己的订单**。工具会做权限校验；
   用户明确说是朋友或他人的订单时，直接说明只能查询本人订单，不调用业务工具。
   如果工具返回无权查询，同样如实拒绝。
6. 先调用工具拿到事实，再组织回答。涉及政策的问题不要跳过检索直接回答。
7. 回答要简洁、自然、口语化，像真人客服。不要输出 JSON，不要罗列工具名称或内部步骤。
8. **日期一律使用工具返回值**（例如 `delivered_date`、`delivered_days_ago`），
   不要自己推算。查订单状态、金额、签收日期只需 `get_order`；
   问订单到哪了，先查订单再查物流；仅问物流轨迹或揽收，只需 `get_logistics`。
   退货与价保先查订单，再检索对应政策，包括已取消订单；订单已含现价与差价，
   无需重复查价格历史。发现买贵或降价时，核对价保政策后再说明资格与起算点。
   判断退货资格时，同时说明政策中的二次销售条件，不能只看天数。
9. **用完整问题检索**，合并相关条件。已有结果足够回答时就结束，
   只有缺少必要依据才补查。纯政策与规格咨询不调用业务工具。
10. 如果检索不到相关内容，如实说明，不要猜测政策内容。
11. 工具均为只读，不能退款、发起售后或转接。仅要求执行退款时，先说明需申请售后，
    再确认退款原因；需要判断资格时才查询。不得宣称已办理，不重复追问已提供的信息。

## 回答风格

- 用中文，语气礼貌、直接。
- 先给结论，再补充必要依据（例如"根据退换货政策，签收后 7 天内…"）。
- 涉及金额和时间时，给出具体数字。
- 不需要向用户复述你调用了哪些工具。
"""


@dataclass
class AgentResult:
    answer: str
    trace: list[TraceEvent] = field(default_factory=list)
    steps: int = 0
    tool_calls: int = 0
    retrieval_queries: list[str] = field(default_factory=list)
    retrieved_documents: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stop_reason: str = "answered"  # running | answered | max_steps | llm_error
    totals: dict[str, Any] = field(default_factory=dict)

    def decision_summary(self) -> str:
        """One short observable sentence. Not chain-of-thought.

        Derived purely from which tools ran -- it says what the agent did, which
        is already visible in the trace, never why in the model's own words.
        """
        if self.stop_reason == "max_steps":
            return f"达到最大步数 {self.steps}，已停止并给出兜底回复。"
        if self.stop_reason == "llm_error":
            return "调用模型时出错，未能完成回答。"
        used = [e.label for e in self.trace if e.type == "tool_call"]
        if not used:
            return "未调用任何工具，直接回答。"
        parts = []
        if "search_knowledge" in used:
            parts.append("检索了知识库")
        business = [n for n in used if n != "search_knowledge"]
        if business:
            parts.append("调用了业务工具 " + "、".join(dict.fromkeys(business)))
        return "，".join(parts) + f"，共 {len(used)} 次工具调用。"


class CustomerServiceAgent:
    """Stateful per-session agent: owns the message history."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        current_user_id: str = "USER001",
        max_steps: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(settings=self.settings)
        self.registry = registry or ToolRegistry(self.settings, llm=self.llm)
        self.current_user_id = current_user_id
        self.max_steps = max_steps or self.settings.max_steps
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    # ------------------------------------------------------------------ #
    def set_user(self, user_id: str) -> None:
        self.current_user_id = user_id

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # ------------------------------------------------------------------ #
    def chat(self, user_message: str) -> AgentResult:
        """Run one user turn to completion, including any tool-call rounds."""
        for _result in self.chat_stream(user_message):
            pass
        return _result

    def _snapshot(self, result: AgentResult, started: float) -> AgentResult:
        totals = self.llm.totals()
        totals["api_latency_ms"] = totals.get("latency_ms", 0.0)
        totals["latency_ms"] = round((perf_counter() - started) * 1000, 1)
        return replace(
            result, trace=list(result.trace), errors=list(result.errors), totals=totals,
            retrieval_queries=list(result.retrieval_queries),
            retrieved_documents=list(result.retrieved_documents),
        )

    def chat_stream(self, user_message: str) -> Iterator[AgentResult]:
        """Yield execution snapshots before slow calls and after tool results."""
        started = perf_counter()
        self.llm.reset_usage()
        self.messages.append({"role": "user", "content": user_message})

        result = AgentResult(answer="", stop_reason="running")
        schemas = self.registry.schemas()

        for step in range(1, self.max_steps + 1):
            result.steps = step
            yield self._snapshot(result, started)
            try:
                response = self.llm.chat(
                    self.messages, tools=schemas, stage=f"agent_step_{step}"
                )
            except Exception as exc:  # noqa: BLE001 - must surface, not crash the UI
                message = f"{type(exc).__name__}: {exc}"
                result.errors.append(f"llm_error: {message}")
                result.trace.append(
                    TraceEvent(
                        type="error",
                        label="llm",
                        detail={"error_type": "llm_api_error", "message": message},
                    )
                )
                result.answer = (
                    "抱歉，我这边连接服务时出了点问题，请稍后再试一次。"
                    "如果反复出现，可以联系人工客服。"
                )
                result.stop_reason = "llm_error"
                self.messages.append({"role": "assistant", "content": result.answer})
                break

            # --- no tool calls: this is the final answer ------------------ #
            if not response.wants_tools:
                result.answer = (
                    response.text.strip()
                    or "抱歉，我没能组织出回答，请再描述一下您的问题。"
                )
                result.trace.append(
                    TraceEvent(type="final", label="answer", detail={"text": result.answer})
                )
                result.stop_reason = "answered"
                self.messages.append({"role": "assistant", "content": result.answer})
                break

            # --- tool calls: execute, append results, continue ------------- #
            self.messages.append(self._assistant_message(response))
            for call in response.tool_calls:
                result.tool_calls += 1
                result.trace.append(TraceEvent(
                    type="tool_call", label=call.name, detail={"arguments": call.arguments},
                ))
                yield self._snapshot(result, started)
                payload, events, error = self.registry.run(
                    call.name, call.arguments, current_user_id=self.current_user_id
                )
                # The registry's first event is the call already published above.
                result.trace.extend(events[1:])
                if error:
                    result.errors.append(error)
                for event in events:
                    if event.type == "retrieval":
                        result.retrieval_queries.append(event.detail.get("query", ""))
                        result.retrieved_documents.extend(
                            event.detail.get("documents", [])
                        )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    }
                )
                yield self._snapshot(result, started)
        else:
            # Loop exhausted without a final answer.
            result.stop_reason = "max_steps"
            result.answer = (
                "抱歉，这个问题需要核实的信息比较多，我暂时没能给出准确答复。"
                "请联系人工客服继续处理。"
            )
            result.errors.append(f"max_steps_exceeded: {self.max_steps}")
            result.trace.append(
                TraceEvent(
                    type="error",
                    label="agent_loop",
                    detail={
                        "error_type": "max_steps_exceeded",
                        "message": f"stopped after {self.max_steps} steps",
                    },
                )
            )
            self.messages.append({"role": "assistant", "content": result.answer})

        yield self._snapshot(result, started)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _assistant_message(response: Any) -> dict[str, Any]:
        """Serialise the assistant turn, preserving tool_calls.

        The API requires the assistant message that requested tools to be echoed
        back before the tool results, otherwise the tool messages have nothing to
        attach to and the next call fails.
        """
        return {
            "role": "assistant",
            "content": response.text or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in response.tool_calls
            ],
        }
