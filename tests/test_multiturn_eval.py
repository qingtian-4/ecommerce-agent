import json
from pathlib import Path

import pytest

from acs.scenarios import build_scenario
from acs.task_cases import build_cases, validate_cases
from acs.task_eval import answer_facts, deterministic_checks, judge_record


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    path = tmp_path_factory.mktemp("tasks") / "scenario"
    build_scenario(path)
    result = build_cases(path / "business.db")
    validate_cases(result)
    return result


def test_frozen_cases_match_current_deterministic_business_seed(cases):
    frozen = json.loads(Path("data/eval/multiturn_v1/cases.json").read_text())
    assert cases == frozen


def test_positive_submission_cannot_pass_without_a_new_request(cases):
    case = next(c for c in cases if c["id"] == "confirmation-01")
    checks = deterministic_checks(case, [], None, {}, {}, True)
    assert not checks["new_request_count"]


def test_wrong_item_quantity_and_review_status_fail(cases):
    case = next(c for c in cases if c["id"] == "review-01")
    row = {
        "user_id": case["user_id"],
        "order_id": case["reference_order"]["order_id"],
        "item_id": case["reference_order"]["item_id"],
        "quantity": 2,
        "status": "pending_review",
    }
    checks = deterministic_checks(case, [], None, {}, {"new": row}, True)
    assert not checks["request_identity"] and not checks["request_status"]


def test_old_request_mutation_and_chat_submission_are_forbidden(cases):
    case = cases[0]
    checks = deterministic_checks(
        case,
        [{"request_delta": 1}],
        None,
        {"old": {"status": "pending_review"}},
        {"old": {"status": "approved"}},
        True,
    )
    assert not checks["existing_requests_immutable"]
    assert not checks["chat_cannot_submit"]


def test_memory_oracle_rejects_wrong_quantity(cases):
    case = next(c for c in cases if c["id"] == "memory-01")
    task = case["expected"]["task"] | {"quantity": 2}
    assert not deterministic_checks(case, [], task, {}, {}, True)["task_fields"]


def test_v2_candidates_and_policy_fit_default_context(tmp_path):
    from acs.chat_service import ChatService
    from acs.config import Settings
    from acs.cs_tools import ToolRegistry
    from acs.scenarios import scenario_settings
    from acs.service_agent import CustomerServiceAgent

    from .conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall

    root = tmp_path / "snapshot"
    build_scenario(root)
    settings = scenario_settings(Settings(_env_file=None), root)
    agent = CustomerServiceAgent(
        settings=settings,
        registry=ToolRegistry(settings),
        llm=ScriptedLLM(
            [
                ScriptedResponse(
                    wants_tools=True,
                    tool_calls=[
                        ScriptedToolCall("orders", "list_orders", "{}"),
                        ScriptedToolCall(
                            "policy",
                            "get_knowledge_documents",
                            '{"document_ids":["return_policy"]}',
                        ),
                    ],
                ),
                ScriptedResponse(text="请选择具体订单"),
            ]
        ),
    )
    final = list(
        ChatService(settings).run_turn(
            "我要为S20002的演示 A1 入耳耳机申请无理由退货。",
            "USER001",
            agent=agent,
        )
    )[-1]
    assert final.result.stop_reason == "answered"
    candidates = next(
        e.detail["result"]["orders"]
        for e in final.result.trace
        if e.type == "tool_result" and e.label == "list_orders"
    )
    assert len(candidates) == 20
    assert all(order["items"] for order in candidates)


def test_answer_evidence_includes_all_items_and_actual_logistics(tmp_path, cases):
    from acs.config import Settings
    from acs.scenarios import scenario_settings

    root = tmp_path / "facts"
    build_scenario(root)
    settings = scenario_settings(Settings(_env_file=None), root)
    case = next(c for c in cases if c["id"] == "orders-03")
    facts = answer_facts(settings, case)
    assert len(facts["order"]["items"]) == 2
    assert "SYNS20017" in json.dumps(facts["logistics"])
    assert set(facts["policies"]) == {
        "return_policy.md",
        "shipping_policy.md",
        "warranty_policy.md",
        "price_protection.md",
    }


@pytest.mark.parametrize("quote,passed", [("签收7天", True), ("签收15天", False)])
def test_quote_validation_ignores_markdown_but_preserves_facts(quote, passed):
    class Client:
        usages = []

        def complete(self, payload, **kwargs):
            from types import SimpleNamespace

            self.payload = json.loads(payload)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "passed": True,
                        "failures": [],
                        "answer_quotes": [quote],
                        "repeated_questions": 0,
                    }
                )
            )

    client = Client()
    events = [
        {
            "query": "之前没有上传",
            "result": {"answer": "签收**7天**", "trace": []},
            "task": {"status": "draft"},
        },
        {"action": "upload", "output": {"saved": True}},
    ]
    result = judge_record(client, {"events": events})
    assert result["passed"] is passed
    timeline = client.payload["candidate_timeline"]
    assert timeline[0]["observed_task_after"] == {"status": "draft"}
    assert timeline[1]["trusted_operation"]["action"] == "upload"


@pytest.mark.parametrize(
    "passed,failures,quotes",
    [
        (True, [{"type": "grounding", "evidence": "wrong"}], ["实际回答"]),
        (True, [], ["引用了评分事实而非客服回答"]),
        (True, [], []),
    ],
)
def test_judge_cannot_pass_contradictory_or_unquoted_outputs(passed, failures, quotes):
    class Client:
        usages = []

        def complete(self, *args, **kwargs):
            from types import SimpleNamespace

            return SimpleNamespace(
                text=json.dumps(
                    {
                        "passed": passed,
                        "failures": failures,
                        "answer_quotes": quotes,
                        "repeated_questions": 0,
                    }
                )
            )

    result = judge_record(Client(), {"events": [{"query": "问", "result": {"answer": "实际回答"}}]})
    assert result["passed"] is False
    assert result["failures"][0]["type"] == "judge_error"
