"""Command-line entry points.

Every experiment is launched from here so that the command line is recorded in
EXPERIMENT_LOG.md verbatim and can be replayed.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings

app = typer.Typer(add_completion=False, help="Adaptive customer-service agent lab")
console = Console()


@app.command()
def info() -> None:
    """Show the resolved configuration (never prints the key itself)."""
    s = get_settings()

    table = Table(title="Resolved configuration", show_lines=False)
    table.add_column("key", style="cyan")
    table.add_column("value")

    rows = [
        ("llm_provider", s.llm_provider),
        ("llm_model", s.llm_model),
        ("llm_small_model", s.llm_small_model or "(unset)"),
        ("llm_base_url", s.llm_base_url),
        ("llm_temperature", str(s.llm_temperature)),
        ("llm_max_output_tokens", str(s.llm_max_output_tokens)),
        ("llm_api_key", "***set***" if s.has_llm_key else "(missing)"),
        ("embedding_provider", s.embedding_provider),
        ("embedding_model", s.embedding_model or "(unset)"),
        ("db_path", str(s.db_path)),
        ("index_dir", str(s.index_dir)),
        ("trace_dir", str(s.trace_dir)),
    ]
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


@app.command()
def smoke(
    prompt: str = typer.Option(
        "用一句话说明什么是检索增强生成(RAG)。", "--prompt", "-p"
    ),
    stage: str = typer.Option("smoke", "--stage"),
) -> None:
    """One real LLM call that prints the answer, token usage and cost.

    This is the gate for Step 0: no later phase is meaningful until this
    succeeds and the cost of a single call is known.
    """
    from .llm import LLMClient

    s = get_settings()
    client = LLMClient(settings=s)

    console.print(f"[dim]model={s.llm_model}  base_url={s.llm_base_url}[/dim]")
    resp = client.complete(prompt, stage=stage)

    console.print("\n[bold green]response[/bold green]")
    console.print(resp.text)
    console.print(f"[dim]finish_reason={resp.finish_reason}[/dim]")

    totals = client.totals()
    console.print("\n[bold]usage[/bold]")
    console.print_json(json.dumps(totals, ensure_ascii=False))
    if totals["unpriced_models"]:
        console.print(
            f"[yellow]warning: no price entry for {totals['unpriced_models']} "
            f"- add it to PRICING in src/acs/config.py[/yellow]"
        )


if __name__ == "__main__":
    app()
