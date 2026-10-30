"""User-scoped memory MVP: facts, summaries, episodes, expiry and deletion."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from acs.memory import MemoryStore, extract_facts
from acs.sessions import SessionStore


@pytest.fixture()
def store(settings):
    SessionStore(settings.db_path)  # apply migration 6
    return MemoryStore(settings.db_path)


def test_facts_are_user_scoped_and_source_tagged(store):
    fact = store.add_fact(
        "USER001",
        "偏好入耳式耳机",
        source_session_id="s1",
        source_turn_id="t1",
    )
    assert fact.kind == "preference"
    assert store.list_facts("USER001")[0].text == "偏好入耳式耳机"
    assert store.list_facts("USER002") == []
    assert store.delete_fact("USER002", fact.fact_id) is False
    assert store.delete_fact("USER001", fact.fact_id) is True
    assert store.list_facts("USER001") == []


def test_expired_fact_is_hidden_but_auditable(store):
    store.add_fact("USER001", "过期偏好", ttl_days=-1)
    assert store.list_facts("USER001") == []
    assert [f.text for f in store.list_facts("USER001", include_expired=True)] == ["过期偏好"]


def test_duplicate_fact_updates_and_undeletes(store):
    first = store.add_fact("USER001", "预算500")
    assert store.delete_fact("USER001", first.fact_id)
    second = store.add_fact("USER001", "预算500")
    assert second.fact_id == first.fact_id
    assert [fact.text for fact in store.list_facts("USER001")] == ["预算500"]


def test_extract_only_explicit_non_pii_preferences():
    facts = extract_facts("请记住我偏好入耳式，预算500以内")
    assert any("入耳式" in text for _, text in facts)
    assert extract_facts("以后怎么办") == []
    assert extract_facts("你好") == []
    assert extract_facts("记住我的手机号13800138000") == []
    assert extract_facts("我叫小明") == [("profile", "小明")]


def test_context_contains_facts_summary_episode_and_is_bounded(store):
    store.add_fact("USER001", "偏好开放式耳机")
    store.upsert_summary("s1", "USER001", "用户咨询过订单")
    store.add_episode("USER001", "s1", "t1", "用户查询订单状态", outcome={"tools": ["get_order"]})
    text = store.context("USER001", "推荐耳机", max_chars=200)
    assert "偏好开放式耳机" in text
    assert "用户咨询过订单" in text
    assert "用户查询订单状态" in text
    assert len(text) <= 200
    assert store.context("USER002") == ""


def test_record_turn_writes_explicit_fact_summary_and_episode(store):
    result = SimpleNamespace(
        stop_reason="answered",
        trace=[SimpleNamespace(type="tool_result", label="get_order")],
    )
    stats = store.record_turn("USER001", "s1", "t1", "记住我偏好入耳式", result)
    assert stats == {"facts": 1, "episodes": 1, "summary": 1}
    assert [fact.text for fact in store.list_facts("USER001")] == ["我偏好入耳式"]
    assert "记住我偏好入耳式" in store.get_summary("s1", "USER001")
    assert len(store.list_episodes("USER001")) == 1


def test_record_running_turn_does_not_write(store):
    result = SimpleNamespace(stop_reason="running", trace=[])
    assert store.record_turn("USER001", "s1", "t1", "记住我偏好入耳式", result) == {
        "facts": 0,
        "episodes": 0,
        "summary": 0,
    }
    assert store.list_facts("USER001") == []


def test_summary_and_episode_do_not_store_obvious_pii(store):
    result = SimpleNamespace(stop_reason="answered", trace=[])
    store.record_turn("USER001", "s1", "t1", "我的手机号是13800138000", result)
    summary = store.get_summary("s1", "USER001")
    assert "13800138000" not in summary
    assert "未记录" in summary


def test_raw_sql_cannot_read_another_users_facts_through_store_api(store):
    store.add_fact("USER001", "只属于USER001")
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute("SELECT text FROM memory_facts WHERE user_id='USER002'").fetchall()
    assert rows == []
