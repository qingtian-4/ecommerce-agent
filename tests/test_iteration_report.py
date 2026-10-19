"""An incomplete or edited run must never become a completed comparison."""

import json

import pytest
from scripts.report_iteration import compare, read_records

from acs.scenarios import sha256


@pytest.mark.parametrize("trials", [[1], [1, 1]])
def test_report_rejects_missing_or_duplicate_trial(tmp_path, trials):
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(json.dumps({"id": "x", "trial": t}) for t in trials))
    (tmp_path / "summary.json").write_text(json.dumps({"raw_sha256": sha256(path)}))
    (tmp_path / "frozen_manifest.json").write_text(json.dumps({
        "planned": {"x": {"trials": 2}},
    }))
    with pytest.raises(ValueError, match="Incomplete or duplicate"):
        read_records(tmp_path)


def test_report_rejects_changed_raw_and_mismatched_pairs(tmp_path):
    path = tmp_path / "raw.jsonl"
    path.write_text('{"id":"x","trial":1}\n')
    (tmp_path / "summary.json").write_text(json.dumps({"raw_sha256": sha256(path)}))
    (tmp_path / "frozen_manifest.json").write_text('{"planned":{"x":{"trials":1}}}')
    assert len(read_records(tmp_path)[0]) == 1
    path.write_text('[]')
    with pytest.raises(ValueError, match="hash mismatch"):
        read_records(tmp_path)
    with pytest.raises(ValueError, match="sets differ"):
        compare([{"id": "x", "trial": 1}], [{"id": "y", "trial": 1}])


def test_partial_report_keeps_balance_failures_and_unfinished_tasks(tmp_path):
    from scripts.report_iteration import write_partial

    names = ["local_before", "local_after", "contrast_before_development",
             "contrast_after_development", "contrast_before_holdout", "contrast_after_holdout",
             "tau_base"]
    (tmp_path / "interruption.json").write_text(json.dumps({"runs": dict.fromkeys(names, {})}))
    for name in names:
        directory = tmp_path / name
        directory.mkdir()
        row = {"id": "x", "trial": 1, "passed": True, "checks": {"state": True},
               "failures": []}
        manifest = {"planned": {"x": {"trials": 1}}}
        if name == "tau_base":
            manifest = {"task_ids": ["x"], "trials": 1, "arms": ["acs_loop", "llm_agent"]}
            row |= {"arm": "acs_loop", "error": "Insufficient Balance", "reward": 0}
        raw = directory / "raw.jsonl"
        raw.write_text(json.dumps(row) + "\n")
        (directory / "frozen_manifest.json").write_text(json.dumps(manifest))
        (directory / "summary.json").write_text(json.dumps({"raw_sha256": sha256(raw)}))
    output = tmp_path / "report"
    write_partial(tmp_path, output)
    report = json.loads((output / "summary.json").read_text())
    assert report["complete"] is False
    tau = report["progress"]["tau_base"]
    assert tau["balance_failure_episodes"] == 1
    assert tau["unfinished_attempts"] == [["x", 1, "llm_agent"]]
    assert "pass^3" not in tau
