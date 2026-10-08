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
from collections.abc import Iterator
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
    thinking: bool = False
    reasoning_chars: int = 0

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
            "thinking": self.thinking,
            "reasoning_chars": self.reasoning_chars,
        }


@dataclass
class ToolCall:
    """A tool call requested by the model, normalised to plain Python.

    Kept deliberately dumb: id / name / raw JSON arguments string. Parsing the
    arguments is the tool layer's job, because only it knows the schema, and
    because a parse failure must be reportable as a tool error rather than
    crashing the loop.
    """

    id: str
    name: str
    arguments: str


@dataclass
class LLMResponse:
    text: str
    usage: Usage
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    finish_reason: str | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


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
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """One single-turn completion (no tools). See `chat` for tool calling."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return self.chat(
            messages,
            stage=stage,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=response_format_json,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )

    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stage: str = "unspecified",
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """One chat completion, optionally with tool schemas.

        `thinking` is explicit because DeepSeek enables it BY DEFAULT at
        `effort=high`. Two consequences make that default unacceptable here:

        * thinking mode ignores `temperature` outright (the API accepts it and
          does nothing), so a run that appears to pin temperature to 0 would not
          actually be deterministic;
        * the reasoning tokens are billed as output and dominate latency.

        So the default is OFF, and turning it on is a deliberate, recorded act.
        """
        kwargs = self._chat_kwargs(
            messages, tools=tools, model=model, temperature=temperature,
            max_tokens=max_tokens, response_format_json=response_format_json,
            thinking=thinking, reasoning_effort=reasoning_effort,
        )
        started = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        usage = self._record(
            resp, model=kwargs["model"], stage=stage,
            latency_ms=(time.perf_counter() - started) * 1000,
            thinking=kwargs["extra_body"]["thinking"]["type"] == "enabled",
        )
        choice = resp.choices[0]
        message = choice.message
        calls = [
            ToolCall(call.id, call.function.name, call.function.arguments or "{}")
            for call in getattr(message, "tool_calls", None) or []
        ]
        return LLMResponse(
            text=message.content or "", usage=usage, tool_calls=calls,
            raw=resp, finish_reason=choice.finish_reason,
        )

    def chat_stream(
        self, messages: list[dict[str, Any]], *,
        tools: list[dict[str, Any]] | None = None, stage: str = "unspecified",
        **options: Any,
    ) -> Iterator[str | LLMResponse]:
        """Yield content deltas immediately, then one assembled response.

        Tool arguments stay buffered until complete. Usage is recorded once,
        including interrupted requests whose provider usage is unavailable.
        """
        kwargs = self._chat_kwargs(messages, tools=tools, **options)
        started = time.perf_counter()
        stream = None
        usage_chunk = None
        text = ""
        calls: dict[int, ToolCall] = {}
        finish_reason = None
        reasoning_chars = 0
        try:
            stream = self._client.chat.completions.create(
                **kwargs, stream=True, stream_options={"include_usage": True},
            )
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage_chunk = chunk
                if not chunk.choices:
                    continue  # The final usage chunk can have no choices.
                choice = chunk.choices[0]
                delta = choice.delta
                reasoning_chars += len(getattr(delta, "reasoning_content", "") or "")
                for fragment in delta.tool_calls or []:
                    call = calls.setdefault(fragment.index, ToolCall("", "", ""))
                    call.id += fragment.id or ""
                    if fragment.function:
                        call.name += fragment.function.name or ""
                        call.arguments += fragment.function.arguments or ""
                if delta.content:
                    text += delta.content
                    yield delta.content
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
            if finish_reason is None:
                raise RuntimeError("模型响应流中断，未收到完成标记")
        finally:
            usage = self._record(
                usage_chunk, model=kwargs["model"], stage=stage,
                latency_ms=(time.perf_counter() - started) * 1000,
                thinking=kwargs["extra_body"]["thinking"]["type"] == "enabled",
                reasoning_chars=reasoning_chars,
            )
            if stream is not None:
                stream.close()
        yield LLMResponse(
            text=text, usage=usage, tool_calls=[calls[i] for i in sorted(calls)],
            finish_reason=finish_reason,
        )

    def _chat_kwargs(
        self, messages: list[dict[str, Any]], *,
        tools: list[dict[str, Any]] | None = None, model: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
        response_format_json: bool = False, thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        model = model or self.settings.llm_model
        thinking = self.settings.llm_thinking if thinking is None else thinking
        temperature = (
            self.settings.llm_temperature if temperature is None else temperature
        )
        max_tokens = max_tokens or self.settings.llm_max_output_tokens

        kwargs: dict[str, Any] = {}
        if response_format_json:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools

        if thinking:
            # `thinking` is not an OpenAI parameter, so it goes in extra_body.
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = (
                reasoning_effort or self.settings.llm_reasoning_effort
            )
            # Sending temperature here would be silently ignored, which would
            # make a "temperature=0" run look reproducible when it is not. Omit
            # it so the trace never implies control that does not exist.
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            kwargs["temperature"] = temperature

        return {"model": model, "messages": messages, "max_tokens": max_tokens, **kwargs}

    # ------------------------------------------------------------------ #
    def embed(self, texts: list[str], *, stage: str = "embed") -> list[list[float]]:
        """Embed texts with the configured embedding provider.

        Lives here rather than in a separate client so there is exactly one
        place that knows about API keys, base URLs and the trailing-slash trap
        (the SDK appends `/embeddings`, so a base_url already ending in the
        endpoint path produces a doubled, 404ing URL).
        """
        if not texts:
            return []
        s = self.settings
        if not s.embedding_api_key:
            raise RuntimeError("ACS_EMBEDDING_API_KEY is not set")

        base_url = (s.embedding_base_url or "https://api.siliconflow.cn/v1").rstrip("/")
        if base_url.endswith("/embeddings"):
            base_url = base_url[: -len("/embeddings")]

        from openai import OpenAI as _OpenAI

        client = _OpenAI(
            api_key=s.embedding_api_key, base_url=base_url, timeout=s.llm_timeout_s
        )
        started = time.perf_counter()
        resp = client.embeddings.create(model=s.embedding_model, input=texts)
        latency_ms = (time.perf_counter() - started) * 1000

        # Embedding calls are priced separately by the provider and are not in
        # PRICING, so record tokens/latency and leave cost as unknown rather
        # than inventing a number.
        u = getattr(resp, "usage", None)
        self.usages.append(
            Usage(
                model=s.embedding_model or "unknown-embedding",
                stage=stage,
                prompt_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
                total_tokens=int(getattr(u, "total_tokens", 0) or 0),
                latency_ms=latency_ms,
                cost_cny=None,
            )
        )
        return [d.embedding for d in resp.data]

    # ------------------------------------------------------------------ #
    def _record(
        self, resp: Any, *, model: str, stage: str, latency_ms: float, thinking: bool,
        reasoning_chars: int | None = None,
    ) -> Usage:
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

        # In thinking mode the CoT arrives as `reasoning_content`, a sibling of
        # `content`. Track its size: it is billed as output, so a silent spike in
        # reasoning length would otherwise show up only as an unexplained cost
        # increase.
        reasoning = ""
        try:
            reasoning = getattr(resp.choices[0].message, "reasoning_content", "") or ""
        except (AttributeError, IndexError, TypeError):
            reasoning = ""

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
            ) if u is not None else None,
            cached_prompt_tokens=cached,
            thinking=thinking,
            reasoning_chars=len(reasoning) if reasoning_chars is None else reasoning_chars,
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
            "cost_is_partial": bool(unpriced),
            "usages": [usage.as_dict() for usage in self.usages],
        }

    def reset_usage(self) -> None:
        self.usages.clear()
