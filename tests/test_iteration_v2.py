"""Regression checks for paired suites and their evaluation evidence."""

from copy import deepcopy

import pytest

from acs.contrast_cases import build_contrast_cases, validate_contrast_cases
from acs.scenarios import build_scenario, scenario_settings
from acs.task_eval import answer_facts


@pytest.fixture(scope="module")
def contrast(tmp_path_factory):
    root = tmp_path_factory.mktemp("contrast") / "scenario"
    build_scenario(root)
    return root, build_contrast_cases(root / "business.db")


def test_pairs_cannot_leak_across_splits(contrast):
    _, cases = contrast
    validate_contrast_cases(cases)
    broken = deepcopy(cases)
    broken[0]["source_cluster"] = broken[2]["source_cluster"]
    with pytest.raises(AssertionError):
        validate_contrast_cases(broken)


def test_judge_has_both_orders_for_an_interruption(contrast, settings):
    root, cases = contrast
    case = next(c for c in cases if c["category"] == "interruption")
    facts = answer_facts(scenario_settings(settings, root), case)
    assert set(facts["referenced_orders"]) == set(case["reference_orders"])
    assert all(o["order"]["found"] for o in facts["referenced_orders"].values())


def test_summary_uses_planned_counts_and_repeats():
    from scripts.eval_multiturn import summarise

    planned = {"a": {"split": "development", "trials": 3}}
    row = {"id": "a", "split": "development", "category": "negation",
           "passed": True, "events": [], "answer_review": {"usage": []}, "failures": []}
    rows = [row | {"trial": n} for n in (1, 2, 3)]
    assert summarise(rows, planned)["splits"]["development"]["all_trials_success"] == 1
    assert summarise(rows, planned)["splits"]["development"]["complete"]
    assert not summarise(rows[:2], planned)["splits"]["development"]["complete"]
    assert not summarise([rows[0]] * 3, planned)["splits"]["development"]["complete"]


def test_requests_do_not_attribute_an_old_application_to_this_session(settings):
    from acs.cs_tools import ToolRegistry
    from acs.database import BusinessDB
    from acs.returns import ReturnService
    from acs.sessions import SessionStore

    store = SessionStore(settings.db_path)
    service = ReturnService(settings, store)
    sid = store.create("USER001")
    tid = store.begin_turn(sid, "USER001", "退货1件，声明符合全部二次销售条件")
    order = BusinessDB(settings).get_order("O10024", "USER001")
    store.update_return_task(sid, "USER001", tid, {
        "order_id": "O10024", "item_id": order["items"][0]["id"],
        "quantity": 1, "reason": "no_reason", "condition": "sealed", "secondary_sale_ok": True,
    })
    preview = service.preview(sid, "USER001", tid)
    request = service.confirm_and_submit(sid, "USER001", preview["preview_id"], tid)
    registry = ToolRegistry(settings)
    registry.bind_return_task(store, sid, tid)
    same, _, _ = registry.run("get_return_requests", "{}", current_user_id="USER001")
    assert same["current_session_request_id"] == request["request_id"]
    other = store.create("USER001")
    other_tid = store.begin_turn(other, "USER001", "[操作] 点击确认提交当前申请")
    registry.bind_return_task(store, other, other_tid)
    result, _, _ = registry.run("get_return_requests", "{}", current_user_id="USER001")
    assert result["requests"][0]["request_id"] == request["request_id"]
    assert result["current_session_request_id"] is None


def test_prompt_has_effective_business_date(settings):
    from datetime import date

    from acs.service_agent import build_system_prompt

    prompt = build_system_prompt(settings.model_copy(update={"reference_date": date(2026, 10, 15)}))
    assert "当前业务日期：2026-10-15" in prompt


def test_tau_selection_and_incomplete_trials():
    from types import SimpleNamespace

    from scripts.eval_tau_retail import select_tasks, summarise

    tasks = [SimpleNamespace(id=str(n)) for n in (10, 2, 0, 1, 3, 4)]
    assert [t.id for t in select_tasks(tasks, "smoke")] == ["0", "1", "2", "3", "4"]
    assert len(select_tasks(tasks, "base")) == 6
    rows = [{"arm": "acs_loop", "id": "0", "trial": t, "reward": 1} for t in (1, 2, 3)]
    assert summarise(rows, ["0"], 3)["acs_loop"]["pass^3"] == 1
    assert summarise(rows[:2], ["0"], 3)["acs_loop"]["pass^3"] is None
    assert not summarise([rows[0]] * 3, ["0"], 3)["acs_loop"]["complete"]
