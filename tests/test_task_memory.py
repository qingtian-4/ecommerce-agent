"""Task invariants use database facts, never a live model or network."""

import json
import subprocess
import sys
from contextlib import closing

import pytest

from acs.cs_tools import ToolRegistry
from acs.database import BusinessDB
from acs.sessions import SessionStore


@pytest.fixture
def task_context(settings):
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "O10024耳机退1个，无理由，未拆封")
    order = BusinessDB(settings).get_order("O10024", "USER001")
    item = next(i for i in order["items"] if i["product_id"] == "P001")
    patch = {"order_id": "O10024", "item_id": item["id"], "quantity": 1,
             "reason": "no_reason", "condition": "sealed"}
    return store, sid, tid, patch


def test_candidate_tool_returns_only_owned_orders_and_actual_items(settings):
    registry = ToolRegistry(settings)
    result, _, error = registry.run("list_orders", "{}", current_user_id="USER001")
    assert error is None
    expected = BusinessDB(settings).list_orders("USER001")
    assert [o["order_id"] for o in result["orders"]] == [o["order_id"] for o in expected]
    assert "O10030" not in json.dumps(result)
    assert len(next(o for o in result["orders"] if o["order_id"] == "O10024")["items"]) == 2


def test_non_argument_tool_failure_keeps_exception_classification(settings, monkeypatch):
    registry = ToolRegistry(settings)

    def failed_lookup(_product_id):
        raise ValueError("storage or index fault")

    monkeypatch.setattr(registry.db, "get_product", failed_lookup)
    _, events, error = registry.run("get_product", '{"product_id":"P001"}',
                                   current_user_id="USER001")
    assert error == "tool_exception: ValueError"
    assert events[-1].detail["error_type"] == "tool_exception"


def test_task_fields_sources_and_fresh_process_recovery(task_context, settings):
    store, sid, tid, patch = task_context
    result = store.update_return_task(sid, "USER001", tid, patch)
    assert result["task"]["quantity"] == 1 and result["can_submit"] is False
    assert result["task"]["sources"]["condition"]["turn_id"] == tid
    assert result["task"]["sources"]["condition"]["user_text"] == "O10024耳机退1个，无理由，未拆封"
    assert result["task"]["sources"]["condition"]["type"] == "user_claim"
    script = ("import json,sys; from pathlib import Path; from acs.sessions import SessionStore; "
              "s=SessionStore(Path(sys.argv[1])); "
              "print(json.dumps(s.get_return_task(sys.argv[2], 'USER001')))"
    )
    child = subprocess.run([sys.executable, "-c", script, str(settings.db_path), sid],
                           check=True, capture_output=True, text=True)
    assert json.loads(child.stdout)["task"] == result["task"]
    assert store.recover_interrupted() == 1
    assert store.get_return_task(sid, "USER001")["task"] == result["task"]
    assert store.get_return_task(store.create("USER001"), "USER001")["task"] is None


def test_order_only_does_not_guess_an_item(task_context):
    store, sid, tid, _ = task_context
    result = store.update_return_task(sid, "USER001", tid, {"order_id": "O10024"})
    assert result["task"]["item_id"] is None
    assert "item_id" in result["missing_fields"] and len(result["order"]["items"]) == 2


def test_unrelated_completed_turn_keeps_task_and_known_fields(task_context):
    store, sid, tid, patch = task_context
    before = store.update_return_task(sid, "USER001", tid, patch)
    store.interrupt_turn(sid, "USER001", tid)
    for text in ("你好", "先聊点别的", "谢谢", "晚点继续", "回到退货"):
        other = store.begin_turn(sid, "USER001", text)
        store.finish_turn(sid, "USER001", other, [{"role": "user", "content": text},
                          {"role": "assistant", "content": "好的"}], {"answer": "好的"})
    assert store.get_return_task(sid, "USER001")["task"] == before["task"]
    assert store.get_return_task(sid, "USER001")["missing_fields"] == ["secondary_sale_ok"]


def test_order_correction_clears_dependent_fields(task_context):
    store, sid, tid, patch = task_context
    before = store.update_return_task(sid, "USER001", tid, patch)
    after = store.update_return_task(sid, "USER001", tid, {"order_id": "O10027"})["task"]
    assert after["revision"] == before["task"]["revision"] + 1
    assert after["order_id"] == "O10027"
    assert all(after[f] is None for f in ["item_id", "quantity", "reason", "condition"])
    assert set(after["sources"]) == {"order_id"}


