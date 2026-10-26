"""Real local quantized BGE embeddings/cross-encoder; no synthetic vectors or API."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import faiss
import numpy as np

from .commerce_benchmark import no_network
from .commerce_benchmark_data import dump, file_hash
from .config import Settings
from .rag import KnowledgeBase
from .retrieval import LexicalIndex, reciprocal_rank_fusion
from .retrieval_eval import evidence_metrics

MODELS = {
    "dense": ("Xenova/bge-small-zh-v1.5", "75c43b069aac4d136ba6bc1122f995fedcfd2781"),
    "rerank": ("Xenova/bge-reranker-base", "280bcc27a84e0b898c251e06fddb25171bd9b101"),
}


class LocalBGE:
    def __init__(self, root, kind):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(root / "onnx/model_quantized.onnx"),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.kind = kind
        self.fingerprint = {
            str(p.relative_to(root)): file_hash(p)
            for p in [root / "tokenizer.json", root / "onnx/model_quantized.onnx"]
        }

    def forward(self, texts):
        encoded = self.tokenizer.encode_batch(texts)
        values = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64),
        }
        return self.session.run(None, {i.name: values[i.name] for i in self.session.get_inputs()})[
            0
        ]

    def embed(self, texts, *, query=False):
        texts = [("为这个句子生成表示以用于检索相关文章：" + t) if query else t for t in texts]
        batches = []
        for i in range(0, len(texts), 16):
            y = self.forward(texts[i : i + 16])
            y = y[:, 0, :] if y.ndim == 3 else y
            y = np.ascontiguousarray(y, dtype=np.float32)
            faiss.normalize_L2(y)
            batches.append(y)
        return np.concatenate(batches)

    def rerank(self, query, chunks):
        scores = []
        for i in range(0, len(chunks), 8):
            scores.extend(
                self.forward([(query, c.title + "\n" + c.content) for c in chunks[i : i + 8]])
                .reshape(-1)
                .tolist()
            )
        return [
            replace(c, score=float(s), score_type="local_bge_cross_encoder")
            for c, s in sorted(
                zip(chunks, scores, strict=True), key=lambda x: (-x[1], x[0].chunk_id)
            )
        ]


def metrics(case, ranked):
    ids = [c.chunk_id for c in ranked]
    groups = case["evidence_groups"]
    gold_docs = {x.split("#")[0] for g in groups for x in g}
    result = {str(k): evidence_metrics(groups, ids[:k]) for k in (1, 3, 5, 10)}
    result["document_recall_at3"] = (
        len({c.document_id for c in ranked[:3]} & gold_docs) / len(gold_docs) if gold_docs else None
    )
    result["unsupported_context_exposed"] = bool(ids) if not groups else None
    return result


def run(suite, out, model_roots):
    out.mkdir(parents=True, exist_ok=False)
    with no_network():
        chunks = KnowledgeBase(
            Settings(
                _env_file=None, docs_dir=suite["root"] / "docs", chunk_size=700, chunk_overlap=80
            )
        ).load_documents()
        started = time.perf_counter()
        dense = LocalBGE(model_roots["dense"], "dense")
        reranker = LocalBGE(model_roots["rerank"], "rerank")
        load_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        vectors = dense.embed([c.title + "\n" + c.content for c in chunks])
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        index_ms = (time.perf_counter() - started) * 1000
        lexical = LexicalIndex(chunks)
        rows = []
        for case in suite["rag"]["cases"]:
            q = case["query"]
            times = {}
            ranked = {}
            started = time.perf_counter()
            ranked["bm25"] = lexical.search(q, 10)
            times["bm25"] = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            v = dense.embed([q], query=True)
            scores, indices = index.search(v, min(10, len(chunks)))
            ranked["dense"] = [
                replace(chunks[i], score=float(s), score_type="bge_cosine")
                for s, i in zip(scores[0], indices[0], strict=True)
            ]
            times["dense"] = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            ranked["hybrid"] = reciprocal_rank_fusion(ranked["dense"], ranked["bm25"])[:10]
            times["hybrid"] = (
                times["dense"] + times["bm25"] + (time.perf_counter() - started) * 1000
            )
            started = time.perf_counter()
            ranked["rerank"] = reranker.rerank(q, ranked["hybrid"])
            times["rerank"] = times["hybrid"] + (time.perf_counter() - started) * 1000
            gold_ids = list(dict.fromkeys(g[0] for g in case["evidence_groups"]))
            ranked["oracle"] = [next(c for c in chunks if c.chunk_id == i) for i in gold_ids]
            times["oracle"] = None
            for mode, items in ranked.items():
                rows.append(
                    dict(
                        id=case["id"],
                        split=case["split"],
                        category=case["category"],
                        mode=mode,
                        ranked=[c.chunk_id for c in items],
                        metrics=metrics(case, items),
                        latency_ms=times[mode],
                    )
                )
            dump(out / "per_case.json", rows)
        summaries = {}
        for split in ("development", "validation"):
            summaries[split] = {}
            for mode in ("bm25", "dense", "hybrid", "rerank", "oracle"):
                selected = [r for r in rows if r["split"] == split and r["mode"] == mode]
                supported = [r for r in selected if r["metrics"]["3"]["recall"] is not None]
                missing = [r for r in selected if r not in supported]
                summaries[split][mode] = dict(
                    cases=len(selected),
                    supported=len(supported),
                    recall={
                        str(k): float(np.mean([r["metrics"][str(k)]["recall"] for r in supported]))
                        for k in (1, 3, 5, 10)
                    },
                    all_evidence_at3=sum(r["metrics"]["3"]["all_evidence"] for r in supported),
                    mrr_at10=float(np.mean([r["metrics"]["10"]["mrr"] for r in supported])),
                    document_recall_at3=float(
                        np.mean([r["metrics"]["document_recall_at3"] for r in supported])
                    ),
                    unsupported=len(missing),
                    unsupported_nonempty=sum(
                        bool(r["metrics"]["unsupported_context_exposed"]) for r in missing
                    ),
                    p50_ms=float(np.median([r["latency_ms"] for r in selected]))
                    if mode != "oracle"
                    else None,
                    p95_ms=float(np.percentile([r["latency_ms"] for r in selected], 95))
                    if mode != "oracle"
                    else None,
                )
        report = dict(
            kind="real_local_onnx_quantized_BGE_inference",
            model_load_ms=load_ms,
            index_ms=index_ms,
            chunks=len(chunks),
            models={
                k: dict(repository=MODELS[k][0], revision=MODELS[k][1], files=m.fingerprint)
                for k, m in [("dense", dense), ("rerank", reranker)]
            },
            summaries=summaries,
            generated_answer_quality="not_evaluated",
            paid_requests=0,
            conditions=dict(
                candidate_k=10,
                context_k=3,
                rrf_k=60,
                dense_input="title+content; CLS normalized; query instruction",
                max_sequence=512,
            ),
            dataset=suite["manifest"],
            source_sha256=file_hash(Path(__file__)),
        )
        dump(out / "summary.json", report)
        dump(
            out / "badcases.json",
            [
                r
                for r in rows
                if r["mode"] != "oracle"
                and (
                    r["metrics"]["3"]["all_evidence"] is False
                    or r["metrics"]["unsupported_context_exposed"]
                )
            ],
        )
        return report
