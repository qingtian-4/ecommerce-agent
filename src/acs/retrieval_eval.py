"""Paired section-level retrieval evaluation, separate from answer scoring."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import numpy as np

from .rag import INDEX_FILE, KnowledgeBase
from .retrieval import RRF_K, TOKENIZER_VERSION, LexicalIndex

MODES = ("dense", "bm25", "hybrid")
METRIC_VERSION = 1


def evidence_metrics(groups: list[list[str]], ids: list[str]) -> dict:
    """Each group is one required claim; alternatives within a group are sufficient."""
    if not groups:
        return {"recall": None, "all_evidence": None, "mrr": None}
    found = sum(bool(set(group) & set(ids)) for group in groups)
    first = next((i for i, key in enumerate(ids, start=1)
                  if any(key in group for group in groups)), None)
    return {"recall": found / len(groups), "all_evidence": found == len(groups),
            "mrr": 1 / first if first else 0.0}


def load_cases(path: Path, kb: KnowledgeBase) -> dict:
    dataset = json.loads(path.read_text())
    ids = {c.chunk_id for c in kb._chunks}
    cases = dataset["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("duplicate retrieval case ID")
    for case in cases:
        if not case["query"].strip() or case["split"] not in {"development", "validation"}:
            raise ValueError(f"invalid query or split: {case['id']}")
        for group in case["evidence_groups"]:
            if not group or not set(group) <= ids:
                raise ValueError(f"unknown evidence section: {case['id']}")
    return dataset


def summarize(rows: list[dict], modes: tuple[str, ...] = MODES) -> dict:
    summary = {}
    for mode in modes:
        part = [r for r in rows if r["mode"] == mode]
        supported = [r for r in part if r["evidence_groups"]]
        unsupported = [r for r in part if not r["evidence_groups"]]
        metrics = {"queries": len(part), "supported": len(supported),
                   "unsupported": len(unsupported),
                   "errors": sum(r["error"] is not None for r in part)}
        for stage in ("candidate", "context", "accepted"):
            metrics[f"{stage}_recall"] = float(np.mean([
                r["metrics"][stage]["recall"] for r in supported
            ])) if supported else None
            metrics[f"{stage}_all_evidence"] = sum(
                r["metrics"][stage]["all_evidence"] for r in supported
            )
        metrics["context_mrr"] = float(np.mean([
            r["metrics"]["context"]["mrr"] for r in supported
        ])) if supported else None
        metrics["supported_rejected"] = sum(not r["accepted"] for r in supported)
        metrics["unsupported_accepted"] = sum(bool(r["accepted"]) for r in unsupported)
        # Errors count separately, never as successful refusals.
        metrics["unsupported_rejected"] = sum(
            not r["accepted"] and r["error"] is None for r in unsupported
        )
        for field in ("ranking_ms", "verification_ms", "paired_retrieval_ms"):
            values = [r[field] for r in part]
            metrics[field] = {"p50": float(np.median(values)),
                              "p95": float(np.percentile(values, 95))} if values else {}
        usage = [u for r in part for u in r["verification_usage"]]
        metrics["verification_calls"] = len(usage)
        metrics["verification_tokens"] = sum(u["total_tokens"] for u in usage)
        if modes != MODES:
            rerank_usage = [u for r in part for u in r["rerank_usage"]]
            metrics["rerank_calls"] = len(rerank_usage)
            metrics["rerank_tokens"] = sum(u["total_tokens"] for u in rerank_usage)
            metrics["incomplete_usage_calls"] = sum(
                u.get("usage_missing", False) for u in usage + rerank_usage
            )
            values = [r["rerank_ms"] for r in part]
            metrics["rerank_ms"] = {"p50": float(np.median(values)),
                                    "p95": float(np.percentile(values, 95))} if values else {}
        summary[mode] = metrics
    return summary


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
    }
    if any(manifest[key] != value for key, value in actual.items()):
        raise ValueError("frozen retrieval inputs changed")
    conditions = dataset["conditions"]
    if (conditions != manifest["conditions"] or conditions["rrf_k"] != RRF_K
            or kb.settings.retrieval_candidate_k != conditions["candidate_k"]
            or kb.settings.top_k != conditions["context_k"]):
        raise ValueError("frozen retrieval conditions changed")
    cases = [c for c in dataset["cases"] if c["split"] == split]
    if not cases:
        raise ValueError(f"empty retrieval split: {split}")
    out.mkdir(parents=True)
    # Warm-up excluded from per-query latency, recorded separately.
    started = perf_counter()
    kb._lexical = LexicalIndex(kb._chunks)
    init_ms = (perf_counter() - started) * 1000
    rows = []
    for i, case in enumerate(cases):
        embed_started = perf_counter()
        usage_start = len(kb.llm.usages)
        vector, embedding_error = None, None
        try:
            vector = kb.llm.embed([case["query"]], stage="retrieval_eval_query")[0]
        except Exception as exc:
            embedding_error = type(exc).__name__
        embedding_ms = (perf_counter() - embed_started) * 1000
        embedding_usage = [u.as_dict() for u in kb.llm.usages[usage_start:]]
        # Rotate execution order to reduce systematic cache/time-order bias.
        order = MODES[i % 3:] + MODES[:i % 3]
        for mode in order:
            usage_start = len(kb.llm.usages)
            candidates, accepted, error = [], [], None
            ranking_ms, verification_ms = 0.0, 0.0
            phase_started, phase = None, None
            try:
                if mode != "bm25" and embedding_error:
                    raise RuntimeError(f"embedding failed: {embedding_error}")
                phase_started, phase = perf_counter(), "ranking"
                candidates = kb.retrieve(case["query"], mode=mode,
                                         top_k=conditions["candidate_k"], query_vector=vector)
                ranking_ms = (perf_counter() - phase_started) * 1000
                phase_started, phase = perf_counter(), "verification"
                accepted = kb._filter_evidence(
                    case["query"], candidates[:conditions["context_k"]],
                )
                verification_ms = (perf_counter() - phase_started) * 1000
            except Exception as exc:
                if phase == "ranking":
                    ranking_ms = (perf_counter() - phase_started) * 1000
                elif phase == "verification":
                    verification_ms = (perf_counter() - phase_started) * 1000
                error = f"{type(exc).__name__}: {exc}" if embedding_error else type(exc).__name__
            context = candidates[:conditions["context_k"]]
            rows.append(case | {
                "mode": mode, "error": error,
                "embedding_error": embedding_error, "embedding_ms": embedding_ms,
                "embedding_usage": embedding_usage if mode != "bm25" else [],
                "ranking_ms": ranking_ms, "verification_ms": verification_ms,
                "paired_retrieval_ms": ranking_ms + verification_ms
                + (embedding_ms if mode != "bm25" else 0),
                "verification_usage": [u.as_dict() for u in kb.llm.usages[usage_start:]],
                "candidates": [c.as_dict() for c in candidates],
                "context": [c.as_dict() for c in context],
                "accepted": [c.as_dict() for c in accepted],
                "metrics": {stage: evidence_metrics(
                    case["evidence_groups"], [c.chunk_id for c in chunks],
                ) for stage, chunks in (("candidate", candidates), ("context", context),
                                       ("accepted", accepted))},
            })
        # Incremental records survive interruptions and include failed attempts.
        (out / "per_case.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        )
        print(f"{split} {i + 1}/{len(cases)} {case['id']}", flush=True)
    report = {
        "metric_version": METRIC_VERSION, "split": split, "inputs": actual,
        "conditions": conditions, "tokenizer_version": TOKENIZER_VERSION,
        "created_at": datetime.now(UTC).isoformat(), "lexical_init_ms": init_ms,
        "llm_model": kb.settings.llm_model, "temperature": 0.0, "thinking": False,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            Path(__file__), Path(__file__).with_name("rag.py"),
            Path(__file__).with_name("retrieval.py"),
        )},
        "records_sha256": hashlib.sha256((out / "per_case.jsonl").read_bytes()).hexdigest(),
        "summary": summarize(rows), "actual_accounting": kb.llm.totals(),
        "limitations": [
            "Same query vector shared by dense and hybrid; paired_retrieval_ms reuses its measured "
            "latency, not an independent end-to-end agent request.",
            "Context capped at 3 chunks for all modes; token lengths may differ.",
            "Dense uses deployed cosine gate; lexical/hybrid verify candidate text. "
            "Report raw ranking separately from evidence gate effects.",
            "Synthetic authored cases, one pass; validation is not strictly blind. "
            "Already observed results are development evidence.",
            "No generated answer scoring; retrieval improvements do not prove "
            "answer accuracy gains.",
        ],
    }
    (out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
