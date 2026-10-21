"""A provider request must be denied before spending beyond the experimental cap."""

import socket
from decimal import Decimal
from types import SimpleNamespace

import pytest
from openai.types import CompletionUsage
from scripts.eval_p1_live import Ledger, MeteredLLM

from acs.config import Settings
from acs.llm import LLMClient, LLMResponse, Usage
from acs.runtime import BudgetExceeded


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Budget tests must not access a provider")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def test_money_limit_denies_before_recording_a_request(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", limit=Decimal("0.000001"))
    with pytest.raises(BudgetExceeded):
        ledger.begin({"messages": [], "max_tokens": 1024})
    assert not ledger.calls and ledger.spent == 0


def test_known_usage_releases_reservation_and_request_limit_stops_next(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", max_requests=1)
    record = ledger.begin({"messages": [], "max_tokens": 1024})
    ledger.complete(record, Usage(model="deepseek-flash", stage="test", prompt_tokens=10,
                                  completion_tokens=5, total_tokens=15))
    assert ledger.spent == Decimal("0.000080")
    with pytest.raises(BudgetExceeded):
        ledger.begin({"messages": [], "max_tokens": 1024})
    assert len(ledger.calls) == 1


@pytest.mark.parametrize("usage", [None, Usage(model="deepseek-flash", stage="test"),
                                   Usage(model="deepseek-flash", stage="test", usage_missing=True)])
def test_missing_or_invalid_usage_charges_full_reserve_and_halts(tmp_path, usage):
    ledger = Ledger(tmp_path / "ledger.json")
    record = ledger.begin({"messages": [], "max_tokens": 1024})
    ledger.complete(record, usage)
    assert ledger.spent == Decimal(record["reserved_cny"])
    with pytest.raises(BudgetExceeded):
        ledger.begin({"messages": [], "max_tokens": 1024})
    assert len(ledger.calls) == 1


def test_stream_stage_is_forwarded_and_provider_usage_is_metered(tmp_path, monkeypatch):
    def stream(self, messages, *, tools=None, stage="unspecified", **options):
        assert stage == "agent_step_1"
        resp = SimpleNamespace(usage=CompletionUsage(prompt_tokens=10, completion_tokens=5,
                                                     total_tokens=15),
                               model="provider-test", id="id")
        usage = self._record(resp, model="deepseek-flash", stage=stage, latency_ms=1,
                             thinking=False)
        yield "白色"
        yield LLMResponse(text="白色", usage=usage, finish_reason="stop")

    monkeypatch.setattr(LLMClient, "chat_stream", stream)
    settings = Settings(_env_file=None, llm_api_key="offline-test-key")
    ledger = Ledger(tmp_path / "ledger.json")
    client = MeteredLLM(settings, ledger)
    try:
        result = list(client.chat_stream([{"role": "user", "content": "颜色？"}],
                                         stage="agent_step_1"))
    finally:
        client._client.close()
    assert result[0] == "白色" and len(ledger.calls) == 1
    assert ledger.calls[0]["provider_model"] == "provider-test"
    assert ledger.calls[0]["finish_reason"] == "stop"
    assert ledger.spent == Decimal("0.000080")
