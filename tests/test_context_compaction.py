"""Model-only compaction of prior long answers; full evidence stays elsewhere."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from acs.context_compaction import MARKER, compact_long_assistant_answers


def test_long_final_answer_is_shortened_only_in_the_model_view():
    original = AIMessage(content="订单清单" * 800)
    tool = ToolMessage(content='{"orders": []}', tool_call_id="t")
    human = HumanMessage(content="继续")
    compacted = compact_long_assistant_answers([human, original, tool], max_chars=1000)

    assert len(compacted[1].content) < 800
    assert compacted[1].content.endswith(MARKER)
    assert compacted[0] is human
    assert compacted[2] is tool
    assert len(original.content) == 3200


def test_tool_calls_and_short_answers_are_not_compacted():
    calling = AIMessage(
        content="",
        tool_calls=[{"id": "c", "name": "list_orders", "args": {}}],
    )
    short = AIMessage(content="共 38 笔订单。")
    assert compact_long_assistant_answers([calling, short], max_chars=10) == [calling, short]
