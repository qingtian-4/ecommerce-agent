"""Minimal trace record.

Deliberately a plain dataclass + JSONL writer rather than an observability
stack. The field names are chosen to map 1:1 onto OpenTelemetry / Langfuse
concepts later, so migrating is a translation, not a redesign.

See docs/ARCHITECTURE.md section "Observability" for the contract.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Stage:
    name: str
    started_at: float = field(default_factory=time.time)
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    # --- identity / reproducibility ---
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    experiment: str = ""
    config_hash: str = ""
    kb_version: str = ""
    dataset_version: str = ""
    seed: int = 0

    # --- input ---
    query: str = ""
    session_turns: int = 0

    # --- routing ---
    route: str = ""
    route_confidence: float | None = None
    route_reason: str = ""
    required_capabilities: list[str] = field(default_factory=list)

    # --- retrieval ---
    retrieval_strategy: str = ""
    retrieved_docs: list[str] = field(default_factory=list)
    retrieval_scores: list[float] = field(default_factory=list)

    # --- tools ---
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # --- mutation safety ---
    mutation: dict[str, Any] | None = None
    confirmation: dict[str, Any] | None = None
    retry: int = 0

    # --- output ---
    final_answer: str = ""
    citations: list[str] = field(default_factory=list)

    # --- outcome ---
    stages: list[Stage] = field(default_factory=list)
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    estimated_cost_cny: str | None = None
    failure_type: str = ""
    reward: float | None = None

    # ------------------------------------------------------------------ #
    def add_stage(self, stage: Stage) -> None:
        self.stages.append(stage)

    def finish(self, *, total_tokens: int, cost_cny: str | None) -> None:
        self.total_latency_ms = round(sum(s.latency_ms for s in self.stages), 1)
        self.total_tokens = total_tokens
        self.estimated_cost_cny = cost_cny

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class TraceWriter:
    """Append-only JSONL writer. One file per (experiment, run)."""

    def __init__(self, directory: Path, run_name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{run_name}.jsonl"

    def write(self, trace: Trace) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(trace.as_dict(), ensure_ascii=False) + "\n")
