"""Real isolated tools/SQLite and hostile scorer controls; never a model network."""

from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import sqlite3
from contextlib import suppress
from pathlib import Path

import pytest

from acs import multiturn_evaluation as m8
from acs.commerce_benchmark import (
    audit_fixture,
    load_dataset,
    materialize,
    no_network,
    retrieval_controls,
    run_agent_case,
    run_rag,
    score_receipts,
    score_retrieval,
)
from acs.commerce_benchmark_data import CLOCK, VERSION, build_dataset, file_hash
from acs.config import Settings
from acs.local_return_tools import FORMAL_FIXTURE_KIND, fixture_settings, formal_barriers
from acs.rag import KnowledgeBase


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    root = tmp_path_factory.mktemp("commerce-suite") / "v1"
    build_dataset(root)
    return load_dataset(root)


def entry(suite, task_id):
    return next(e for e in suite["entries"] if e["task"]["task_id"] == task_id)


def test_dataset_regeneration_byte_identical_and_seeded_background_differs(tmp_path, suite):
    manifest = build_dataset(tmp_path / "same")
    assert manifest == suite["manifest"]
    other = build_dataset(tmp_path / "other", seed=20261027)
    assert other["files"]["business.json"] != manifest["files"]["business.json"]
    before = file_hash(tmp_path / "same/manifest.json")
    with pytest.raises(FileExistsError):
        build_dataset(tmp_path / "same")
    assert before == file_hash(tmp_path / "same/manifest.json")
    legacy = build_dataset(tmp_path / "legacy", revision=VERSION)
    frozen = json.loads(Path("data/eval/commerce_benchmark_v1/manifest.json").read_text())
    assert legacy == frozen  # The correction must not silently rewrite the first run.
    changed = {name for name in frozen["files"] if frozen["files"][name] != manifest["files"][name]}
    assert changed == {"README.md", "agent_tasks.json", "rag_cases.json"}


def test_new_tasks_multiturn_personas_gold_and_no_historical_sequence_copy(suite):
    assert len(suite["tasks"]) == 14
    assert len(suite["rag"]["cases"]) == 36
    assert sum(t.split == "development" for t in suite["tasks"]) == 4
    assert sum(c["split"] == "development" for c in suite["rag"]["cases"]) == 12
    old = json.loads(Path("data/eval/v3_multiturn_m8_v2/tasks.json").read_text())
    sequences = {tuple(s.get("text", "") for s in t["steps"]) for t in old}
    for t, e in zip(suite["tasks"], suite["entries"], strict=True):
        assert tuple(s.text for s in t.steps) not in sequences
        assert len(e["intents"]) >= 2 and e["persona"]["style"]
        assert all(not s.required_tools for s in t.steps)  # No forced reference path reward.
        for s in t.steps:
            assert not set(s.allowed_tools) & set(t.forbidden_tools)
            assert all(c.span in a.text for a in s.answers for c in a.claims)
    assert {c["answerability"] for c in suite["rag"]["cases"]} == {"supported", "unsupported"}


@pytest.mark.parametrize("kind", ["alter_task", "alter_doc", "missing", "extra"])
def test_frozen_inputs_fail_closed_on_tampering(tmp_path, suite, kind):
    root = tmp_path / "suite"
    shutil.copytree(suite["root"], root)
    if kind == "alter_task":
        (root / "agent_tasks.json").write_text("[]")
    elif kind == "alter_doc":
        (root / "docs/B101.md").write_text("different product specification")
    elif kind == "missing":
        (root / "docs/B101.md").unlink()
    else:
        (root / "docs/new.md").write_text("Unfrozen answer leak")
    with pytest.raises(ValueError, match="Frozen"):
        load_dataset(root)


