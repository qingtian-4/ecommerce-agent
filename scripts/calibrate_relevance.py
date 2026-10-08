"""Measure real retrieval scores; calibrate conservatively before enabling rejection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.config import get_settings  # noqa: E402
from acs.rag import CHUNKER_VERSION, KnowledgeBase  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold-from", type=Path, help="verify a fixed deployed calibration")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    settings = get_settings().model_copy(update={"relevance_calibration_path": None})
    source = ROOT / "data/eval/retrieval_samples.yaml"
    samples = yaml.safe_load(source.read_text())["samples"]
    kb = KnowledgeBase(settings)
    kb.load()
    vectors = kb.llm.embed([s["query"] for s in samples], stage="relevance_calibration")
    matrix = KnowledgeBase._normalise(np.asarray(vectors, dtype="float32"))
    scores, indices = kb._index.search(matrix, len(kb._chunks))
    rows = []
    for sample, query_scores, query_indices in zip(samples, scores, indices, strict=True):
        hits = [kb._chunks[int(i)].as_dict() | {"score": float(score)}
                for score, i in zip(query_scores, query_indices, strict=True)
                if i >= 0 and re.sub(r"(?m)^#{1,6}\s.*$", "", kb._chunks[int(i)].content).strip()]
        rows.append(sample | {"top_score": hits[0]["score"], "hits": hits[:3]})
    # Keep every supported calibration query; reject only below its observed floor.
    threshold = min(r["top_score"] for r in rows
                    if r["split"] == "calibration" and r["in_scope"]) - 0.000001
    if args.threshold_from:
        bound = KnowledgeBase(settings.model_copy(update={
            "relevance_calibration_path": args.threshold_from,
        }))
        bound.load()
        threshold = bound._min_score
    counts = {}
    for split in ("calibration", "validation"):
        part = [r for r in rows if r["split"] == split]
        counts[split] = {
            "supported": sum(r["in_scope"] for r in part),
            "supported_rejected": sum(r["in_scope"] and r["top_score"] < threshold for r in part),
            "unsupported": sum(not r["in_scope"] for r in part),
            "unsupported_rejected": sum(
                not r["in_scope"] and r["top_score"] < threshold for r in part
            ),
        }
    report = {
        "model": settings.embedding_model,
        "knowledge_sha256": hashlib.sha256(b"".join(
            p.name.encode() + b"\0" + p.read_bytes() for p in sorted(settings.docs_dir.glob("*.md"))
        )).hexdigest(),
        "samples_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "chunker_version": CHUNKER_VERSION,
        "threshold": threshold, "counts": counts, "rows": rows,
        "method": ("fixed deployed threshold; no reselection" if args.threshold_from else
                   "lowest supported calibration top score; validation not used for selection"),
        "limitations": "small synthetic sample; score overlap cannot prove topical relevance",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"threshold": threshold, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
