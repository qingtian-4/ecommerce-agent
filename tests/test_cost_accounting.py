"""Tests that need no API key.

The point of these is to keep the accounting honest: a cost report is only
trustworthy if the arithmetic is pinned down by tests. If a price changes, the
test fails loudly instead of the projection silently drifting.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from acs.config import PRICING, estimate_cost_cny


def test_deepseek_chat_cost_is_arithmetically_exact() -> None:
    # 1M input + 1M output at 2.0 / 8.0 USD per 1M tokens = 10 USD = 72 CNY @ 7.2
    cost = estimate_cost_cny(
        "deepseek-chat", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert cost == Decimal("72.000000")


def test_cost_scales_linearly() -> None:
    small = estimate_cost_cny("deepseek-chat", 1_000, 500)
    large = estimate_cost_cny("deepseek-chat", 10_000, 5_000)
    assert small is not None and large is not None
    assert large == small * 10


def test_zero_tokens_is_zero_cost() -> None:
    assert estimate_cost_cny("deepseek-chat", 0, 0) == Decimal("0.000000")


def test_unknown_model_returns_none_not_zero() -> None:
    """An unpriced model must be visibly unknown, never silently free."""
    assert estimate_cost_cny("some-unlisted-model", 1000, 1000) is None


def test_every_priced_model_has_both_directions() -> None:
    for name, spec in PRICING.items():
        assert spec.input_per_1m > 0, name
        assert spec.output_per_1m > 0, name


@pytest.mark.parametrize("usd_to_cny", [7.0, 7.2, 7.4])
def test_fx_conversion(usd_to_cny: float) -> None:
    cost = estimate_cost_cny(
        "deepseek-chat", 1_000_000, 0, usd_to_cny=usd_to_cny
    )
    assert cost == (Decimal("2.0") * Decimal(str(usd_to_cny))).quantize(
        Decimal("0.000001")
    )
