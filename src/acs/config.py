"""Configuration is injected from the environment / .env.

Everything that is an experimental variable (model, temperature, seed,
prices) lives here so that a run can always be described by its config.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PriceSpec(BaseSettings):
    """Per-1M-token prices. Input and output are priced differently.

    Prices come from the provider's public pricing page and are used only to
    produce a cost estimate for experiments; they are recorded in the trace so
    a stale price can always be detected rather than silently trusted.
    """

    input_per_1m: Decimal
    output_per_1m: Decimal
    cached_input_per_1m: Decimal | None = None


# Update alongside the provider's pricing page. Keep the date in the comment so
# a stale entry is obvious in review.
PRICING: dict[str, PriceSpec] = {
    # DeepSeek public pricing, standard (non-discount) tier.
    "deepseek-chat": PriceSpec(input_per_1m="2.0", output_per_1m="8.0"),
    "deepseek-reasoner": PriceSpec(input_per_1m="4.0", output_per_1m="16.0"),
    "deepseek-v3": PriceSpec(input_per_1m="2.0", output_per_1m="8.0"),
}


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
    llm_model: str = "deepseek-chat"
    llm_small_model: str = ""
    llm_temperature: float = 0.0
    llm_max_output_tokens: int = 1024
    llm_timeout_s: float = 60.0
    llm_usd_to_cny: float = Field(default=7.2, description="only used to print cost in CNY")

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

    # ---------- Experiment metadata ----------
    experiment_name: str = "default"
    seed: int = 42

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        for name in ("db_path", "index_dir", "trace_dir"):
            p = getattr(self, name)
            if not p.is_absolute():
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
) -> Decimal | None:
    """Return estimated cost in CNY, or None when the model has no price entry.

    Returning None rather than 0.0 is deliberate: an unknown price must show up
    as "unknown" in a report instead of quietly becoming a free call.
    """
    price = PRICING.get(model)
    if price is None:
        return None
    usd = (
        Decimal(prompt_tokens) / Decimal(1_000_000) * price.input_per_1m
        + Decimal(completion_tokens) / Decimal(1_000_000) * price.output_per_1m
    )
    return (usd * Decimal(str(usd_to_cny))).quantize(Decimal("0.000001"))
