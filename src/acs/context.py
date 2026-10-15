"""Bound model input by characters without splitting completed tool rounds."""

from __future__ import annotations

import json


class ContextLimitExceeded(ValueError):
    pass


def input_chars(messages: list[dict], tools: list[dict]) -> int:
    """A reproducible character measure, deliberately not a token estimate."""
    return len(json.dumps({"messages": messages, "tools": tools},
                          ensure_ascii=False, separators=(",", ":")))


def bounded_context(required: list[dict], history: list[dict], query: str,
                    tools: list[dict], limit: int) -> tuple[list[dict], dict]:
    current = [{"role": "user", "content": query}]
    if input_chars(required + current, tools) > limit:
        raise ContextLimitExceeded(
            "当前问题、业务草稿及必要规则超过输入上限，请缩短问题或新建会话。",
        )
    turns: list[list[dict]] = []
    for message in history:
        if message["role"] == "user":
            turns.append([])
        if not turns:
            raise ValueError("历史消息必须从完整用户轮次开始")
        turns[-1].append(message)
    selected: list[dict] = []
    retained = 0
    for turn in reversed(turns):
        if input_chars(required + turn + selected + current, tools) > limit:
            break  # Keep a contiguous suffix, not disconnected fragments.
        selected = turn + selected
        retained += 1
    messages = required[:1] + selected + required[1:]
    return messages, {"limit_chars": limit, "input_chars": input_chars(messages + current, tools),
                      "retained_turns": retained, "dropped_turns": len(turns) - retained}


def model_context(messages: list[dict], current_start: int, tools: list[dict],
                  limit: int) -> list[dict]:
    """Also enforce the limit after tools add content during the current turn."""
    previous = messages[:current_start]
    system = [m for m in previous if m["role"] == "system"]
    history = [m for m in previous if m["role"] != "system"]
    current = messages[current_start:]
    # Current tool results and requests stay intact, even if they cannot fit.
    if input_chars(system + current, tools) > limit:
        raise ContextLimitExceeded("当前问题及工具返回超过输入上限，请缩小问题范围")
    while input_chars(system + history + current, tools) > limit:
        boundary = next((i for i, m in enumerate(history[1:], 1) if m["role"] == "user"),
                        len(history))
        history = history[boundary:]
    return system[:1] + history + system[1:] + current
