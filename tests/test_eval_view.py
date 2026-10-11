"""The progress page must use matched evidence and a common scoring version."""

import json
from collections import Counter

import pytest

from acs.eval_view import RESULTS_PATH, case_rows, load_evaluation, replay_case, review_run_rows


def test_retrieval_replay_uses_verified_records_and_preserves_failed_baseline():
    from acs.eval_view import (
        load_retrieval_evaluation,
        retrieval_case_view,
        retrieval_comparison_rows,
    )

    report = load_retrieval_evaluation()
    rows = retrieval_comparison_rows(report)
    assert len(rows) == 6
    case = retrieval_case_view(report, "q45-quickcharge")["检索对照"]
    assert case["dense"]["metrics"]["context"]["recall"] == 0
    assert case["bm25"]["metrics"]["context"]["recall"] == 1
    assert case["hybrid"]["metrics"]["context"]["recall"] == 1


def test_retrieval_snapshot_rejects_modified_raw_evidence(tmp_path):
    import shutil

    from acs.config import PROJECT_ROOT
    from acs.eval_view import load_retrieval_evaluation

    shutil.copytree(PROJECT_ROOT / "data/docs", tmp_path / "data/docs")
    shutil.copytree(PROJECT_ROOT / "data/eval", tmp_path / "data/eval")
    shutil.copytree(PROJECT_ROOT / "results/hybrid_retrieval",
                    tmp_path / "results/hybrid_retrieval")
    path = tmp_path / "results/hybrid_retrieval/development/per_case.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="原始记录"):
        load_retrieval_evaluation(tmp_path)


def test_published_answers_are_rescored_without_changing_original_files():
    path = RESULTS_PATH / "after" / "per_case.jsonl"
    original = path.read_bytes()
    assert sum(json.loads(line)["score"]["success"] for line in original.splitlines()) == 103
    report = load_evaluation()
    assert sum(r["score"]["success"] for r in report["groups"]["after"]) == 105
    assert path.read_bytes() == original
    rows = case_rows(report)
    assert len(rows) == 35
    assert len(case_rows(report, "security")) == 4
    assert all(row[-1] == "3/3" for row in rows)
    assert Counter(r["score"]["success"] for r in report["groups"]["initial"])[False] == 11


def test_stale_case_definitions_cannot_be_presented_as_current_results(tmp_path):
    cases = tmp_path / "cases.yaml"
    cases.write_text("cases: []\n")
    with pytest.raises(ValueError, match="不一致"):
        load_evaluation(cases_path=cases)


def test_modified_source_records_are_rejected(tmp_path):
    (tmp_path / "comparison_v5.json").write_bytes(
        (RESULTS_PATH / "comparison_v5.json").read_bytes()
    )
    (tmp_path / "before").mkdir()
    (tmp_path / "before" / "per_case.jsonl").write_bytes(
        (RESULTS_PATH / "before" / "per_case.jsonl").read_bytes() + b"\n"
    )
    with pytest.raises(ValueError, match="原始记录"):
        load_evaluation(base=tmp_path)


def test_replay_preserves_failed_optimization_and_escapes_model_output():
    report = load_evaluation()
    row = next(r for r in report["groups"]["initial"]
               if r["case_id"] == "pp-no-difference" and r["attempt"] == 1)
    assert row["score"]["success"] is False
    row["answer"] = "<script>alert(1)</script>"
    rendered, evidence = replay_case(report, row["case_id"], 1)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert evidence["历史记录"]["首轮优化"]["score"]["failure_reasons"]
    assert evidence["历史记录"]["修正后"]["score"]["strict_success"] is True


def test_new_runs_cannot_be_displayed_against_different_case_inputs(tmp_path):
    import hashlib

    cases = tmp_path / "data/eval/holdout.yaml"
    cases.parent.mkdir(parents=True)
    cases.write_text("frozen")
    result = tmp_path / "results/holdout/first/summary.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({
        "cases": 15, "passes": 1, "success_rate": 11/15, "strict_success_rate": 10/15,
        "dimensions": {"no_errors": {"applicable": 15, "passed": 14}},
        "run": {"cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
                "scoring_version": 4},
    }))
    assert review_run_rows(tmp_path)[0][1:4] == [15, 1, "73.3%"]
    cases.write_text("changed")
    with pytest.raises(ValueError, match="不匹配"):
        review_run_rows(tmp_path)
