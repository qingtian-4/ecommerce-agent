"""A conversation uses one session, scores every turn and accounts for all requests."""

import json

import pytest
import yaml

from acs import eval_runner as runner


@pytest.mark.parametrize("first_answer,success", [("甲", True), ("错误", False)])
def test_every_turn_must_pass_and_each_attempt_gets_a_fresh_session(
    tmp_path, settings, monkeypatch, first_answer, success,
):
    dataset = tmp_path / "turns.yaml"
    dataset.write_text(yaml.safe_dump({"cases": [{
        "id": "multi", "category": "multi_turn", "user": "USER003", "query": "第一问",
        "turns": [
            {"query": "第一问", "required_tools": [], "must_contain": ["甲"]},
            {"query": "下一问", "required_tools": [], "must_contain": ["乙"]},
        ],
    }]}))
    sessions = []

    class Agent:
        def __init__(self, **kwargs):
            assert kwargs["current_user_id"] == "USER003"
            self.questions = []
            sessions.append(self)

        def chat(self, query):
            self.questions.append(query)
            return runner.AgentResult(
                answer=first_answer if len(self.questions) == 1 else "乙",
                totals={"calls": 1, "total_tokens": 20, "latency_ms": 10, "cost_cny": "0.01"},
            )

    monkeypatch.setattr(runner, "CustomerServiceAgent", Agent)
    summary = runner.run(passes=2, settings=settings, cases_path=dataset, out_dir=tmp_path / "out")
    assert len(sessions) == 2
    assert all(s.questions == ["第一问", "下一问"] for s in sessions)
    assert summary["success_rate"] == float(success)
    assert summary["api_calls"] == 4
    assert summary["cost_cny"] == "0.04"
    assert summary["multi_turn"]["conversation_success"]["passed"] == (2 if success else 0)
    assert summary["multi_turn"]["turn_success"]["passed"] == (4 if success else 2)
    row = json.loads((tmp_path / "out/per_case.jsonl").read_text().splitlines()[0])
    assert row["total_tokens"] == 40
    assert len(row["turns"]) == 2
    if not success:
        assert row["turns"][1]["score"]["success"]
        assert row["score"]["failure_reasons"][0].startswith("turn_1:")


def test_unknown_answer_does_not_hide_irrelevant_knowledge_hits():
    case = {"required_tools": ["search_knowledge"], "expected_knowledge_found": False}
    observed = runner.Observed(tool_sequence=["search_knowledge"], knowledge_found=[True])
    assert not runner.score_case(case, observed).knowledge_rejection_ok
    observed.knowledge_found = [False]
    assert runner.score_case(case, observed).success
