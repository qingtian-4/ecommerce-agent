import json
from datetime import datetime

import pytest
from scripts.generate_demo_candidates import generate


def test_candidate_workflow_is_reproducible_and_not_an_active_policy(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    data = generate(first)
    generate(second)
    assert (first / "candidates.json").read_bytes() == (second / "candidates.json").read_bytes()
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    assert data["policy"]["application_enabled"] is False
    assert data["policy"]["status"] == "draft_not_active"
    assert len(data["logistics"]) == 240
    routes, intervals = set(), set()
    for order in data["logistics"]:
        if not order["trace"]:
            continue
        routes.add(order["route_id"])
        times = [datetime.fromisoformat(e["time"]) for e in order["trace"]]
        assert times == sorted(set(times))
        assert times[-1] <= datetime.fromisoformat(data["logical_snapshot"])
        assert all(t.utcoffset().total_seconds() == 8*3600 for t in times)
        intervals.add(tuple((b-a).total_seconds() for a, b in zip(times, times[1:], strict=False)))
    assert len(routes) == 4 and len(intervals) > 50
    assert json.loads((first / "manifest.json").read_text())["paid_requests"] == 0
    with pytest.raises(FileExistsError):
        generate(first)