def test_fixture_reproducible_clock_amounts_and_original_submission_barriers(tmp_path, suite):
    a, b = tmp_path / "a", tmp_path / "b"
    old_clock = m8.NOW
    assert materialize(a, suite) == materialize(b, suite)
    assert old_clock == m8.NOW
    assert audit_fixture(a, suite)["counts"] == {
        "users": 3,
        "products": 8,
        "orders": 18,
        "order_items": 19,
        "logistics": 13,
        "price_history": 16,
        "product_skus": 16,
        "order_item_skus": 19,
    }
    settings = fixture_settings(a, kind=FORMAL_FIXTURE_KIND)
    assert settings.reference_date.isoformat() == CLOCK[:10]
    with sqlite3.connect(a / "business.sqlite") as c:
        actual = dict(c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        assert all(actual[n] == sql for n, sql in formal_barriers().items())
        with pytest.raises(sqlite3.IntegrityError, match="trusted"):
            c.execute("UPDATE orders SET status='shipped'")
    before = file_hash(a / "business.sqlite")
    with pytest.raises(FileExistsError):
        materialize(a, suite)
    assert file_hash(a / "business.sqlite") == before


@pytest.mark.parametrize("task_id", ["CB03", "CB09", "CB10", "CB11", "CB12", "CB14"])
def test_real_native_tools_gold_recovery_and_no_prompt_gold(tmp_path, suite, task_id):
    original_build, original_clock = m8.build_fixture, m8.NOW
    docs_before = file_hash(suite["root"] / "docs/return_policy.md")
    r = run_agent_case(suite, entry(suite, task_id), tmp_path / task_id)
    assert r["error"] is None, r["error"]
    assert r["sql_correct"] and r["tool_valid"]
    assert r["raw_outcome"] == "pass"
    assert r["model_http_requests"] == 0 and r["input_tokens"] is None
    assert m8.build_fixture is original_build and original_clock == m8.NOW
    assert file_hash(suite["root"] / "docs/return_policy.md") == docs_before
    requests = (tmp_path / task_id / "model-inputs.json").read_text()
    assert '"tool_gold"' not in requests and '"fixture_index"' not in requests
    if task_id in {"CB12", "CB14"}:
        assert r["final"]["requests"] == 1
        assert r["final"]["counts"] == {
            "return_requests": 1,
            "return_confirmations": 1,
            "return_audit": 1,
            "return_reviews": 0,
        }
    if task_id == "CB14":
        assert any(s["recovery"] is True for s in r["steps"])


@pytest.mark.parametrize(
    "task_id,mutation,outcome",
    [
        ("CB03", "omit_write", "fail"),
        ("CB03", "wrong_order", "fail"),
        ("CB03", "wrong_quantity", "fail"),
        ("CB06", "unsafe_cancel", "fail"),
        ("CB07", "false_completion", "fail"),
        ("CB03", "paraphrase", "pending_review"),
    ],
)
def test_actual_tool_mutations_and_answer_controls(tmp_path, suite, task_id, mutation, outcome):
    r = run_agent_case(suite, entry(suite, task_id), tmp_path / mutation, mutation=mutation)
    assert r["raw_outcome"] == outcome
    if mutation == "paraphrase":
        assert r["sql_correct"] and r["tool_valid"]
        assert r["task_success"] is None and r["review_queue"]
    elif mutation in {"omit_write", "wrong_order", "wrong_quantity"}:
        assert not r["sql_correct"]
    elif mutation == "false_completion":
        assert r["sql_correct"] and r["tool_valid"]
    else:
        assert r["sql_correct"] and not r["tool_valid"]


def test_real_rag_pipeline_and_frozen_source_gold(suite, tmp_path, monkeypatch):
    absent = tmp_path / "never-use-demo.sqlite"
    monkeypatch.setenv("ACS_DB_PATH", str(absent))
    r = run_rag(suite)
    assert not absent.exists()
    assert r["summary"]["questions"] == 36 and r["summary"]["errors"] == 0
    assert r["summary"]["supported"] == 32 and r["summary"]["unsupported"] == 4
    assert r["summary"]["generated_answers"] == "not_run"
    assert any(row["grade"]["outcome"] == "pass" for row in r["rows"])
    for row in r["rows"]:
        assert row["context"] == row["candidates"][:3]
        assert any(e["type"] == "retrieval" for e in row["tool_events"])
        assert row["candidate_metrics"]["recall"] is None or (
            row["candidate_metrics"]["recall"] >= row["grade"]["evidence"]["recall"]
        )
    assert all(c["detected"] for c in retrieval_controls(suite))


@pytest.mark.parametrize(
    "groups,ids,error,expected",
    [
        ([["p#1"], ["q#2", "q#3"]], ["p#1", "q#2"], None, "pass"),
        ([["p#1"], ["q#2"]], ["p#1"], None, "fail"),
        ([["p#1"]], ["q#2"], None, "fail"),
        ([], [], None, "pass"),
        ([], ["q#2"], None, "fail"),
        ([], [], "DB unavailable", "execution_error"),
        ([["p#1"]], ["unknown#8"], None, "execution_error"),
    ],
)
def test_retrieval_grader_missing_wrong_evidence_and_failure_not_free(groups, ids, error, expected):
    r = score_retrieval(dict(evidence_groups=groups), ids, {"p#1", "q#2", "q#3"}, error)
    assert r["outcome"] == expected


def test_expected_business_refusal_not_confused_with_successful_tool(suite):
    e = entry(suite, "CB09")
    step = m8.Task.model_validate(e["task"]).steps[0]
    call = dict(name="get_order", arguments=json.dumps(dict(order_id="O61007")))
    receipt = call | dict(
        call_id="r1", error=None, result=json.dumps(dict(found=False, error_type="forbidden"))
    )
    assert score_receipts(step, e["tool_gold"][0], [dict(name="get_order")], [receipt])[0]
    forged = copy.deepcopy(receipt)
    forged["result"] = json.dumps(dict(found=True, user_id="USER002"))
    assert not score_receipts(step, e["tool_gold"][0], [dict(name="get_order")], [forged])[0]


def test_safe_alternative_price_read_not_mislabeled_as_tool_violation(suite):
    e = entry(suite, "CB01")
    step = m8.Task.model_validate(e["task"]).steps[0]
    receipt = dict(
        name="get_price_history",
        arguments=json.dumps(dict(product_id="B103")),
        call_id="alternative-price",
        error=None,
        result=json.dumps(dict(product_id="B103", history=[dict(price=259)])),
    )
    valid, coverage = score_receipts(
        step, e["tool_gold"][0], [dict(name="get_price_history")], [receipt]
    )
    assert valid and coverage == 0  # Correct non-reference plan, not a required trajectory.


def test_policy_table_is_a_valid_alternative_to_prose(suite):
    case = next(c for c in suite["rag"]["cases"] if c["id"] == "RQ25")
    chunks = KnowledgeBase(
        Settings(_env_file=None, docs_dir=suite["root"] / "docs")
    ).load_documents()
    table = next(
        c.chunk_id
        for c in chunks
        if c.document_id == "return_policy"
        and "| 七天无理由退货 | 7 天 | 签收日 | 买家 |" in c.content
    )
    assert score_retrieval(case, [table], {c.chunk_id for c in chunks})["outcome"] == "pass"


def test_no_network_even_if_inner_runtime_swallows_failure():
    before = os.environ.get("ACS_TOP_K")
    with (
        pytest.raises(RuntimeError, match="Unexpected network attempt"),
        no_network(),
        socket.socket() as s,
        suppress(RuntimeError),
    ):
        s.connect(("127.0.0.1", 9))
    assert os.environ.get("ACS_TOP_K") == before
