"""Model-only compaction of prior long assistant answers.

The full answer remains in SQLite, the UI and the Graph checkpoint.  Only the model
request gets a short marker, so a later turn does not re-send a full catalogue/order
table that the model can obtain again through tools.  Tool messages, user messages
and the current turn are never rewritten here.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

MARKER = "\n\n（历史长回答已省略；业务事实以当前工具回执为准，需要明细请重新查询。）"


def compact_long_assistant_answers(
    messages: list,
    *,
    max_chars: int = 1500,
    keep_chars: int = 500,
) -> list:
    compacted = []
    for message in messages:
        if (
            isinstance(message, AIMessage)
            and not message.tool_calls
            and isinstance(message.content, str)
            and len(message.content) > max_chars
        ):
            text = message.content[:keep_chars].rstrip() + MARKER
            compacted.append(AIMessage(content=text))
        else:
            compacted.append(message)
    return compacted
