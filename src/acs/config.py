"""Configuration is injected from the environment / .env.

Everything that is an experimental variable (model, temperature, seed,
prices) lives here so that a run can always be described by its config.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PriceSpec(BaseSettings):
    """Per-1M-token USD prices for one model.

    `cached_input_per_1m` is not a nicety. DeepSeek prices a cache-hit input
    token roughly 50x cheaper than a cache-miss one, and a RAG service resends
    the same system prompt on every call, so ignoring the cache rate overstates
    cost. `estimate_cost_cny` therefore needs the cached token count.

    `peak_*` fields exist because DeepSeek halves prices off-peak. Peak is
    defined in UTC (Mon-Fri 01:00-04:00 and 06:00-10:00); the working assumption
    here is off-peak, with the peak multiplier reported alongside any estimate.
    """

    input_per_1m: Decimal
    output_per_1m: Decimal
    cached_input_per_1m: Decimal | None = None
    peak_input_per_1m: Decimal | None = None
    peak_output_per_1m: Decimal | None = None
    peak_cached_input_per_1m: Decimal | None = None


# Source: https://api-docs.deepseek.com/quick_start/pricing  (fetched 2026-10-07)
#
# Re-check before quoting any cost figure in the README. The previous table in
# this file listed `deepseek-chat` / `deepseek-reasoner`, which have since been
# retired, and the retired name was still being used by .env.example. A stale
# table is the failure mode this comment exists to prevent.
PRICING: dict[str, PriceSpec] = {
    # DeepSeek-V4.1-Flash. Off-peak / peak, USD per 1M tokens.
    "deepseek-flash": PriceSpec(
        input_per_1m="0.15",
        output_per_1m="0.6",
        cached_input_per_1m="0.003",
        peak_input_per_1m="0.3",
        peak_output_per_1m="1.2",
        peak_cached_input_per_1m="0.006",
    ),
    # DeepSeek-V4-Pro-0813.
    "deepseek-v4-pro": PriceSpec(
        input_per_1m="0.66",
        output_per_1m="1.98",
        cached_input_per_1m="0.022",
        peak_input_per_1m="1.32",
        peak_output_per_1m="3.96",
        peak_cached_input_per_1m="0.044",
    ),
}


def is_peak_now(when: datetime | None = None) -> bool:
    """True during DeepSeek peak hours: Mon-Fri 01:00-04:00 and 06:00-10:00 UTC."""
    now = when or datetime.now(UTC)
    if now.weekday() >= 5:  # Saturday / Sunday are always off-peak
        return False
    return (1 <= now.hour < 4) or (6 <= now.hour < 10)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACS_",
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- LLM ----------
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-flash"
    llm_small_model: str | None = None
    llm_temperature: float = 0.0
    llm_max_output_tokens: int = 1024
    llm_timeout_s: float = 60.0
    llm_usd_to_cny: float = Field(default=7.2, description="only used to print cost in CNY")
    # None = detect from the clock. Pin it to make cost numbers reproducible.
    llm_assume_peak: bool | None = None
    # DeepSeek enables thinking mode BY DEFAULT at high effort. It is disabled
    # here because thinking mode ignores `temperature` entirely (so a run could
    # not be made deterministic) and because reasoning tokens are billed as
    # output and dominate latency. Enabling it must be a deliberate, recorded act.
    llm_thinking: bool = False
    llm_reasoning_effort: str = "high"

    # ---------- Embedding ----------
    embedding_provider: str = "api"
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dim: int | None = None

    # ---------- Storage ----------
    db_path: Path = Path("data/db/business.db")
    index_dir: Path = Path("data/.index")
    trace_dir: Path = Path("results/traces")
    docs_dir: Path = Path("data/docs")

    # Separate operator entrance; customer simulation never grants this identity.
    admin_username: str = ""
    admin_password: SecretStr = SecretStr("")

    # ---------- RAG ----------
    knowledge_mode: Literal["retrieval", "full_context", "documents"] = "retrieval"
    # Chunk size is picked once and deliberately NOT tuned in this phase. Tuning
    # chunks before an evaluation set exists would be fitting to nothing.
    chunk_size: int = 700
    chunk_overlap: int = 80
    top_k: int = 3
    # Selected on frozen development evidence; dense/hybrid remain comparison modes.
    retrieval_mode: Literal["dense", "bm25", "hybrid"] = "bm25"
    retrieval_candidate_k: int = Field(default=10, ge=1)
    evidence_backfill: bool = False
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_base_url: str = "https://api.siliconflow.cn/v1"
    # Empty reuses the embedding key only when both base URLs are identical.
    reranker_api_key: str = ""
    reranker_timeout_s: float = Field(default=15.0, gt=0)
    relevance_calibration_path: Path | None = Path("data/eval/relevance_calibration.json")

    # ---------- Agent ----------
    write_receipt_guard_enabled: bool = False
    max_steps: int = 6
    turn_timeout_s: float = Field(default=120.0, gt=0)
    context_max_chars: int = Field(default=24000, ge=100)
    max_api_requests: int = Field(default=16, ge=1)
    max_tool_calls: int = Field(default=12, ge=1)
    max_retrieval_calls: int = Field(default=4, ge=1)
    # Opt-in P0: only repair an absent order-only draft from explicit current-turn evidence.
    return_state_check_enabled: bool = False
    # Opt-in P1: authoritative rendering for a pure order-candidate lookup.
    order_candidate_render_enabled: bool = False
    # Independent opt-ins: finite colour constraints and explicit count requests.
    order_constraint_enabled: bool = False
    order_aggregation_enabled: bool = False
    workflow_router_enabled: bool = False
    runtime_recovery_enabled: bool = False
    execution_progress_enabled: bool = False
    transient_retries: int = Field(default=1, ge=0, le=2)
    no_progress_limit: int = Field(default=3, ge=2, le=6)
    # Freeze the business clock for comparable evaluation runs; live queries use today.
    reference_date: date | None = None

    @field_validator(
        "embedding_dim", "llm_small_model", "relevance_calibration_path", mode="before",
    )
    @classmethod
    def _empty_string_means_unset(cls, value: object) -> object:
        """Treat an empty value in `.env` as unset rather than invalid.

        `ACS_EMBEDDING_DIM=` is the natural way to leave an optional numeric
        field blank, and `.env.example` ships that way. Without this, pydantic
        tries to parse '' as an int and the whole config fails to load -- with an
        error that points at the field rather than at the real cause.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        for name in ("db_path", "index_dir", "trace_dir", "docs_dir", "relevance_calibration_path"):
            p = getattr(self, name)
            if p is not None and not p.is_absolute():
                object.__setattr__(self, name, (PROJECT_ROOT / p).resolve())
        return self

    @property
    def has_llm_key(self) -> bool:
        return bool(self.llm_api_key) and not self.llm_api_key.startswith("sk-xxxx")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def estimate_cost_cny(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    usd_to_cny: float = 7.2,
    cached_prompt_tokens: int = 0,
    peak: bool | None = None,
) -> Decimal | None:
    """Return estimated cost in CNY, or None when the model has no price entry.

    Returning None rather than 0.0 is deliberate: an unknown price must show up
    as "unknown" in a report instead of quietly becoming a free call.

    `peak=None` means "detect from the clock". Pass True/False explicitly to make
    a result reproducible, since the same run can otherwise cost double purely
    because of when it was executed.
    """
    price = PRICING.get(model)
    if price is None:
        return None

    at_peak = is_peak_now() if peak is None else peak
    if at_peak and price.peak_input_per_1m is not None:
        input_rate = price.peak_input_per_1m
        output_rate = price.peak_output_per_1m or price.output_per_1m
        cached_rate = price.peak_cached_input_per_1m or input_rate
    else:
        input_rate = price.input_per_1m
        output_rate = price.output_per_1m
        cached_rate = price.cached_input_per_1m or input_rate

    cached = max(0, min(cached_prompt_tokens, prompt_tokens))
    uncached = prompt_tokens - cached
    million = Decimal(1_000_000)

    usd = (
        Decimal(uncached) / million * input_rate
        + Decimal(cached) / million * cached_rate
        + Decimal(completion_tokens) / million * output_rate
    )
    return (usd * Decimal(str(usd_to_cny))).quantize(Decimal("0.000001"))
