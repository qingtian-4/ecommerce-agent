"""Incremental allowance uses the original journal/deadline and real SDK HTTP boundary."""

import httpx
import pytest
from openai import APIConnectionError

from acs.episode_budget import EpisodeLedger, EpisodeLimits
from acs.runtime import BudgetExceeded

from .test_episode_streaming import USAGE, client_for, frame, request


class Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield frame("ok", finish="stop") + frame(usage=USAGE) + b"data: [DONE]\n\n"


def test_shared_ledger_window_blocks_http_and_cannot_be_reset_on_restart(tmp_path):
    client, ledger, sent = client_for(tmp_path, Body, limit=3)
    with client:
        list(request(client))
        window = ledger.restrict_window("phase", added_cny=2, added_requests=1)
        assert window["baseline_requests"] == 1
        list(request(client))
        with pytest.raises((BudgetExceeded, APIConnectionError)):
            list(request(client))
    assert len(sent) == 2 and ledger.summary()["requests"] == 2
    assert ledger.summary()["stop_reasons"] == ["phase_request_limit"]
    again, reopened, more = client_for(tmp_path, Body, limit=3)
    assert reopened.restrict_window("phase", added_cny=2, added_requests=1) == window
    with again, pytest.raises((BudgetExceeded, APIConnectionError)):
        list(request(again))
    assert more == []
    with pytest.raises(ValueError, match="Cannot reset"):
        reopened.restrict_window("phase", added_cny=2, added_requests=2)


def test_incremental_cost_ceiling_is_checked_before_http(tmp_path):
    client, ledger, sent = client_for(tmp_path, Body)
    ledger.restrict_window("cost", added_cny=0.000001, added_requests=2)
    with client, pytest.raises((BudgetExceeded, APIConnectionError)):
        list(request(client))
    assert sent == [] and ledger.summary()["requests"] == 0
    assert ledger.summary()["stop_reasons"] == ["phase_cost_limit"]


def test_restriction_does_not_extend_original_episode_deadline(tmp_path):
    now = [0.0]
    ledger = EpisodeLedger(
        tmp_path / "ledger.jsonl", cap_cny=3, max_requests=24, clock=lambda: now[0]
    )
    limits = EpisodeLimits(episode_timeout_s=10)
    ledger.begin("original", limits)
    ledger.restrict_window("phase", added_cny=2, added_requests=18)
    now[0] = 11
    ledger.begin("original", limits)
    with pytest.raises(BudgetExceeded, match="episode_deadline"):
        ledger.remaining("original")
