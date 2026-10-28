"""Seen real failures: lossless context, evidence coverage and explicit quota choice."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from acs.episode_budget import EpisodeLedger, EpisodeLimits
from acs.shop_mainline_data import load
from acs.shop_mainline_eval import grade


def test_context_roundtrip_reduces_repeated_business_facts_without_losing_constraints():
    from acs.shop_model_context import expand_messages, project_messages

    source = dict(user_text="未拆封，只退一件；不要改成另一订单。"*8,
                  type="user_claim", updated_at="2026-10-28T04:00:00+00:00")
    task = dict(order_id="O72001", item_id=720010, quantity=1,
                revision=2, sources={k: source for k in ["order_id", "item_id", "quantity"]})
    receipt = dict(result=dict(task=task), error=None, current_task=dict(task=task))
    messages = [dict(role="user", content="只退1件，别修改另一笔订单。"),
                dict(role="assistant", content=None, tool_calls=[dict(id="d", type="function",
                    function=dict(name="update_return_task", arguments="{}"))]),
                dict(role="tool", tool_call_id="d", content=json.dumps(receipt)),
                dict(role="system", content="当前应用核验事实："+json.dumps(dict(task=task)))]
    original = copy.deepcopy(messages)
    projected = project_messages(messages)
    assert messages == original
    expanded = expand_messages(projected)
    assert expanded[:2] == original[:2]
    assert json.loads(expanded[2]["content"]) == json.loads(original[2]["content"])
    assert json.loads(expanded[3]["content"].split('：', 1)[1]) == json.loads(
        original[3]["content"].split('：', 1)[1])
    assert len(json.dumps(projected, ensure_ascii=False)) < len(json.dumps(original,
                                                                         ensure_ascii=False))
    assert [m for m in projected if m["role"] == "user"] == [messages[0]]
    assert projected[2]["tool_call_id"] == "d"


def test_context_does_not_interpret_existing_reference_keys_as_authority():
    from acs.shop_model_context import expand_messages, project_messages

    messages = [dict(role="tool", tool_call_id="x", content=json.dumps(
        dict(result={"$context_ref": "v0"})))]
    assert expand_messages(project_messages(messages)) == messages


def test_missing_second_turn_goals_are_visible_without_certifying_language():
    entry = next(e for e in load()["entries"] if e["task"]["task_id"] == "SX04")
    result = dict(steps=[], raw_outcome="execution_error", display_outcome="execution_error")
    score = grade(entry, result)
    assert len(score["objectives"]) == sum(len(g["goals"]) for g in entry["intent_gold"])
    assert all(g["raw"] == "not_executed" and not g["evidence"] for g in score["objectives"])
    assert score["raw_outcome"] == "execution_error"


def test_policy_history_is_valid_only_with_the_current_document_fingerprint():
    from acs.shop_mainline_eval import evidence_present

    receipt = dict(name="search_knowledge", error=None, arguments="{}", result=json.dumps(dict(
        source_fingerprints={"appliance_consultation": "current"}, documents=[dict(
            document_id="appliance_consultation", content="模拟无理由退货期限：签收日起7天")])))
    record = dict(state=dict(receipts=[receipt], consultation=dict(document_sha256="current")))
    goal = dict(evidence=dict(kind="policy", document_id="appliance_consultation",
                             contains="模拟无理由退货期限：签收日起7天"))
    assert evidence_present(goal, record, [], {})
    record["state"]["consultation"]["document_sha256"] = "changed"
    assert not evidence_present(goal, record, [], {})


def test_uncapped_accounting_is_explicit_and_cannot_reopen_a_limited_ledger(tmp_path):
    path = tmp_path / "ledger.jsonl"
    EpisodeLedger(path)
    with pytest.raises(ValueError):
        EpisodeLedger(path, cap_cny=None, max_requests=None,
                      uncapped_authorization="current human request")
    with pytest.raises(ValueError):
        EpisodeLedger(tmp_path / "unauthorized", cap_cny=None, max_requests=None)
    ledger = EpisodeLedger(tmp_path / "uncapped", cap_cny=None, max_requests=None,
                          uncapped_authorization="current human request")
    ledger.begin("e", EpisodeLimits(requests=60, steps=100))
    for _ in range(50):
        reservation = ledger.reserve("e", "agent", json.dumps(dict(
            model="deepseek-flash", max_tokens=1024, padding="x"*32000)).encode())
        ledger.finish(reservation, status="completed", usage=dict(prompt_tokens=10,
                                                                 completion_tokens=1))
    assert ledger.summary("e")["requests"] == 50
    assert ledger.summary("e")["cost_complete"]
    assert Decimal(ledger.summary("e")["reserved_cny"]) > 5


def test_real_failure_projection_is_byte_safe_if_private_trace_is_available():
    from acs.shop_model_context import project_messages

    root = Path("results/shop_mainline_live_20261028_user/.private-evidence")
    if not root.exists():
        return  # Public CI has the synthetic round-trip control above, never needs private traces.
    for arm in ("old", "v3"):
        data = json.loads((root / f"scoped-SX04-{arm}/model-inputs.json").read_text())[-1]
        projected = data | {"messages": project_messages(data["messages"])}
        assert len(json.dumps(projected, ensure_ascii=False,
                              separators=(",", ":")).encode())+256 < 32768


def test_rehearsal_http_usage_is_per_episode_and_aggregates_once(tmp_path, monkeypatch):
    from argparse import Namespace

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import eval_shop_mainline as pilot

    report = pilot.execute(Namespace(mode="rehearsal", out=tmp_path / "trial",
        model_context=True, env_file=None, price_review=None))
    assert report["paid_requests"] == 0
    assert report["ledger_total"]["requests"] == sum(
        v["requests"] for v in report["ledgers"].values()) == 24
    assert len(report["cases"]) == 4
    for case in report["cases"]:
        key = 'scoped:'+case['task_id']+':'+case['arm']
        ledger = report["ledgers"][key]
        assert ledger["requests"] == 6 and ledger["roles"]["agent"]["prompt_tokens"] == 600
        result = json.loads((tmp_path / "trial/.private-evidence" / key.replace(':','-') /
                             "result.json").read_text())
        assert result["model_http_requests"] == 6 and result["input_tokens"] == 600