def test_item_correction_clears_old_quantity_and_condition(task_context, settings):
    store, sid, tid, patch = task_context
    store.update_return_task(sid, "USER001", tid, patch)
    item = next(i for i in BusinessDB(settings).get_order("O10024", "USER001")["items"]
                if i["id"] != patch["item_id"])
    after = store.update_return_task(sid, "USER001", tid, {"item_id": item["id"]})["task"]
    assert after["quantity"] is None and after["condition"] is None
    assert after["reason"] == "no_reason"


@pytest.mark.parametrize("quantity", [0, -1, 2, 1.5, True, "1"])
def test_invalid_quantity_does_not_mutate_previous_state(task_context, quantity):
    store, sid, tid, patch = task_context
    before = store.update_return_task(sid, "USER001", tid, patch)["task"]
    with pytest.raises(ValueError, match="数量"):
        store.update_return_task(sid, "USER001", tid, {"quantity": quantity})
    assert store.get_return_task(sid, "USER001")["task"] == before


def test_cross_order_item_and_owner_are_rejected_without_mutation(task_context, settings):
    store, sid, tid, patch = task_context
    before = store.update_return_task(sid, "USER001", tid, patch)["task"]
    foreign_item = BusinessDB(settings).get_order("O10027", "USER001")["items"][0]["id"]
    for changes in [{"item_id": foreign_item}, {"order_id": "O10030"}, {"surprise": "x"}]:
        with pytest.raises(ValueError):
            store.update_return_task(sid, "USER001", tid, changes)
        assert store.get_return_task(sid, "USER001")["task"] == before
    for action in [lambda: store.get_return_task(sid, "USER002"),
                   lambda: store.update_return_task(sid, "USER002", tid, patch),
                   lambda: store.cancel_return_task(sid, "USER002", tid)]:
        with pytest.raises(PermissionError):
            action()


def test_cancel_only_clears_draft_and_retains_business_order(task_context, settings):
    store, sid, tid, patch = task_context
    db = BusinessDB(settings)
    order = db.get_order("O10024", "USER001")
    store.update_return_task(sid, "USER001", tid, patch)
    store.interrupt_turn(sid, "USER001", tid)
    tid = store.begin_turn(sid, "USER001", "中止这个退货草稿，不要取消订单")
    result = store.cancel_return_task(sid, "USER001", tid)
    assert result["task"]["status"] == "cancelled"
    assert result["task"]["order_id"] is None and result["missing_fields"] == []
    assert db.get_order("O10024", "USER001") == order


@pytest.mark.parametrize("text", ["你好，先不用继续退货。", "不要中止退货草稿", "暂停一下"])
def test_pausing_or_negating_cancel_cannot_clear_a_draft(task_context, text):
    store, sid, tid, patch = task_context
    before = store.update_return_task(sid, "USER001", tid, patch)["task"]
    store.interrupt_turn(sid, "USER001", tid)
    tid = store.begin_turn(sid, "USER001", text)
    with pytest.raises(ValueError, match="暂停话题"):
        store.cancel_return_task(sid, "USER001", tid)
    assert store.get_return_task(sid, "USER001")["task"] == before


def test_quality_claim_is_not_verified_and_evidence_stays_missing(task_context):
    store, sid, tid, patch = task_context
    result = store.update_return_task(sid, "USER001", tid, patch | {
        "reason": "quality", "problem_description": "左耳无声",
    })
    assert result["task"]["sources"]["problem_description"]["type"] == "user_claim"
    assert "quality_evidence" in result["missing_fields"] and not result["can_submit"]


def test_task_writes_require_current_running_turn(task_context):
    store, sid, tid, patch = task_context
    other_sid = store.create("USER001")
    with pytest.raises(ValueError, match="轮次"):
        store.update_return_task(other_sid, "USER001", tid, patch)
    store.interrupt_turn(sid, "USER001", tid)
    with pytest.raises(ValueError, match="轮次"):
        store.update_return_task(sid, "USER001", tid, patch)


def test_migration_preserves_v1_history_and_business_tables(task_context):
    store, sid, tid, _ = task_context
    store.interrupt_turn(sid, "USER001", tid)
    before = store.load(sid, "USER001")
    with closing(store._connect()) as conn:
        conn.execute("DROP TABLE return_tasks")
        conn.execute("DELETE FROM acs_migrations WHERE version=2")
        conn.commit()
        counts = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
    migrated = SessionStore(store.db_path)
    assert migrated.load(sid, "USER001") == before
    with closing(migrated._connect()) as conn:
        assert conn.execute("SELECT MAX(version) FROM acs_migrations").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == counts
