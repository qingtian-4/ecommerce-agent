"""Shared fixtures.

Tests must not need an API key, a network call, or a pre-built index. The agent
tests use a scripted fake LLM, so a failure means the loop is wrong rather than
the model behaving differently.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.config import Settings  # noqa: E402
from acs.db_seed import build  # noqa: E402


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One seeded database for the whole session (25 orders, 4 products)."""
    db_path = tmp_path_factory.mktemp("db") / "business.db"
    build(db_path)
    return db_path


@pytest.fixture()
def settings(seeded_db: Path, tmp_path: Path) -> Settings:
    db_path = tmp_path / "business.db"
    shutil.copyfile(seeded_db, db_path)
    return Settings(
        _env_file=None,  # ignore the developer's .env
        db_path=db_path,
        docs_dir=ROOT / "data" / "docs",
        index_dir=tmp_path / "index",
        embedding_model="fake-embedding",
        embedding_dim=8,
        relevance_calibration_path=None,
        retrieval_mode="dense",
    )


class FakeEmbedder:
    """Deterministic bag-of-characters embedding.

    Not semantically meaningful, but it IS deterministic and it does put texts
    sharing characters closer together, which is enough to test the index,
    the normalisation and the score plumbing without any API call. Tests that
    need real retrieval quality are integration tests, not unit tests.
    """

    dim = 8

    def embed(self, texts: list[str], *, stage: str = "embed") -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for ch in text:
                vector[ord(ch) % self.dim] += 1.0
            out.append(vector)
        return out


@dataclass
class ScriptedToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ScriptedResponse:
    text: str = ""
    tool_calls: list[ScriptedToolCall] = field(default_factory=list)
    wants_tools: bool = False


class ScriptedLLM:
    """Replays a fixed sequence of responses, then repeats the last one.

    Records every call so tests can assert what the loop actually sent, and
    counts usage so `totals()` keeps working.
    """

    def __init__(self, responses: list[ScriptedResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []
        self.table: dict[str, Any] = {}

    def chat(self, messages, *, tools=None, stage="unspecified", **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"messages": list(messages), "tools": tools or [], "stage": stage})
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        scripted = self._responses[index]

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15

        class _Response:
            text = scripted.text
            tool_calls = scripted.tool_calls
            wants_tools = scripted.wants_tools
            usage = _Usage()

        return _Response()

    def chat_stream(self, messages, **kwargs):  # noqa: ANN001, ANN003
        yield self.chat(messages, **kwargs)

    def reset_usage(self) -> None:
        pass

    def totals(self) -> dict[str, Any]:
        return {
            "calls": len(self.calls),
            "prompt_tokens": 10 * len(self.calls),
            "completion_tokens": 5 * len(self.calls),
            "total_tokens": 15 * len(self.calls),
            "latency_ms": 1.0,
            "cost_cny": "0.000100",
            "unpriced_models": [],
        }


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
