"""Persistence, ownership and interrupted tool rounds; all checks are offline."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from acs.db_seed import build
from acs.sessions import ConversationBusy, SessionStore


@pytest.fixture
def store(settings):
    return SessionStore(settings.db_path)


def messages(text="查订单"):
    return [{"role": "user", "content": text},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "o", "type": "function", "function": {
                    "name": "get_order", "arguments": '{"order_id":"O10024"}',
                }},
            ]},
            {"role": "tool", "tool_call_id": "o", "content": '{"found":true}'},
            {"role": "assistant", "content": "已查询订单"}]


def test_completed_tool_pair_survives_a_fresh_process(store, settings):
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "查订单")
    store.finish_turn(sid, "USER001", tid, messages(), {"answer": "已查询订单"})
    script = (
        "import json,sys; from pathlib import Path; from acs.sessions import SessionStore; "
        "print(json.dumps(SessionStore(Path(sys.argv[1])).load(sys.argv[2], 'USER001')))"
    )
    child = subprocess.run([sys.executable, "-c", script, str(settings.db_path), sid],
                           check=True, capture_output=True, text=True)
    result = json.loads(child.stdout)
    assert result["messages"] == messages()
    assert result["history"][-1]["content"] == "已查询订单"
    assert result["last_result"]["answer"] == "已查询订单"


def test_new_conversation_is_empty_and_other_owners_cannot_read_or_mutate(store):
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "查订单")
    store.finish_turn(sid, "USER001", tid, messages(), {"answer": "已查询订单"})
    assert not store.list("USER002")
    for action in [lambda: store.load(sid, "USER002"),
                   lambda: store.begin_turn(sid, "USER002", "偷看"),
                   lambda: store.finish_turn(sid, "USER002", tid, messages(), {})]:
        with pytest.raises(PermissionError):
            action()
    empty = store.load(store.create("USER001"), "USER001")
    assert empty["messages"] == empty["history"] == []


def test_only_one_concurrent_request_can_reserve_the_same_conversation(store):
    sid = store.create("USER001")

    def start():
        try:
            return store.begin_turn(sid, "USER001", "查订单")
        except ConversationBusy:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert len(store.load(sid, "USER001")["history"]) == 1


def test_restart_marks_unfinished_turn_and_does_not_replay_or_keep_orphan_tool_calls(store):
    sid = store.create("USER001")
    store.begin_turn(sid, "USER001", "查订单")
    assert store.recover_interrupted() == 1
    state = store.load(sid, "USER001")
    assert state["last_result"]["stop_reason"] == "interrupted"
    assert "中断" in state["history"][-1]["content"]
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]
    assert store.recover_interrupted() == 0
    assert store.begin_turn(sid, "USER001", "再查询")


def test_incomplete_tool_pairs_cannot_be_saved_as_a_completed_turn(store):
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "查订单")
    with pytest.raises(ValueError, match="工具"):
        store.finish_turn(sid, "USER001", tid, messages()[:2], {"answer": "错误成功"})
    assert store.load(sid, "USER001")["last_result"] is None
    store.interrupt_turn(sid, "USER001", tid, "回答未完成")
    assert store.load(sid, "USER001")["messages"][-1]["content"] == "回答未完成"


def test_repeated_migration_preserves_history_and_seed_cannot_destroy_it(store, settings):
    sid = store.create("USER001")
    other = SessionStore(settings.db_path)
    assert other.list("USER001")[0]["session_id"] == sid
    with pytest.raises(RuntimeError, match="会话"):
        build(settings.db_path)
    assert other.load(sid, "USER001")["history"] == []
