"""OpenAI-compatible LLM client with per-call accounting.

Design constraints that matter for this project:

* No OpenAI-specific behaviour may leak into the rest of the code base, so the
  provider can be swapped (DeepSeek / Qwen / anything OpenAI-compatible) without
  touching the agent.
* Every call returns a usage record. "Cost-aware routing" is unfalsifiable
  unless every LLM call is counted, attributed to a stage, and priced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from openai import OpenAI

from .config import Settings, estimate_cost_cny, get_settings


@dataclass
class Usage:
    """Accounting for a single LLM call."""

    model: str
    stage: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_cny: Decimal | None = None
    cached_prompt_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stage": self.stage,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "cost_cny": str(self.cost_cny) if self.cost_cny is not None else None,
        }


@dataclass
class LLMResponse:
    text: str
    usage: Usage
    raw: Any = None
    finish_reason: str | None = None


@dataclass
class LLMClient:
    """Thin typed wrapper. One instance per run; it accumulates usage."""

    settings: Settings = field(default_factory=get_settings)
    usages: list[Usage] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.settings.has_llm_key:
            raise RuntimeError(
                "No LLM API key configured. Copy .env.example to .env and set "
                "ACS_LLM_API_KEY before making real calls."
            )
        self._client = OpenAI(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            timeout=self.settings.llm_timeout_s,
        )

    # ------------------------------------------------------------------ #
    def complete(
        self,
        prompt: str,
        *,
        stage: str = "unspecified",
        model: str | None = None,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
    ) -> LLMResponse:
        model = model or self.settings.llm_model
        temperature = (
            self.settings.llm_temperature if temperature is None else temperature
        )
        max_tokens = max_tokens or self.settings.llm_max_output_tokens

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {}
        if response_format_json:
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        resp = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        latency_ms = (time.perf_counter() - started) * 1000

        usage = self._record(resp, model=model, stage=stage, latency_ms=latency_ms)
        choice = resp.choices[0]
        return LLMResponse(
            text=choice.message.content or "",
            usage=usage,
            raw=resp,
            finish_reason=choice.finish_reason,
        )

    # ------------------------------------------------------------------ #
    def _record(self, resp: Any, *, model: str, stage: str, latency_ms: float) -> Usage:
        u = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(u, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(u, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(u, "total_tokens", 0) or 0) or (
            prompt_tokens + completion_tokens
        )

        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)

        usage = Usage(
            model=model,
            stage=stage,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            cost_cny=estimate_cost_cny(
                model,
                prompt_tokens,
                completion_tokens,
                self.settings.llm_usd_to_cny,
                cached_prompt_tokens=cached,
                peak=self.settings.llm_assume_peak,
            ),
            cached_prompt_tokens=cached,
        )
        self.usages.append(usage)
        return usage

    # ------------------------------------------------------------------ #
    def totals(self) -> dict[str, Any]:
        cost = sum(
            (u.cost_cny for u in self.usages if u.cost_cny is not None), Decimal("0")
        )
        unpriced = [u.model for u in self.usages if u.cost_cny is None]
        return {
            "calls": len(self.usages),
            "prompt_tokens": sum(u.prompt_tokens for u in self.usages),
            "completion_tokens": sum(u.completion_tokens for u in self.usages),
            "total_tokens": sum(u.total_tokens for u in self.usages),
            "latency_ms": round(sum(u.latency_ms for u in self.usages), 1),
            "cost_cny": str(cost.quantize(Decimal("0.000001"))),
            "unpriced_models": sorted(set(unpriced)),
        }
