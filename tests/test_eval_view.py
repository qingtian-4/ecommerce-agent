"""The progress page must use matched evidence and a common scoring version."""

import json
from collections import Counter

import pytest

from acs.eval_view import RESULTS_PATH, case_rows, load_evaluation, replay_case


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
    (tmp_path / "comparison.json").write_bytes((RESULTS_PATH / "comparison.json").read_bytes())
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
