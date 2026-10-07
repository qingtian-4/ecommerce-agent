"""Build the dense knowledge index.

Run after adding or editing anything in `data/docs/`:
    uv run python scripts/build_index.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.rag import KnowledgeBase  # noqa: E402


def main() -> int:
    settings = get_settings()
    kb = KnowledgeBase(settings)
    print(f"docs dir      : {settings.docs_dir}")
    print(f"embedding     : {settings.embedding_model}")
    print(f"chunk size    : {settings.chunk_size} (overlap {settings.chunk_overlap})")
    print("embedding all chunks ...")

    stats = kb.build()
    print(
        f"indexed {stats['chunks']} chunks from {stats['documents']} documents "
        f"into {settings.index_dir} (dim={stats['dim']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
