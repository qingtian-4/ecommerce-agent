#!/usr/bin/env python3
"""Step 0 gate: verify real connectivity and calibrate cost.

Two things are checked, because both are load-bearing and both are cheap to
verify now and expensive to discover later:

1. the LLM endpoint answers, and what a call actually costs;
2. the embedding endpoint answers, and what dimension it returns.

`embedding_dim` is not cosmetic: the vector index is built with a fixed
dimension, so a provider that silently returns a different size would surface as
a corrupt index rather than a clear error. And the embedding provider is a
separate service with a separate key, so an LLM-only smoke test would pass while
retrieval was entirely unusable.

Run:
    uv run python scripts/check_connectivity.py
"""

from __future__ import annotations

import sys
from decimal import Decimal

from rich.console import Console
from rich.table import Table

from acs.config import estimate_cost_cny, get_settings, is_peak_now
from acs.llm import LLMClient

console = Console()

# Roughly the shape of real prompts this project will send, so the per-call
# figure is a useful calibration rather than a toy: a router decision, a
# grounded answer, and a tool-argument extraction.
SMOKE_PROMPTS: list[tuple[str, str]] = [
    ("router", '你是电商客服意图路由器。判断用户问题该走哪条链路，只输出 JSON：'
               '{"route": "...", "confidence": 0.0}。route 取值：DIRECT/RAG/TOOL/RAG_TOOL/REFUSE。'
               '用户问题：我上周买的耳机昨天降价了，能退差价吗？'),
    ("generate", "你是电商客服。基于以下资料回答用户问题，不要编造资料里没有的信息。"
                 "资料：价保政策 v2，自签收之日起 15 天内，商品降价可申请退还差价，"
                 "差价退回原支付账户。用户问题：我上周买的耳机昨天降价了，能退差价吗？"),
    ("extract", "从用户话术中抽取工具参数，只输出 JSON。"
                "用户话术：我那个订单 A202600317 的耳机到现在还没发货，帮我看看。"
                "输出：{\"order_id\": \"...\", \"intent\": \"...\"}"),
]


def check_llm() -> bool:
    settings = get_settings()
    if not settings.has_llm_key:
        console.print("[red]LLM: no API key configured[/red]")
        return False

    console.print(
        f"[bold]LLM[/bold]  provider={settings.llm_provider}  "
        f"model={settings.llm_model}  base_url={settings.llm_base_url}"
    )
    console.print(
        f"      peak hours now: {'[yellow]YES (double price)[/yellow]' if is_peak_now() else 'no'}"
    )

    client = LLMClient(settings=settings)
    table = Table(title="LLM per-call accounting", show_lines=True)
    for col in ("stage", "tokens in/out", "cached", "latency ms", "cost CNY", "reply (truncated)"):
        table.add_column(col)

    for stage, prompt in SMOKE_PROMPTS:
        try:
            resp = client.complete(prompt, stage=stage)
        except Exception as exc:  # noqa: BLE001 - surface it, do not hide it
            console.print(f"[red]LLM call '{stage}' failed:[/red] {type(exc).__name__}: {exc}")
            return False

        usage = resp.usage
        table.add_row(
            stage,
            f"{usage.prompt_tokens}/{usage.completion_tokens}",
            str(usage.cached_prompt_tokens),
            f"{usage.latency_ms:.0f}",
            str(usage.cost_cny) if usage.cost_cny is not None else "n/a",
            resp.text.replace("\n", " ")[:48],
        )

    console.print(table)

    totals = client.totals()
    console.print(
        f"      calls={totals['calls']}  tokens={totals['total_tokens']}  "
        f"latency={totals['latency_ms']}ms  cost={totals['cost_cny']} CNY"
    )
    if totals["unpriced_models"]:
        console.print(
            f"[yellow]      no price entry for {totals['unpriced_models']} - "
            "add it to PRICING before trusting cost numbers[/yellow]"
        )
        return False

    # Projection. Deliberately labelled a floor: a real case makes more than one
    # LLM call (router + generation, sometimes rerank/judge).
    per_call = Decimal(totals["cost_cny"]) / max(1, totals["calls"])
    console.print(f"      avg cost per LLM call: [bold]{per_call:.6f} CNY[/bold]")
    console.print(
        f"      => a 120-case x 3-pass run at 1 call/case ~= "
        f"[bold]{(per_call * 120 * 3):.4f} CNY[/bold] (floor)"
    )
    return True


