"""Step 0 smoke test: one real LLM call, plus a cost-per-100-cases projection.

Run:
    uv run python scripts/smoke_llm.py
"""

from __future__ import annotations

import sys
from decimal import Decimal

from rich.console import Console
from rich.table import Table

from acs.config import get_settings
from acs.llm import LLMClient

console = Console()

PROMPTS = [
    "用一句话说明什么是检索增强生成(RAG)。",
    "用户问：我上周买的耳机昨天降价了，可以退差价吗？请简短回答你需要哪些信息才能判断。",
    "请用 JSON 输出：{\"route\": \"...\", \"confidence\": 0.0, \"reason\": \"...\"}，"
    "route 从 RAG / TOOL / RAG_TOOL 中选一个，回答“退货需要什么材料”。",
]


def main() -> int:
    settings = get_settings()

    if not settings.has_llm_key:
        console.print(
            "[red]ACS_LLM_API_KEY is not set.[/red]\n"
            "Run:  cp .env.example .env  then edit .env and set the key."
        )
        return 2

    console.print(
        f"[bold]provider[/bold]={settings.llm_provider}  "
        f"[bold]model[/bold]={settings.llm_model}  "
        f"[bold]base_url[/bold]={settings.llm_base_url}"
    )

    client = LLMClient(settings=settings)
    table = Table(title="per-call accounting", show_lines=True)
    table.add_column("#", justify="right")
    table.add_column("tokens (in/out)")
    table.add_column("latency ms", justify="right")
    table.add_column("cost CNY", justify="right")
    table.add_column("answer (truncated)")

    for i, prompt in enumerate(PROMPTS, start=1):
        try:
            resp = client.complete(prompt, stage=f"smoke_{i}")
        except Exception as exc:  # noqa: BLE001 - report and stop, do not hide
            console.print(f"[red]call {i} failed:[/red] {type(exc).__name__}: {exc}")
            return 1

        u = resp.usage
        answer = resp.text.replace("\n", " ")[:60]
        table.add_row(
            str(i),
            f"{u.prompt_tokens}/{u.completion_tokens}",
            f"{u.latency_ms:.0f}",
            str(u.cost_cny) if u.cost_cny is not None else "n/a",
            answer,
        )

    console.print(table)

    totals = client.totals()
    console.print("\n[bold]totals[/bold]")
    for k, v in totals.items():
        console.print(f"  {k:>20} = {v}")

    if totals["unpriced_models"]:
        console.print(
            f"[yellow]No price entry for {totals['unpriced_models']}; "
            "add it to PRICING in src/acs/config.py before trusting cost numbers.[/yellow]"
        )
        return 3

    # ---- projection: what does one full evaluation run cost? ----
    n = len(PROMPTS)
    per_call = Decimal(totals["cost_cny"]) / n
    table2 = Table(title="cost projection (1 LLM call per case, naive floor)")
    table2.add_column("scenario")
    table2.add_column("cases", justify="right")
    table2.add_column("passes", justify="right")
    table2.add_column("LLM calls", justify="right")
    table2.add_column("est. cost CNY", justify="right")

    for cases, passes in ((40, 1), (40, 3), (120, 3), (200, 3)):
        calls = cases * passes
        table2.add_row(
            f"dev/test {cases} cases x {passes} pass(es)",
            str(cases),
            str(passes),
            str(calls),
            str((per_call * calls).quantize(Decimal("0.01"))),
        )
    console.print(table2)
    console.print(
        "[dim]Real runs use more than one LLM call per case (router + generation, "
        "sometimes rerank/judge), so treat this as a floor for calibration only. "
        "Token consumption varies heavily with case type — re-measure after "
        "Phase 2 with the real prompt sizes.[/dim]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
