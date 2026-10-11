"""Paired ablations: fixed ranking pool, independent rerank and evidence backfill."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from .rag import INDEX_FILE, KnowledgeBase
from .retrieval import RRF_K, LexicalIndex
from .retrieval_eval import evidence_metrics, load_cases, summarize

VARIANTS = ("bm25", "hybrid", "hybrid_backfill", "hybrid_rerank", "hybrid_rerank_backfill")
METRIC_VERSION = 1


def run_evaluation(kb: KnowledgeBase, cases_path: Path, manifest_path: Path,
                   split: str, out: Path) -> dict:
    if out.exists():
        raise FileExistsError(out)
    kb.load()
    dataset = load_cases(cases_path, kb)
    manifest = json.loads(manifest_path.read_text())
    actual = {
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "knowledge_fingerprint": kb._fingerprint,
        "embedding_model": kb.settings.embedding_model,
        "index_sha256": hashlib.sha256(
            (kb.settings.index_dir / INDEX_FILE).read_bytes(),
        ).hexdigest(),
        "calibration_sha256": hashlib.sha256(
            kb.settings.relevance_calibration_path.read_bytes(),
        ).hexdigest(),
        "reranker_model": kb.settings.reranker_model,
        "reranker_base_url": kb.settings.reranker_base_url,
        "llm_model": kb.settings.llm_model,
    }
    conditions = dataset["conditions"]
    if (any(manifest[key] != value for key, value in actual.items())
            or conditions != manifest["conditions"] or conditions["rrf_k"] != RRF_K
            or kb.settings.retrieval_candidate_k != conditions["candidate_k"]
            or kb.settings.top_k != conditions["context_k"]
            or manifest["variants"] != list(VARIANTS)):
        raise ValueError("frozen rerank inputs or conditions changed")
    if split == "validation":
        decision = json.loads((manifest_path.parent / "deployment_decision.json").read_text())
        dev_path = manifest_path.parent / "development/summary.json"
        if (decision["development_sha256"] != hashlib.sha256(dev_path.read_bytes()).hexdigest()
                or decision["selected_variant"] not in VARIANTS):
            raise ValueError("validation requires a frozen development selection")
    cases = [c for c in dataset["cases"] if c["split"] == split]
    if not cases:
        raise ValueError("empty rerank split")
    out.mkdir(parents=True)
    started = perf_counter()
    kb._lexical = LexicalIndex(kb._chunks)
    init_ms = (perf_counter() - started) * 1000
    rows = []
    k, candidate_k = conditions["context_k"], conditions["candidate_k"]
    for i, case in enumerate(cases):
        query = case["query"]
        usage_start = len(kb.llm.usages)
        started = perf_counter()
        vector, embedding_error = None, None
        try:
            vector = kb.llm.embed([query], stage="rerank_eval_query")[0]
        except Exception as exc:
            embedding_error = type(exc).__name__
        embedding_ms = (perf_counter() - started) * 1000
        embedding_usage = [u.as_dict() for u in kb.llm.usages[usage_start:]]
        pools, ranking_ms, ranking_errors = {}, {}, {}
        for mode in ("bm25", "hybrid"):
            started = perf_counter()
            pools[mode], ranking_errors[mode] = [], None
            try:
                if mode == "hybrid" and embedding_error:
                    raise RuntimeError("query embedding failed")
                pools[mode] = kb.retrieve(query, mode=mode, top_k=candidate_k,
                                          query_vector=vector)
            except Exception as exc:
                ranking_errors[mode] = type(exc).__name__
            ranking_ms[mode] = (perf_counter() - started) * 1000
        usage_start = len(kb.llm.usages)
        started = perf_counter()
        reranked, rerank_error = [], ranking_errors["hybrid"]
        try:
            if not rerank_error:
                reranked = kb.rerank(query, pools["hybrid"])
        except Exception as exc:
            rerank_error = type(exc).__name__
        rerank_ms = (perf_counter() - started) * 1000
        rerank_usage = [u.as_dict() for u in kb.llm.usages[usage_start:]]
        # Shared ranking requests; only verification is repeated in rotated order.
        offset = i % len(VARIANTS)
        for variant in VARIANTS[offset:] + VARIANTS[:offset]:
            mode = "bm25" if variant == "bm25" else "hybrid"
            with_rerank, backfill = "rerank" in variant, "backfill" in variant
            candidates = pools[mode]
            ranked = reranked if with_rerank else candidates
            verification_pool = ranked if backfill else ranked[:k]
            error = rerank_error if with_rerank else ranking_errors[mode]
            usage_start = len(kb.llm.usages)
            started = perf_counter()
            accepted = []
            if not error:
                try:
                    accepted = kb.select_evidence(query, ranked, top_k=k, backfill=backfill)
                except Exception as exc:
                    error = type(exc).__name__
            verification_ms = (perf_counter() - started) * 1000
            rows.append(case | {
                "mode": variant, "error": error, "embedding_error": embedding_error,
                "embedding_ms": embedding_ms if mode == "hybrid" else 0.0,
                "embedding_usage": embedding_usage if mode == "hybrid" else [],
                "ranking_ms": ranking_ms[mode], "rerank_ms": rerank_ms if with_rerank else 0.0,
                "rerank_usage": rerank_usage if with_rerank else [],
                "verification_ms": verification_ms,
                "verification_usage": [u.as_dict() for u in kb.llm.usages[usage_start:]],
                "paired_retrieval_ms": ranking_ms[mode] + verification_ms
                + (embedding_ms if mode == "hybrid" else 0) + (rerank_ms if with_rerank else 0),
                "candidates": [c.as_dict() for c in candidates],
                "ranked": [c.as_dict() for c in ranked],
                "context": [c.as_dict() for c in ranked[:k]],
                "verification_pool": [c.as_dict() for c in verification_pool],
                "accepted": [c.as_dict() for c in accepted],
                "metrics": {stage: evidence_metrics(case["evidence_groups"],
                                                     [c.chunk_id for c in chunks])
                            for stage, chunks in (("candidate", candidates),
                                                  ("context", ranked[:k]),
                                                  ("accepted", accepted))},
            })
        (out / "per_case.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        )
        print(f"{split} {i + 1}/{len(cases)} {case['id']}", flush=True)
    report = {
        "metric_version": METRIC_VERSION, "experiment": "rerank_backfill", "split": split,
        "inputs": actual, "conditions": conditions,
        "created_at": datetime.now(UTC).isoformat(), "lexical_init_ms": init_ms,
        "temperature": 0.0, "thinking": False,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            Path(__file__), Path(__file__).with_name("rag.py"),
            Path(__file__).with_name("retrieval.py"), Path(__file__).with_name("llm.py"),
            Path(__file__).with_name("retrieval_eval.py"),
        )},
        "records_sha256": hashlib.sha256((out / "per_case.jsonl").read_bytes()).hexdigest(),
        "summary": summarize(rows, VARIANTS), "actual_accounting": kb.llm.totals(),
        "limitations": [
            "52 previously observed cases are development evidence. New validation was frozen "
            "before running; authored synthetic labels are not strictly blind.",
            "Query embedding, BM25/RRF ranking and one rerank request are shared within a case. "
            "Per-variant latency/usage reuse those measurements; sum actual_accounting only.",
            "Backfill verifies up to 10 candidates before selecting at most 3 accepted chunks. "
            "The final chunk budget is equal; verification and final token budgets differ.",
            "Single pass; service caching and network variance remain. Retrieval coverage is "
            "not generated answer accuracy or end-to-end agent latency.",
            "Rerank and embedding prices are unknown; costs are partial. Failures are retained.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