def check_embedding() -> bool:
    settings = get_settings()
    if not settings.embedding_model:
        console.print("[yellow]Embedding: ACS_EMBEDDING_MODEL not set - skipped[/yellow]")
        return True

    base_url = settings.embedding_base_url or "https://api.siliconflow.cn/v1"
    # The OpenAI SDK appends "/embeddings" itself. A base_url that already ends
    # with the endpoint path produces a doubled, 404ing URL -- an easy mistake,
    # because most provider docs show the full endpoint rather than the base.
    base_url = base_url.rstrip("/")
    if base_url.endswith("/embeddings"):
        base_url = base_url[: -len("/embeddings")]
        console.print(
            f"[yellow]      ACS_EMBEDDING_BASE_URL ended with '/embeddings'; "
            f"using {base_url} instead (the SDK appends the path)[/yellow]"
        )
    console.print(
        f"[bold]Embedding[/bold]  provider={settings.embedding_provider}  "
        f"model={settings.embedding_model}  base_url={base_url}"
    )

    if not settings.embedding_api_key:
        console.print("[red]Embedding: ACS_EMBEDDING_API_KEY not set[/red]")
        return False

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.embedding_api_key,
            base_url=base_url,
            timeout=settings.llm_timeout_s,
        )
        resp = client.embeddings.create(
            model=settings.embedding_model,
            input=["价保政策：签收后15天内降价可退差价", "物流查询：运单号在哪里看"],
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Embedding call failed:[/red] {type(exc).__name__}: {exc}")
        return False

    vectors = [d.embedding for d in resp.data]
    dims = {len(v) for v in vectors}
    console.print(f"      vectors returned: {len(vectors)}  dims: {sorted(dims)}")

    if len(dims) != 1:
        console.print(f"[red]Embedding: inconsistent dimensions {sorted(dims)}[/red]")
        return False

    dim = dims.pop()
    if settings.embedding_dim is not None and settings.embedding_dim != dim:
        console.print(
            f"[yellow]      configured ACS_EMBEDDING_DIM={settings.embedding_dim} "
            f"but the model returns {dim} - update .env[/yellow]"
        )
    elif settings.embedding_dim is None:
        console.print(
            f"[yellow]      ACS_EMBEDDING_DIM is unset; the model returns {dim}. "
            f"Pin it to {dim} so the index cannot be built at the wrong size.[/yellow]"
        )

    # A trivial sanity signal: the two inputs are unrelated, so their cosine
    # similarity should not be ~1.0. This catches an endpoint that echoes a
    # constant vector, which would make every retrieval score identical.
    import math

    a, b = vectors[0], vectors[1]
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    cos = dot / (na * nb) if na and nb else 0.0
    console.print(f"      cosine similarity of two unrelated inputs: {cos:.4f}")
    if cos > 0.99:
        console.print(
            "[red]      suspiciously high: embedding may be constant, "
            "which would make all retrieval scores identical[/red]"
        )
        return False
    if na == 0 or nb == 0:
        console.print("[red]      zero-length vector returned[/red]")
        return False

    # Cost of embedding the corpus is negligible on hosted endpoints, but the
    # dimension matters for the index, so report what was observed.
    console.print(f"      [green]OK[/green] (dim={dim})")
    return True


def main() -> int:
    console.rule("Step 0 connectivity gate")
    llm_ok = check_llm()
    console.print()
    emb_ok = check_embedding()
    console.print()
    console.rule("summary")
    console.print(f"  LLM       : {'[green]OK[/green]' if llm_ok else '[red]FAILED[/red]'}")
    console.print(f"  Embedding : {'[green]OK[/green]' if emb_ok else '[red]FAILED[/red]'}")
    return 0 if (llm_ok and emb_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
