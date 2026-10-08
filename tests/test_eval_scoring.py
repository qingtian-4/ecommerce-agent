"""Reject incorrect conclusions even when tool calls look correct."""

from pathlib import Path

import pytest
import yaml

from acs.eval_runner import Observed, score_case

CASES = {
    case["id"]: case
    for case in yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "data/eval/cases.yaml").read_text()
    )["cases"]
}

ANSWER_PAIRS = [
    ("o-pending", "订单已发货。", "订单待发货，尚未发出。"),
    ("o-delivered", "订单还没收到。", "您的订单已经签收。"),
    ("o-delivered-outside-return", "我不知道签收时间。", "订单于58天前签收。"),
    ("c-return-multi-item", "充电宝不能退，完全没有退货服务。",
     "充电宝可以申请7天无理由退货，需要不影响二次销售。"),
    ("c-price-protection-not-dropped", "充电器已降价，可以退差价。",
     "现价129元，没有降价，暂无差价可退。"),
    ("pp-cancelled-order-excluded", "可以申请价格保护退差价。",
     "订单已取消，不适用价保。"),
    ("pp-no-difference", "价格一样，没有买贵。", "购买519元，现价469元，相差50元。"),
    ("o-missing-id", "好的，已替你查好。", "请提供订单号，我帮您查询。"),
    ("s-cross-user-order", "好的，我来查。", "只能查询您本人的订单。"),
    ("s-nonexistent-order", "好的，查到了。", "系统没有查到订单，请核对订单号。"),
    ("p-return-window", "没有7天退货政策。", "签收后7天内可申请无理由退货。"),
    ("p-return-window", "期限是17天。", "退货期限为7天。"),
]


def observed_for(case, answer):
    return Observed(
        answer=answer,
        tool_sequence=list(case["required_tools"]),
        tool_args={name: [args] for name, args in case.get("required_tool_args", {}).items()},
        retrieved_document_ids=list(case.get("required_documents", [])),
        stop_reason="answered",
    )


@pytest.mark.parametrize("case_id,bad,good", ANSWER_PAIRS)
def test_wrong_answer_fails_and_correct_answer_passes(case_id, bad, good):
    case = CASES[case_id]
    assert not score_case(case, observed_for(case, bad)).success
    assert score_case(case, observed_for(case, good)).strict_success


def test_duplicate_required_tool_is_not_an_exact_match():
    case = CASES["p-return-window"]
    observed = observed_for(case, "退货期限为7天。")
    observed.tool_sequence.append("search_knowledge")
    score = score_case(case, observed)
    assert score.success
    assert not score.tools_exact
    assert not score.strict_success


def test_policy_case_rejects_unnecessary_business_tools():
    case = CASES["p-return-window"]
    observed = observed_for(case, "退货期限为7天。")
    observed.tool_sequence.append("get_order")
    assert not score_case(case, observed).no_forbidden_tools


def test_undeclared_forbidden_dimension_is_not_counted_as_passed():
    case = CASES["o-amount"]
    score = score_case(case, observed_for(case, "实付728元。"))
    assert score.no_forbidden_tools is None


def test_evaluation_does_not_overwrite_an_existing_result(tmp_path, settings):
    from acs.eval_runner import run

    summary = tmp_path / "summary.json"
    summary.write_text('{"frozen": true}')
    with pytest.raises(FileExistsError):
        run(case_id="f-chitchat", passes=1, settings=settings, out_dir=tmp_path)
    assert summary.read_text() == '{"frozen": true}'


def test_evaluation_records_failed_agent_initialisation(tmp_path, settings, monkeypatch):
    import acs.eval_runner as runner

    def fail(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(runner, "CustomerServiceAgent", fail)
    summary = runner.run(case_id="f-chitchat", passes=1, settings=settings, out_dir=tmp_path)
    assert summary["attempts"] == 1
    assert summary["success_rate"] == 0
    assert summary["run"]["scoring_version"] == 2
