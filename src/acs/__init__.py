"""Adaptive customer-service agent lab.

Package layout (see docs/ARCHITECTURE.md):

    config    resolved experiment configuration + pricing
    llm       OpenAI-compatible client with per-call accounting
    kb        knowledge-base loading and chunking          (Phase 2)
    retrieval dense / bm25 / rrf / rerank pipelines        (Phase 2+)
    tools     read/write tool registry and safety guard     (Phase 3)
    agent     router + explicit control loop                (Phase 3)
    obs       structured JSONL trace
    eval      runner, metrics, failure taxonomy             (Phase 4)
"""

__version__ = "0.0.1"
