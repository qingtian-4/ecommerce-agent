"""Tests for cost accounting.

A cost report is only trustworthy if its arithmetic is pinned down. These tests
exist because the first price table in this repo listed `deepseek-chat`, a model
name that has since been retired -- and `.env.example` was still pointing at it.
A stale table plus no tests equals silently wrong numbers in a README.

The current table is transcribed from
https://api-docs.deepseek.com/quick_start/pricing (fetched 2026-10-07).
Two properties of that table are load-bearing and are tested explicitly:

* a cache-hit input token is ~50x cheaper than a cache-miss one, and a RAG
  service resends the same system prompt on every call, so ignoring it
  overstates cost;
* off-peak prices are exactly half of peak prices, so an unpinned run can cost
  double purely because of when it executed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from acs.config import PRICING, estimate_cost_cny, is_peak_now

FLASH = "deepseek-flash"
PRO = "deepseek-v4-pro"


# ------------------------------------------------------------------ #
# Basic arithmetic
# ------------------------------------------------------------------ #


def test_no_cache_no_peak_cost_is_exact() -> None:
    """1M in + 1M out at 0.15 / 0.6 USD per 1M = 0.75 USD = 5.4 CNY @ 7.2."""
    cost = estimate_cost_cny(
        FLASH, prompt_tokens=1_000_000, completion_tokens=1_000_000, peak=False
    )
    assert cost == Decimal("5.400000")


def test_cost_scales_linearly() -> None:
    small = estimate_cost_cny(FLASH, 1_000, 500, peak=False)
    large = estimate_cost_cny(FLASH, 10_000, 5_000, peak=False)
    assert small is not None and large is not None
    assert large == small * 10


def test_zero_tokens_is_zero_cost() -> None:
    assert estimate_cost_cny(FLASH, 0, 0, peak=False) == Decimal("0.000000")


def test_unknown_model_returns_none_not_zero() -> None:
    """An unpriced model must be visibly unknown, never silently free."""
    assert estimate_cost_cny("some-unlisted-model", 1000, 1000, peak=False) is None


def test_pro_is_more_expensive_than_flash() -> None:
    flash = estimate_cost_cny(FLASH, 10_000, 2_000, peak=False)
    pro = estimate_cost_cny(PRO, 10_000, 2_000, peak=False)
    assert flash is not None and pro is not None
    assert pro > flash


# ------------------------------------------------------------------ #
# Cache pricing: the property that matters most for a RAG service
# ------------------------------------------------------------------ #


def test_cache_hit_is_cheaper_than_cache_miss() -> None:
    miss = estimate_cost_cny(FLASH, 1_000_000, 0, cached_prompt_tokens=0, peak=False)
    hit = estimate_cost_cny(FLASH, 1_000_000, 0, cached_prompt_tokens=1_000_000, peak=False)
    assert miss is not None and hit is not None
    assert hit < miss


def test_fully_cached_input_cost_is_exact() -> None:
    """1M cached in at 0.003 = 0.003 USD = 0.0216 CNY @ 7.2."""
    cost = estimate_cost_cny(
        FLASH, prompt_tokens=1_000_000, completion_tokens=0,
        cached_prompt_tokens=1_000_000, peak=False,
    )
    assert cost == Decimal("0.021600")


def test_mixed_cache_split_is_exact() -> None:
    """500k uncached (0.075) + 500k cached (0.0015) + 1M out (0.6) = 0.6765 USD."""
    cost = estimate_cost_cny(
        FLASH, prompt_tokens=1_000_000, completion_tokens=1_000_000,
        cached_prompt_tokens=500_000, peak=False,
    )
    assert cost == Decimal("4.870800")


def test_cached_tokens_cannot_exceed_prompt_tokens() -> None:
    """A provider reporting more cached than prompt tokens must not yield a credit."""
    over = estimate_cost_cny(FLASH, 1_000, 0, cached_prompt_tokens=999_999, peak=False)
    exact = estimate_cost_cny(FLASH, 1_000, 0, cached_prompt_tokens=1_000, peak=False)
    assert over == exact


def test_negative_cached_tokens_are_clamped() -> None:
    assert estimate_cost_cny(FLASH, 1_000, 0, cached_prompt_tokens=-50, peak=False) == \
        estimate_cost_cny(FLASH, 1_000, 0, cached_prompt_tokens=0, peak=False)


# ------------------------------------------------------------------ #
# Peak / off-peak
# ------------------------------------------------------------------ #


def test_peak_is_exactly_twice_off_peak() -> None:
    off = estimate_cost_cny(FLASH, 1_000_000, 1_000_000, peak=False)
    on = estimate_cost_cny(FLASH, 1_000_000, 1_000_000, peak=True)
    assert off is not None and on is not None
    assert on == off * 2


def test_peak_detection_weekday_windows() -> None:
    # 2026-10-07 is a Wednesday.
    assert is_peak_now(datetime(2026, 10, 7, 1, 0, tzinfo=UTC)) is True
    assert is_peak_now(datetime(2026, 10, 7, 3, 59, tzinfo=UTC)) is True
    assert is_peak_now(datetime(2026, 10, 7, 4, 0, tzinfo=UTC)) is False
    assert is_peak_now(datetime(2026, 10, 7, 6, 0, tzinfo=UTC)) is True
    assert is_peak_now(datetime(2026, 10, 7, 9, 59, tzinfo=UTC)) is True
    assert is_peak_now(datetime(2026, 10, 7, 10, 0, tzinfo=UTC)) is False
    assert is_peak_now(datetime(2026, 10, 7, 0, 59, tzinfo=UTC)) is False
    assert is_peak_now(datetime(2026, 10, 7, 23, 0, tzinfo=UTC)) is False


def test_weekends_are_always_off_peak() -> None:
    # 2026-10-10 is a Saturday, 2026-10-11 a Sunday.
    assert is_peak_now(datetime(2026, 10, 10, 2, 0, tzinfo=UTC)) is False
    assert is_peak_now(datetime(2026, 10, 11, 7, 0, tzinfo=UTC)) is False


def test_peak_flag_can_be_pinned_for_reproducibility() -> None:
    """Same tokens, explicit peak flag -> deterministic, clock-independent result."""
    a = estimate_cost_cny(FLASH, 500, 500, peak=False)
    b = estimate_cost_cny(FLASH, 500, 500, peak=False)
    assert a == b


# ------------------------------------------------------------------ #
# Table hygiene
# ------------------------------------------------------------------ #


def test_every_priced_model_has_both_directions_and_a_cache_rate() -> None:
    for name, spec in PRICING.items():
        assert spec.input_per_1m > 0, name
        assert spec.output_per_1m > 0, name
        assert spec.cached_input_per_1m is not None, f"{name} missing cache rate"


def test_cache_rate_is_much_cheaper_than_the_miss_rate() -> None:
    """If this stops holding, the cache-aware branch stops earning its keep."""
    for name, spec in PRICING.items():
        assert spec.cached_input_per_1m is not None
        assert spec.cached_input_per_1m < spec.input_per_1m / 10, name


def test_peak_rates_are_consistent_with_off_peak() -> None:
    for name, spec in PRICING.items():
        if spec.peak_input_per_1m is None:
            continue
        assert spec.peak_input_per_1m == spec.input_per_1m * 2, name
        assert spec.peak_output_per_1m == spec.output_per_1m * 2, name


@pytest.mark.parametrize("usd_to_cny", [7.0, 7.2, 7.4])
def test_fx_conversion(usd_to_cny: float) -> None:
    cost = estimate_cost_cny(FLASH, 1_000_000, 0, usd_to_cny=usd_to_cny, peak=False)
    assert cost == (Decimal("0.15") * Decimal(str(usd_to_cny))).quantize(
        Decimal("0.000001")
    )
