"""Read-only views of the published evaluation; never invoke a model."""

from __future__ import annotations

import hashlib
import html
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from statistics import median

import yaml

from .config import get_settings
from .eval_runner import CASES_PATH, PROJECT_ROOT, SCORING_VERSION, Observed, score_case
from .rag import KnowledgeBase
from .rerank_eval import METRIC_VERSION as RERANK_METRIC_VERSION
from .rerank_eval import VARIANTS
from .retrieval_eval import METRIC_VERSION, MODES

GROUP_LABELS = {"before": "优化前", "initial": "首轮优化", "after": "修正后"}
CATEGORY_LABELS = {
    "policy": "政策问答", "product": "商品咨询", "order": "订单查询",
    "logistics": "物流查询", "rag+tool": "政策与订单联合判断",
    "price_protection": "价保", "security": "安全边界", "fallback": "闲聊兜底",
}
RESULTS_PATH = PROJECT_ROOT / "results" / "tool_efficiency"


def catalog_knowledge_rows(root: Path = PROJECT_ROOT, db_path: Path | None = None) -> list[list]:
    """Recalculate the small-corpus table after checking raw inputs and records."""
    base = root / "results/catalog_knowledge"
    manifest = json.loads((base / "frozen_manifest.json").read_text())
    if manifest["cases_sha256"] != hashlib.sha256(
        (root / "data/eval/catalog_knowledge_cases.json").read_bytes(),
    ).hexdigest():
        raise ValueError("小知识库用例与冻结输入不一致")
    for name, expected in manifest["knowledge_fingerprints"].items():
        if hashlib.sha256((root / "data/docs" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("知识正文与小知识库对照不一致")
    with closing(sqlite3.connect(db_path or root / "data/db/business.db")) as conn:
        for table, expected in manifest["business_fingerprints"].items():
            data = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            if hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode(
            )).hexdigest() != expected:
                raise ValueError("业务事实与小知识库对照不一致")
    rows = []
    conditions = None
    reviews = json.loads((base / "manual_review.json").read_text())["attempts"]
    for folder, label in (("baseline", "修复前 · BM25"), ("retrieval", "修复后 · BM25"),
                          ("full_context", "修复后 · 全文"), ("documents", "修复后 · 选整篇文档")):
        summary = json.loads((base / folder / "summary.json").read_text())
        actual_conditions = {k: summary[k] for k in (
            "model", "temperature", "thinking", "retrieval_mode", "reranker_enabled",
            "evidence_backfill", "clock",
        )}
        if conditions is None:
            conditions = actual_conditions
        elif conditions != actual_conditions:
            raise ValueError("小知识库对照的模型或运行条件不一致")
        raw = (base / folder / "per_case.jsonl").read_bytes()
        if (summary["cases_sha256"] != manifest["cases_sha256"]
                or hashlib.sha256(raw).hexdigest() != summary["raw_sha256"]):
            raise ValueError("小知识库原始记录不匹配")
        records = [json.loads(line) for line in raw.splitlines()]
        review = reviews[folder]
        if (review["raw_sha256"] != summary["raw_sha256"]
                or set(review["cases"]) != {r["id"] for r in records}):
            raise ValueError("逐条复核与小知识库原始记录不匹配")
        turns = [t for r in records for t in r["turns"]]
        if len(records) != summary["cases"] or len(turns) != summary["turns"]:
            raise ValueError("小知识库记录条数不匹配")
        usage = [t["result"]["totals"] for t in turns]
        walls = [t["handler_wall_ms"] for t in turns if "handler_wall_ms" in t]
        partial = any(u.get("cost_is_partial") or u.get("tokens_are_partial") for u in usage)
        cost = sum(Decimal(u.get("cost_cny") or "0") for u in usage)
        rows.append([label, len(records), len(turns),
                     sum(all(r["automatic_checks"].values()) for r in records),
                     sum(c["passed"] for c in review["cases"].values()),
                     sum(t["result"]["tool_calls"] for t in turns),
                     sum(u.get("calls", 0) for u in usage),
                     sum(u.get("total_tokens", 0) for u in usage),
                     round(median(walls)) if len(walls) == len(turns) else "未测",
                     str(cost) + ("（不完整）" if partial else "")])
    return rows


def load_retrieval_evaluation(root: Path = PROJECT_ROOT) -> dict:
    """Validate frozen retrieval snapshots before showing comparisons or replay."""
    base = root / "results/hybrid_retrieval"
    source = root / "data/eval/retrieval_cases.json"
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = json.loads((base / "frozen_manifest.json").read_text())
    if manifest["cases_sha256"] != expected:
        raise ValueError("检索用例与冻结清单不匹配")
    settings = get_settings().model_copy(update={"docs_dir": root / "data/docs"})
    if KnowledgeBase(settings)._current_fingerprint() != manifest["knowledge_fingerprint"]:
        raise ValueError("检索快照与当前知识库或切分配置不匹配")
    runs = {}
    for split in ("development", "validation"):
        path = base / split / "summary.json"
        if not path.exists():
            continue
        summary = json.loads(path.read_text())
        raw = (path.parent / "per_case.jsonl").read_bytes()
        if (summary["metric_version"] != METRIC_VERSION
                or any(summary["inputs"][key] != manifest[key] for key in summary["inputs"])
                or summary["conditions"] != manifest["conditions"]
                or summary["records_sha256"] != hashlib.sha256(raw).hexdigest()):
            raise ValueError(f"{split} 检索快照与用例、条件或原始记录不匹配")
        runs[split] = {"report": summary,
                       "records": [json.loads(line) for line in raw.splitlines()]}
    if not runs:
        raise FileNotFoundError("尚未生成检索对照结果")
    return {"cases": json.loads(source.read_text())["cases"], "runs": runs}


def retrieval_comparison_rows(report: dict) -> list[list]:
    rows = []
    for split, run in report["runs"].items():
        for mode in MODES:
            m = run["report"]["summary"][mode]
            rows.append([
                "开发对照" if split == "development" else "冻结验证 · 首次",
                {"dense": "Dense", "bm25": "BM25", "hybrid": "Dense＋BM25＋RRF"}[mode],
                m["queries"], f"{m['candidate_recall']:.1%}", f"{m['context_recall']:.1%}",
                f"{m['accepted_recall']:.1%}", f"{m['supported_rejected']}/{m['supported']}",
                f"{m['unsupported_accepted']}/{m['unsupported']}",
                round(m["paired_retrieval_ms"]["p50"]), m["errors"],
            ])
    return rows


def retrieval_case_view(report: dict, case_id: str) -> dict:
    case = next(c for c in report["cases"] if c["id"] == case_id)
    records = report["runs"][case["split"]]["records"]
    return {"问题与所需证据章节": case, "检索对照": {
        row["mode"]: {key: row[key] for key in (
            "candidates", "context", "accepted", "metrics", "error",
            "ranking_ms", "verification_ms",
        )} for row in records if row["id"] == case_id
    }}


def load_rerank_evaluation(root: Path = PROJECT_ROOT) -> dict:
    """Keep the new ablation separate from the published three-mode baseline."""
    base = root / "results/rerank"
    source = root / "data/eval/rerank_cases.json"
    manifest = json.loads((base / "frozen_manifest.json").read_text())
    if manifest["cases_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("重排用例与冻结清单不匹配")
    settings = get_settings().model_copy(update={"docs_dir": root / "data/docs"})
    if KnowledgeBase(settings)._current_fingerprint() != manifest["knowledge_fingerprint"]:
        raise ValueError("重排快照与当前知识库或切分配置不匹配")
    runs = {}
    for split in ("development", "validation"):
        path = base / split / "summary.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text())
        raw = (path.parent / "per_case.jsonl").read_bytes()
        if (report["metric_version"] != RERANK_METRIC_VERSION
                or report["experiment"] != "rerank_backfill"
                or any(report["inputs"][key] != manifest[key] for key in report["inputs"])
                or report["conditions"] != manifest["conditions"]
                or set(report["summary"]) != set(VARIANTS)
                or report["records_sha256"] != hashlib.sha256(raw).hexdigest()):
            raise ValueError(f"{split} 重排快照与用例、条件或原始记录不匹配")
        runs[split] = {"report": report,
                       "records": [json.loads(line) for line in raw.splitlines()]}
    if not runs:
        raise FileNotFoundError("尚未生成重排对照结果")
    return {"cases": json.loads(source.read_text())["cases"], "runs": runs}


def rerank_comparison_rows(report: dict) -> list[list]:
    labels = {"bm25": "BM25", "hybrid": "RRF", "hybrid_backfill": "RRF＋补位",
              "hybrid_rerank": "RRF＋重排", "hybrid_rerank_backfill": "RRF＋重排＋补位"}
    rows = []
    for split, run in report["runs"].items():
        for variant in VARIANTS:
            m = run["report"]["summary"][variant]
            rows.append([
                "已见问题 · 开发" if split == "development" else "新增验证 · 首次",
                labels[variant], m["queries"], f"{m['context_recall']:.1%}",
                f"{m['accepted_recall']:.1%}", f"{m['accepted_all_evidence']}/{m['supported']}",
                f"{m['unsupported_accepted']}/{m['unsupported']}",
                round(m["paired_retrieval_ms"]["p50"]),
                round(m["verification_tokens"] / m["queries"]),
                round(m["rerank_tokens"] / m["queries"]), m["errors"],
            ])
    return rows


def rerank_case_view(report: dict, case_id: str) -> dict:
    case = next(c for c in report["cases"] if c["id"] == case_id)
    records = report["runs"][case["split"]]["records"]
    return {"问题与所需证据章节": case, "重排与补位对照": {
        row["mode"]: {key: row[key] for key in (
            "candidates", "ranked", "context", "verification_pool", "accepted", "metrics",
            "error", "rerank_ms", "rerank_usage", "verification_ms", "verification_usage",
        )} for row in records if row["id"] == case_id
    }}


def load_evaluation(base: Path = RESULTS_PATH, cases_path: Path = CASES_PATH) -> dict:
    comparison = json.loads((base / "comparison_v5.json").read_text())
    if (comparison["scoring_version"] != SCORING_VERSION
            or comparison["cases_sha256"] != hashlib.sha256(cases_path.read_bytes()).hexdigest()):
        raise ValueError("评测快照与当前用例或评分规则不一致，请重新生成对照结果")
    cases = yaml.safe_load(cases_path.read_text())["cases"]
    case_by_id = {case["id"]: case for case in cases}
    groups = {}
    for name in GROUP_LABELS:
        source = (base / name / "per_case.jsonl").read_bytes()
        if hashlib.sha256(source).hexdigest() != comparison["groups"][name]["source_rows_sha256"]:
            raise ValueError(f"{name} 原始记录与评测快照不一致")
        rows = [json.loads(line) for line in source.splitlines()]
        for row in rows:
            observed = Observed(**{
                key: row[key] for key in Observed.__dataclass_fields__ if key in row
            })
            row["score"] = asdict(score_case(case_by_id[row["case_id"]], observed))
        groups[name] = rows
    return {"comparison": comparison, "cases": cases, "groups": groups}


def comparison_rows(report: dict) -> list[list]:
    rows = []
    for name, label in GROUP_LABELS.items():
        m = report["comparison"]["groups"][name]["metrics"]
        rows.append([
            label, m["attempts"], f"{m['success_rate']:.1%}", f"{m['strict_success_rate']:.1%}",
            m["tool_calls"], m["redundant_calls_total"], round(m["total_tokens"]["mean"]),
            round(m["latency_ms"]["p50"]),
        ])
    return rows


def review_run_rows(root: Path = PROJECT_ROOT) -> list[list]:
    """Keep first-pass verification and matched development runs visibly separate."""
    rows = []
    sources = [
        ("新增验证 · 首次", "holdout/first", "holdout.yaml"),
        ("开发回归 · 修复前", "review_regression/before", "development.yaml"),
        ("开发回归 · 首版过滤", "review_regression/after", "development.yaml"),
        ("开发回归 · 修正后", "review_regression/after_v2", "development.yaml"),
    ]
    for label, folder, dataset in sources:
        path = root / "results" / folder / "summary.json"
        if not path.exists():
            continue
        summary = json.loads(path.read_text())
        expected = hashlib.sha256((root / "data/eval" / dataset).read_bytes()).hexdigest()
        if summary["run"]["cases_sha256"] != expected:
            raise ValueError(f"{label} 与当前用例不匹配")
        errors = summary["dimensions"]["no_errors"]
        multi = summary.get("multi_turn", {}).get("conversation_success", {})
        rows.append([
            label, summary["cases"], summary["passes"], f"{summary['success_rate']:.1%}",
            f"{summary['strict_success_rate']:.1%}", errors["applicable"] - errors["passed"],
            f"{multi['passed']}/{multi['applicable']}" if multi.get("applicable") else "—",
            f"v{summary['run']['scoring_version']}",
        ])
    return rows


def case_rows(report: dict, category: str = "all") -> list[list]:
    rows = []
    for case in report["cases"]:
        if category != "all" and case["category"] != category:
            continue
        passed = []
        for records in report["groups"].values():
            selected = [r for r in records if r["case_id"] == case["id"]]
            passed.append(f"{sum(r['score']['success'] for r in selected)}/{len(selected)}")
        rows.append([case["id"], CATEGORY_LABELS[case["category"]], case["user"],
                     case["query"], *passed])
    return rows


def replay_case(report: dict, case_id: str, attempt: int) -> tuple[str, dict]:
    case = next(c for c in report["cases"] if c["id"] == case_id)
    cards = []
    evidence = {"用例与判据": case, "共同评分版本": SCORING_VERSION, "历史记录": {}}
    for name, label in GROUP_LABELS.items():
        row = next(r for r in report["groups"][name]
                   if r["case_id"] == case_id and r["attempt"] == int(attempt))
        score = row["score"]
        status, color = ("严格通过", "ok") if score["strict_success"] else (
            ("通过 · 存在多余调用", "warn") if score["success"] else ("失败", "bad")
        )
        calls = " → ".join(row["tool_sequence"]) or "未调用工具"
        reasons = "; ".join(score["strict_failure_reasons"]) or "无规则失败项"
        cards.append(
            f"<div class='cs-card'><h4>{label} <span class='pill {color}'>{status}</span></h4>"
            f"<p class='cs-hint'>{html.escape(calls)}</p>"
            f"<div class='replay-answer'>{html.escape(row['answer'])}</div>"
            f"<p class='cs-hint'>耗时 {row['latency_ms']:.0f} ms · "
            f"{row['total_tokens']} token</p>"
            f"<p class='cs-hint'>{html.escape(reasons)}</p></div>"
        )
        evidence["历史记录"][label] = {
            key: row[key] for key in ("tool_args", "retrieved_document_ids", "errors", "score")
        }
    heading = (
        f"<div class='cs-card'><h4>{html.escape(case['query'])}</h4>"
        f"<p class='cs-hint'>{html.escape(case_id)} · {html.escape(case['user'])} · "
        f"第 {int(attempt)} 次尝试 · {html.escape(case.get('notes', ''))}</p></div>"
    )
    return heading + "<div class='replay-grid'>" + "".join(cards) + "</div>", evidence
