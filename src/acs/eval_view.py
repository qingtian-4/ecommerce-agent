"""Read-only views of the published evaluation; never invoke a model."""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import asdict
from pathlib import Path

import yaml

from .eval_runner import CASES_PATH, PROJECT_ROOT, SCORING_VERSION, Observed, score_case

GROUP_LABELS = {"before": "优化前", "initial": "首轮优化", "after": "修正后"}
CATEGORY_LABELS = {
    "policy": "政策问答", "product": "商品咨询", "order": "订单查询",
    "logistics": "物流查询", "rag+tool": "政策与订单联合判断",
    "price_protection": "价保", "security": "安全边界", "fallback": "闲聊兜底",
}
RESULTS_PATH = PROJECT_ROOT / "results" / "tool_efficiency"


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
