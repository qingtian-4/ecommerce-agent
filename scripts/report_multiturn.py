"""Summarise preserved task records, separating scoring coverage from actual failures."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from eval_multiturn import summarise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records = [json.loads(line) for p in args.raw for line in p.read_text().splitlines()]
    if len({(r["id"], r["trial"]) for r in records}) != len(records):
        raise ValueError("Duplicate task/trial records")
    args.out.mkdir(parents=True, exist_ok=False)
    summary = summarise(records)
    summary["measurement_correction"] = True
    summary["grading_unknown"] = sum(
        any(f["type"] == "judge_error" for f in r["failures"]) for r in records
    )
    summary["deterministic_all_passed"] = sum(all(r["checks"].values()) for r in records)
    summary["checks_failed"] = dict(
        Counter(k for r in records for k, v in r["checks"].items() if not v)
    )
    usages = [u for r in records for u in r["answer_review"]["usage"]]
    agent = [e["result"]["totals"] for r in records for e in r["events"] if "query" in e]
    summary["priced_cost_cny"] = {
        "agent": str(sum((Decimal(t.get("cost_cny") or "0") for t in agent), Decimal(0))),
        "final_judge": str(sum((Decimal(u["cost_cny"] or "0") for u in usages), Decimal(0))),
        "scope": "Configured request-time prices; excludes prior attempts/calibration/external.",
    }
    summary["unpriced_or_incomplete_usage"] = any(
        t.get("cost_is_partial") or t.get("tokens_are_partial") for t in agent
    ) or any(u["cost_cny"] is None or u["usage_missing"] for u in usages)
    summary["failure_notes"] = {
        "judge_error": "未判定，保守计未通过；不是已确认的Agent错误。",
        "grounding": "语义评分器发现的事实/政策问题，仍需要逐条复核。",
        "confirmation": "同时检查应创建和不应创建，未完成正向任务不等于发生越权写入。",
        "runtime": "实际停止或评测执行中断。",
    }
    import hashlib

    summary["sources"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.raw}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    failures = [
        {
            "id": r["id"],
            "trial": r["trial"],
            "category": r["category"],
            "checks": r["checks"],
            "failures": r["failures"],
        }
        for r in records
        if not r["passed"]
    ]
    (args.out / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2) + "\n"
    )
    text = [
        "# 80 段多轮任务实测",
        "",
        "固定脚本用户；作者知道任务。开发40段各1次，留出40段各3次。",
        "评分同时要求SQL/状态约束和独立角色的语义检查；无效评分保守计未通过。",
        "同一批原始回答因证据时间顺序和事实缺失修正而重评分，不代表Agent能力提高。",
        "",
        "| 分组 | 任务 | 运行次数 | 严格通过 | 平均通过率 | 规定次数全部通过 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, s in summary["splits"].items():
        text.append(
            f"| {label} | {s['tasks']} | {s['episodes']} | {s['passed']} | "
            f"{s['mean_success']:.1%} | {s['all_trials_success']:.1%} |"
        )
    text += [
        "",
        f"确定性检查全部通过 {summary['deterministic_all_passed']}/{len(records)}；"
        f"语义评分未判定 {summary['grading_unknown']} 次。",
        "",
        "失败按一次运行内去重分类：",
        "",
        "| 类型 | 涉及运行次数 |",
        "|---|---:|",
    ]
    text += [f"| {k} | {v} |" for k, v in summary["failure_episodes"].items()]
    text += [
        "",
        "标签可重叠。全部失败见 failures.json，原始回答及两版评分保留在来源记录中。",
        "",
        "当前只使用经过成对反例校准的同供应商模型评分，尚无独立人工全量复核；"
        "不能将该数字写成真实客户解决率或生产准确率。耗时是在并行评测环境中测得，不是生产SLA。",
    ]
    (args.out / "REPORT.md").write_text("\n".join(text) + "\n")
    print(json.dumps(summary["splits"]))


if __name__ == "__main__":
    main()
