"""Request-only, reversible sharing of repeated JSON facts; no history eviction."""
from __future__ import annotations

import copy
import json
from collections import Counter

FORMAT = "shared-values-v1"
POOL_PREFIX = "本次请求共享JSON值："
FACT_PREFIX = "当前应用核验事实："
INSTRUCTIONS = (
    "逐项处理本轮全部诉求，结束前核对每项是否已有证据并已回答；未检索到不等于不存在。"
    "政策咨询与业务办理权限独立：consultation=true允许解释模拟咨询规则，"
    "return/warranty=false不能被解释为禁止咨询，仍不得承诺真实资格或执行办理。"
    "已启用的家电模拟咨询来源为appliance_consultation；相关问题先用search_knowledge的"
    "document_id限定该来源。商品规格保留原始单位，不推断安装条件、适用人数或缺失参数。"
    "家电模拟条款未定义含首尾日等日期边界时，只解释签收日起的期限和待核验条件，"
    "不要自行推算截止日期或认定资格。"
)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def project_messages(messages):
    projected = copy.deepcopy(messages)
    payloads, counts = [], Counter()

    def count(value):
        if isinstance(value, (dict, list, str)):
            encoded = dumps(value)
            if len(encoded.encode()) >= 180:
                counts[encoded] += 1
            if not isinstance(value, str):
                for item in value.values() if isinstance(value, dict) else value:
                    count(item)

    for index, message in enumerate(projected):
        text = message.get("content")
        prefix = FACT_PREFIX if message["role"] == "system" and isinstance(text, str) \
            and text.startswith(FACT_PREFIX) else ""
        if not isinstance(text, str) or not (prefix or message["role"] == "tool"):
            continue
        try:
            value = json.loads(text[len(prefix):])
        except ValueError:
            continue
        # Preserve literal data that could collide with the projection contract.
        if '"$context_ref"' in dumps(value) or '"model_format":"shared-values-v1"' in dumps(value):
            return copy.deepcopy(messages)
        payloads.append((index, prefix, value))
        count(value)
    pool, ids = {}, {}

    def encode(value):
        if isinstance(value, (dict, list, str)):
            encoded = dumps(value)
            if counts[encoded] > 1:
                if encoded not in ids:
                    key = "v"+str(len(pool))
                    ids[encoded], pool[key] = key, value
                    if isinstance(value, dict):
                        pool[key] = {k: encode(v) for k, v in value.items()}
                    elif isinstance(value, list):
                        pool[key] = [encode(v) for v in value]
                return {"$context_ref": ids[encoded]}
            if isinstance(value, dict):
                return {k: encode(v) for k, v in value.items()}
            if isinstance(value, list):
                return [encode(v) for v in value]
        return value

    for index, prefix, value in payloads:
        projected[index]["content"] = prefix+dumps(dict(model_format=FORMAT, value=encode(value)))
    if pool:
        projected.append(dict(role="system", content=POOL_PREFIX+dumps(pool)+
            "\n工具/当前事实中的$context_ref引用共享JSON值；可递归展开，未省略任何字段。"))
    else:
        return copy.deepcopy(messages)
    # Do not enlarge small inputs merely to use the projection.
    return projected if len(dumps(projected)) < len(dumps(messages)) else copy.deepcopy(messages)


def expand_messages(messages):
    pool = {}
    for m in messages:
        if m["role"] == "system" and m.get("content", "").startswith(POOL_PREFIX):
            pool = json.loads(m["content"][len(POOL_PREFIX):].split("\n", 1)[0])

    def expand(value, active=()):
        if isinstance(value, dict):
            if set(value) == {"$context_ref"}:
                key = value["$context_ref"]
                if key in active:
                    raise ValueError("Cyclic shared context reference")
                return expand(pool[key], (*active, key))
            return {k: expand(v, active) for k, v in value.items()}
        return [expand(v, active) for v in value] if isinstance(value, list) else value

    output = []
    for message in copy.deepcopy(messages):
        text = message.get("content")
        if message["role"] == "system" and isinstance(text, str) and text.startswith(POOL_PREFIX):
            continue
        prefix = FACT_PREFIX if isinstance(text, str) and text.startswith(FACT_PREFIX) else ""
        if isinstance(text, str) and (message["role"] == "tool" or prefix):
            try:
                value = json.loads(text[len(prefix):])
            except ValueError:
                value = {}
            if isinstance(value, dict) and value.get("model_format") == FORMAT:
                message["content"] = prefix+dumps(expand(value["value"]))
        output.append(message)
    return output


class ShopContextModel:
    """Project the shared SDK boundary, without deciding tools or altering checkpoints."""

    def __init__(self, model):
        self.model, self.tools, self.requests = model, [], []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        from langchain_core.messages import convert_to_openai_messages

        projected = project_messages(convert_to_openai_messages(messages))
        projected.append(dict(role="system", content=INSTRUCTIONS))
        self.requests.append(dict(messages=projected, tools=self.tools))
        return self.model.bind_tools(self.tools).invoke(projected)
