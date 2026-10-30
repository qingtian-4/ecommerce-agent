"""RAG-enhanced, multi-label intent planning.

Intent definitions are versioned data (``data/eval/intent_registry_v1.json``); a small
BM25 index retrieves boundary examples before one JSON-only model call.  The output is
a set of goals, not a hard route: tools stay visible and the planner never authorizes a
write.  Low confidence or conflicting wording asks the user to clarify.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

from .retrieval import tokenize

DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "data/eval/intent_registry_v1.json"
DEFAULT_THRESHOLD = 0.45

SYSTEM = (
    "你是电商售后客服的意图规划器，不是回复生成器。\n"
    "只输出 JSON，不要 Markdown。格式：\n"
    '{"goals":[{"intent":"orders|logistics|product|policy|return|price",'
    '"confidence":0.0}],\n'
    ' "needs_clarification":false,"clarify_question":null}\n\n'
    "规则：\n"
    "- 一句话里有多个诉求时列出全部 goal；不要只取第一个。\n"
    "- 只列回答当前问题“必需”的意图；相关但非必需的意图不要列。\n"
    "- policy 是解释退货/退款/保修/运费/期限；return 是明确要求创建、修改、"
    "取消退货草稿、生成预览或查询本人退货申请。不要把“能退吗”“退款多久”"
    "“退货运费谁出”标成 return。\n"
    "- 订单状态/订单号/订单明细是 orders；物流轨迹/配送/签收是 logistics。"
    "用户问“订单是否签收/签收状态/没收到/快递显示签收/什么时候发货”时，"
    "必须同时列 orders 和 logistics，不能只给 logistics。\n"
    "- 商品规格/颜色/功率/续航/降噪/多少钱/推荐是 product；价保/差价是 price。"
    "商品故障/没声音/颜色不对/维修通常同时需要 product 和 policy；"
    "如果用户在问某个订单里的商品，还要加 orders。\n"
    "- 多轮里“只确定订单/数量重新选择/改成O.../保留草稿/只读核对/不要重复创建”"
    "是在处理退货草稿：需要 return；如果还要订单事实再加 orders。\n"
    "- 否定句要按字面处理：“不要自动选退货对象”“不是要取消”“先别建草稿”"
    "不是退货动作；但“保留草稿/只读核对已有申请”仍可能需要 return 查询。\n"
    "- 用户明确说“取消退货申请/取消草稿”才是 return 的取消动作；"
    "“如果以后取消会怎样”是 policy。\n"
    "- 寒暄或明显超出业务范围：goals=[]；如果问题有歧义或缺少关键对象，"
    "needs_clarification=true 并给出一个简短中文问题。\n"
    "- 如果最近对话已经能消歧，不要仅因为出现代词就澄清；confidence 要校准。"
)


@dataclass(frozen=True)
class IntentDefinition:
    name: str
    label: str
    description: str
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    tools: tuple[str, ...]
    evidence: str


@dataclass(frozen=True)
class PlannedGoal:
    intent: str
    confidence: float = 1.0
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentPlan:
    goals: list[PlannedGoal]
    needs_clarification: bool = False
    clarify_question: str | None = None
    source: str = "llm"
    retrieved: list[dict[str, str]] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [goal.intent for goal in self.goals]

    @property
    def labels(self) -> list[str]:
        return [LABELS.get(goal.intent, goal.intent) for goal in self.goals]

    @property
    def confidence(self) -> float:
        return min((goal.confidence for goal in self.goals), default=0.0)

    def to_hint(self) -> str:
        if self.needs_clarification:
            question = self.clarify_question or "请先确认您具体要处理哪一笔订单或哪一件商品。"
            return (
                "本轮意图存在歧义，先不要猜："
                + question
                + " 澄清前不要创建、修改或取消任何退货草稿。"
            )
        if not self.goals:
            return ""
        labels = "、".join(dict.fromkeys(self.labels))
        tools = sorted(
            {tool for goal in self.goals for tool in REGISTRY_TOOLS.get(goal.intent, ())}
        )
        tool_text = "、".join(tools) if tools else "按需"
        return (
            f"本轮识别到的意图：{labels}。优先使用这些工具：{tool_text}；"
            "这只是优先级提示，不是权限。逐项完成所有 goal，不要因为完成一个就忽略其他；"
            "写操作仍必须经过页面确认。"
        )


LABELS = {
    "orders": "订单查询",
    "logistics": "物流查询",
    "product": "商品咨询",
    "policy": "退换货政策",
    "return": "退换货办理",
    "price": "价保",
}
REGISTRY_TOOLS: dict[str, tuple[str, ...]] = {}
ALLOWED = set(LABELS)


class IntentRegistry:
    def __init__(self, definitions: list[IntentDefinition]) -> None:
        self.definitions = {item.name: item for item in definitions}
        self.docs: list[tuple[str, str, str]] = []
        for item in definitions:
            self.docs.append((item.name, "description", item.description))
            for text in item.positive:
                self.docs.append((item.name, "positive", text))
            for text in item.negative:
                self.docs.append((item.name, "negative", text))
        self.index = BM25Okapi([tokenize(text) for _, _, text in self.docs]) if self.docs else None

    @classmethod
    def load(cls, path: Path = DEFAULT_REGISTRY) -> IntentRegistry:
        data = json.loads(path.read_text(encoding="utf-8"))
        definitions = [
            IntentDefinition(
                name=item["name"],
                label=item["label"],
                description=item["description"],
                positive=tuple(item.get("positive_examples", [])),
                negative=tuple(item.get("negative_examples", [])),
                tools=tuple(item.get("tools", [])),
                evidence=item.get("evidence", ""),
            )
            for item in data["intents"]
        ]
        return cls(definitions)

    def retrieve(self, query: str, k: int = 8) -> list[dict[str, str]]:
        if self.index is None or not query.strip():
            return []
        scores = self.index.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        hits = []
        for index in order[:k]:
            if scores[index] <= 0:
                break
            name, polarity, text = self.docs[index]
            hits.append(
                {
                    "intent": name,
                    "polarity": polarity,
                    "text": text,
                }
            )
        return hits

    def tools_for(self, names: list[str]) -> list[str]:
        tools = {tool for name in names for tool in self.definitions[name].tools}
        return sorted(tools)


def _definitions_block(registry: IntentRegistry) -> str:
    lines = []
    for item in registry.definitions.values():
        lines.append(
            f"- {item.name}（{item.label}）：{item.description} "
            f"正例：{'；'.join(item.positive[:3])} "
            f"反例：{'；'.join(item.negative[:3])}"
        )
    return "\n".join(lines)


def _examples_block(hits: list[dict[str, str]]) -> str:
    if not hits:
        return "（无检索示例）"
    return "\n".join(f"- [{hit['intent']}/{hit['polarity']}] {hit['text']}" for hit in hits)


def _parse(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class IntentPlanner:
    def __init__(
        self,
        llm: Any,
        *,
        registry: IntentRegistry | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        max_tokens: int = 400,
    ) -> None:
        self.llm = llm
        self.registry = registry or IntentRegistry.load()
        self.threshold = threshold
        self.max_tokens = max_tokens
        for name, item in self.registry.definitions.items():
            REGISTRY_TOOLS[name] = item.tools

    def plan(self, text: str, *, history: list[str] | None = None) -> IntentPlan:
        hits = self.registry.retrieve(text, k=8)
        context = ""
        if history:
            context = "\n最近对话（只用于消歧，不要重复回答）：\n" + "\n".join(history[-4:])
        prompt = (
            "意图定义：\n"
            + _definitions_block(self.registry)
            + "\n\n检索到的边界示例：\n"
            + _examples_block(hits)
            + context
            + "\n\n当前用户："
            + text
            + "\n\n只输出 JSON。"
        )
        try:
            response = self.llm.complete(
                prompt,
                system=SYSTEM,
                temperature=0,
                max_tokens=self.max_tokens,
                response_format_json=True,
                stage="intent_plan",
            )
            raw = response.text
        except Exception:
            return IntentPlan([], source="planner_error", retrieved=hits)
        value = _parse(raw)
        if value is None:
            return IntentPlan([], source="planner_invalid", retrieved=hits)
        raw_goals = value.get("goals")
        goals: list[PlannedGoal] = []
        if isinstance(raw_goals, list):
            seen = set()
            for item in raw_goals:
                if isinstance(item, str):
                    name, confidence, slots = item, 1.0, {}
                elif isinstance(item, dict):
                    name = item.get("intent")
                    confidence = item.get("confidence", 1.0)
                    slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
                else:
                    continue
                if name not in ALLOWED or name in seen:
                    continue
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = 0.0
                seen.add(name)
                goals.append(PlannedGoal(name, max(0.0, min(1.0, confidence)), slots))
        question = None
        # Normalise a common split: a delivery exception needs order facts too,
        # even when the model only labels the logistics family.
        goal_names = {goal.intent for goal in goals}
        if (
            "logistics" in goal_names
            and "orders" not in goal_names
            and re.search(r"签收|没收到|没拿到|发货|送到|出库|配送", text)
        ):
            goals.append(PlannedGoal("orders", 0.7))
            goal_names.add("orders")
        clarify = bool(value.get("needs_clarification"))
        if goals and any(goal.confidence < self.threshold for goal in goals):
            clarify = True
        if (
            any(goal.intent == "return" for goal in goals)
            and not re.search(
                r"[Oo]\d{5,}|订单|明细|这笔|这件|这个|刚才|申请|草稿|预览|取消", text
            )
        ):
            clarify = True
            question = "您要退哪一笔订单/哪件商品？"
        if (
            any(goal.intent == "return" for goal in goals)
            and re.search(r"换成|改成", text)
            and re.search(r"颜色|白色|黑色|红色|蓝色", text)
        ):
            clarify = True
            question = "您要改哪一笔订单/哪件商品的颜色？"
        raw_question = value.get("clarify_question")
        if not isinstance(raw_question, str) or not raw_question.strip():
            raw_question = None
        if raw_question:
            question = raw_question
        return IntentPlan(
            goals=goals,
            needs_clarification=clarify,
            clarify_question=question,
            source="llm",
            retrieved=hits,
        )
