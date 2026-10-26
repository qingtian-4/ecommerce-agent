"""Explicit interactive grants must not weaken frozen benchmark budgets."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from acs.episode_budget import EpisodeLedger
from acs.interactive_demo import EPISODE, InteractiveLedger, interactive_app
from acs.local_submission import init_formal_fixture
from acs.runtime import BudgetExceeded

from .test_web_service import ORIGIN, browser

BODY = b'{"model":"deepseek-flash","max_tokens":1024,"content":"' + b"x" * 12000 + b'"}'
USAGE = {"prompt_tokens": 100, "completion_tokens": 5}
EXPIRY = datetime(2030, 1, 1, tzinfo=UTC).isoformat()


def test_interactive_accounting_allows_more_than_old_request_and_money_limits(tmp_path):
    ledger = InteractiveLedger(tmp_path / "fees", expires_at=EXPIRY)
    for _ in range(55):
        r = ledger.reserve(EPISODE, "agent", BODY)
        ledger.finish(r, status="completed", usage=USAGE)
    assert ledger.summary()["requests"] == 55
    assert float(ledger.summary()["reserved_cny"]) > 3
    assert ledger.summary()["cost_complete"]
    assert ledger.rows()[0]["max_requests"] is None
    assert InteractiveLedger(tmp_path / "fees", expires_at=EXPIRY).summary()["requests"] == 55


def test_benchmark_constructor_keeps_its_hard_caps(tmp_path):
    with pytest.raises(ValueError, match="48"):
        EpisodeLedger(tmp_path / "benchmark", max_requests=55)
    with pytest.raises(ValueError, match="5"):
        EpisodeLedger(tmp_path / "benchmark", cap_cny=6)


def test_interactive_cannot_reinterpret_benchmark_or_reset_expiry(tmp_path):
    EpisodeLedger(tmp_path / "benchmark", cap_cny=3, max_requests=24)
    with pytest.raises(ValueError, match="capped experiment"):
        InteractiveLedger(tmp_path / "benchmark", expires_at=EXPIRY)
    InteractiveLedger(tmp_path / "ui", expires_at=EXPIRY)
    with pytest.raises(ValueError, match="grant differs"):
        InteractiveLedger(tmp_path / "ui", expires_at="2031-01-01T00:00:00+00:00")


def test_missing_usage_still_stops_and_is_not_free(tmp_path):
    ledger = InteractiveLedger(tmp_path / "fees", expires_at=EXPIRY)
    r = ledger.reserve(EPISODE, "agent", BODY)
    with pytest.raises(BudgetExceeded, match="usage_missing"):
        ledger.finish(r, status="completed", usage=None)
    assert ledger.summary()["unknown_requests"] == 1
    with pytest.raises(BudgetExceeded, match="usage_missing"):
        ledger.reserve(EPISODE, "agent", BODY)


def test_interactive_expiry_is_persistent_and_model_timeout_is_60(tmp_path):
    now = [100.0]
    end = datetime.fromtimestamp(200, UTC).isoformat()
    ledger = InteractiveLedger(tmp_path / "fees", expires_at=end, clock=lambda: now[0])
    assert ledger.remaining(EPISODE) == 60
    now[0] = 195
    assert ledger.remaining(EPISODE) == 5
    now[0] = 201
    with pytest.raises(BudgetExceeded):
        ledger.reserve(EPISODE, "agent", BODY)


def test_live_profile_metadata_is_truthful_and_business_routes_stay_owned(tmp_path):
    root = tmp_path / "ui"
    init_formal_fixture(root)
    with TestClient(interactive_app(root, lambda: None, origin=ORIGIN), base_url=ORIGIN) as client:
        sid, headers = browser(client)
        runtime = client.get("/api/bootstrap").json()["runtime"]
        assert runtime["mode"] == "deepseek" and runtime["answer_streaming"] is True
        assert runtime["cumulative_request_limit"] is None
        assert "3元/24" not in runtime["notice"]
        assert "不设累计" in runtime["notice"]
        assert client.get(f"/api/sessions/{sid}").status_code == 200
        assert (
            client.post(
                f"/api/sessions/{sid}/messages",
                json={"text": "x", "request_key": "valid-body-without-csrf"},
            ).status_code
            == 403
        )
        assert client.get("/api/sessions/foreign").status_code == 403
