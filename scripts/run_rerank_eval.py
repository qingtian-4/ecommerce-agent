"""Evaluate rerank and evidence backfill on frozen cases without changing live settings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.config import get_settings  # noqa: E402
from acs.rag import KnowledgeBase  # noqa: E402
from acs.rerank_eval import run_evaluation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "validation"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"llm_assume_peak": True})
    report = run_evaluation(
        KnowledgeBase(settings), ROOT / "data/eval/rerank_cases.json",
        ROOT / "results/rerank/frozen_manifest.json", args.split, args.out,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
