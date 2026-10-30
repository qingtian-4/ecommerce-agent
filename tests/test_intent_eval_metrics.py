"""Optional-aware exact scoring must not count a valid optional route as a failure."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from eval_intent_planner import _metrics  # noqa: E402


def test_optional_route_counts_as_acceptable_but_not_strict():
    rows = [
        {
            "required": ["policy"],
            "optional": ["product"],
            "planner": ["product", "policy"],
            "rule": ["policy"],
            "planned_clarify": False,
            "clarify": False,
        },
        {
            "required": ["policy"],
            "optional": [],
            "planner": ["orders", "policy"],
            "rule": ["policy"],
            "planned_clarify": False,
            "clarify": False,
        },
    ]
    metrics = _metrics(rows, "planner")

    assert metrics["exact_match"] == 0
    assert metrics["acceptable_match"] == 1
    assert metrics["coverage"] == 2
    assert metrics["optional_predictions"] == 1
    assert metrics["unnecessary_labels"] == 1
    assert metrics["extra_labels"] == 2
