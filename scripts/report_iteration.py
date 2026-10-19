"""Produce a report only from complete, hash-checked iteration attempts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from acs.scenarios import sha256


def read_records(directory: Path, *, external=False):
    manifest = json.loads((directory / "frozen_manifest.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    path = directory / "raw.jsonl"
    if sha256(path) != summary["raw_sha256"]:
        raise ValueError(f"Result hash mismatch: {directory}")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if external:
        expected = {(tid, t, arm) for tid in manifest["task_ids"]
                    for t in range(1, manifest["trials"] + 1) for arm in manifest["arms"]}
        keys = [(r["id"], r["trial"], r["arm"]) for r in rows]
    else:
        expected = {(tid, t) for tid, spec in manifest["planned"].items()
                    for t in range(1, spec["trials"] + 1)}
        keys = [(r["id"], r["trial"]) for r in rows]
    if len(set(keys)) != len(keys) or set(keys) != expected:
        raise ValueError(f"Incomplete or duplicate attempts: {directory}")
    return rows, summary


def local_stats(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["id"], []).append(row)
    return {
        "episodes": len(rows), "passed": sum(r["passed"] for r in rows),
        "mean_success": sum(r["passed"] for r in rows) / len(rows),
        "deterministic_passed": sum(all(r["checks"].values()) for r in rows),
        "all_trials_passed_tasks": sum(all(r["passed"] for r in rs) for rs in groups.values()),
        "tasks": len(groups),
        "judge_unresolved": sum(any(f["type"] == "judge_error" for f in r["failures"])
                                for r in rows),
        "failure_episodes": dict(Counter(
            label for r in rows for label in {f["type"] for f in r["failures"]}
        )),
    }


def compare(before, after):
    b = {(r["id"], r["trial"]): r for r in before}
    a = {(r["id"], r["trial"]): r for r in after}
    if b.keys() != a.keys():
        raise ValueError("Comparison task/trial sets differ")
    return {
        "before": local_stats(before), "after": local_stats(after),
        "paired_outcomes": dict(Counter(
            f"{int(b[k]['passed'])}->{int(a[k]['passed'])}" for k in b
        )),
    }


def write_partial(root: Path, out: Path):
    interruption = json.loads((root / "interruption.json").read_text())
    progress, sources = {}, {}
    for name in interruption["runs"]:
        directory = root / name
        manifest = json.loads((directory / "frozen_manifest.json").read_text())
        summary = json.loads((directory / "summary.json").read_text())
        path = directory / "raw.jsonl"
        if sha256(path) != summary["raw_sha256"]:
            raise ValueError(f"Result hash mismatch: {directory}")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        external = name == "tau_base"
        if external:
            expected = {(tid, trial, arm) for tid in manifest["task_ids"]
                        for trial in range(1, manifest["trials"] + 1) for arm in manifest["arms"]}
            keys = [(r["id"], r["trial"], r["arm"]) for r in rows]
        else:
            expected = {(tid, trial) for tid, spec in manifest["planned"].items()
                        for trial in range(1, spec["trials"] + 1)}
            keys = [(r["id"], r["trial"]) for r in rows]
        if len(set(keys)) != len(keys) or not set(keys) <= expected:
            raise ValueError(f"Unexpected/duplicate attempts: {name}")
        progress[name] = {
            "saved": len(rows), "planned": len(expected),
            "unfinished_attempts": sorted(expected - set(keys)),
            "balance_failure_episodes": sum(
                "Insufficient Balance" in json.dumps(r, ensure_ascii=False) for r in rows
            ),
            "complete": set(keys) == expected,
        }
        sources[name] = sha256(path)
    comparisons = {}
    for split in ("development", "holdout"):
        before, _ = read_records(root / f"contrast_before_{split}")
        after, _ = read_records(root / f"contrast_after_{split}")
        comparisons[split] = compare(before, after)
    acceptance = {}
    for name in ("ui_acceptance.json", "container_read.json", "container_recreated.json"):
        p = root / name
        acceptance[name] = json.loads(p.read_text()) if p.exists() and p.stat().st_size else {
            "complete": False,
        }
    lines = ["# 电商客服 Agent 执行记录（在线评测未完成）", "",
             "模型服务返回 HTTP 402 Insufficient Balance，已停止剩余调用。",
             "没有充值、更换供应商、删除失败或补跑挑选成绩。", "",
             "## 已完成的新增任务对照", "",
             "| 分组 | 基线严格通过 | 候选严格通过 | 确定性通过（前/后） | 未判定（前/后） |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for split, comp in comparisons.items():
        b, a = comp["before"], comp["after"]
        lines.append(f"| {split} | {b['passed']}/{b['episodes']} | "
                     f"{a['passed']}/{a['episodes']} | {b['deterministic_passed']}/"
                     f"{a['deterministic_passed']} | {b['judge_unresolved']}/"
                     f"{a['judge_unresolved']} |")
    lines += ["", "任务由编码助手编写，不是严格盲测。评分未判定保守计未通过；",
              "前后未判定数量不同，严格通过数差异不能单独证明模型质量提升。", "",
              "## 中断进度", "", "| 批次 | 保存/计划 | 含余额错误的尝试 |",
              "| --- | ---: | ---: |"]
    lines += [f"| {name} | {p['saved']}/{p['planned']} | {p['balance_failure_episodes']} |"
              for name, p in progress.items()]
    lines += ["", "80段历史回归与τ正式对照均未完成，且混入账户故障，不能报告完整成功率、",
              "pass^3或据此比较两个方案。已完成尝试、故障和未完成清单全部保留。", "",
              "## 验收与后续", "",
              "实际浏览器检查、容器持久化检查见summary.json的acceptance；初次驱动错误记录保留。",
              "容器最后一轮真实模型探针也遇到402；已有已提交样本的恢复验证不需要新模型请求。",
              "充值后需在冻结条件下建立新的完整对照批次，原中断批次继续保留，不选择拼接好成绩。",
              "已见新增验证任务此后归为回归材料。外部成本含未定价/未导出部分，不是完整账单。", ""]
    out.mkdir(parents=True, exist_ok=False)
    (out / "summary.json").write_text(json.dumps({
        "complete": False, "blocker": "provider_http_402_insufficient_balance",
        "progress": progress, "completed_contrast_comparisons": comparisons,
        "acceptance": acceptance, "source_raw_sha256": sources,
        "report_source_sha256": sha256(Path(__file__)),
    }, ensure_ascii=False, indent=2) + "\n")
    (out / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("results/iteration_v2"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--partial", action="store_true",
                        help="Explicitly report a preserved interrupted batch without full scores")
    args = parser.parse_args()
    if args.partial:
        write_partial(args.root, args.out)
        return
    comparisons, sources, measures = {}, {}, {}
    for label, before_name, after_name in (
        ("历史80段回归", "local_before", "local_after"),
        ("新增开发16段", "contrast_before_development", "contrast_after_development"),
        ("新增验证8段", "contrast_before_holdout", "contrast_after_holdout"),
    ):
        before, bs = read_records(args.root / before_name)
        after, ass = read_records(args.root / after_name)
        comparisons[label] = compare(before, after)
        measures[label] = {"before": bs, "after": ass}
        for name in (before_name, after_name):
            sources[name] = sha256(args.root / name / "raw.jsonl")
    external, tau = read_records(args.root / "tau_base", external=True)
    sources["tau_base"] = sha256(args.root / "tau_base/raw.jsonl")
    stops = Counter(
        m["raw_data"]["acs_stop_reason"] for r in external
        for m in r.get("simulation", {}).get("messages", [])
        if (m.get("raw_data") or {}).get("acs_stop_reason")
    )
    evidence = {}
    for name in ("ui_acceptance.json", "container_write.json", "container_read.json",
                 "container_recreated.json"):
        evidence[name] = json.loads((args.root / name).read_text())
    provenance_path = args.root / "provenance.json"
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else None
    data = {"complete": True, "comparisons": comparisons, "measurements": measures, "tau": tau,
            "acs_external_turn_stops": dict(stops), "acceptance": evidence,
            "provenance": provenance,
            "source_raw_sha256": sources, "report_source_sha256": sha256(Path(__file__))}
    lines = ["# 电商客服 Agent 迭代实测", "",
             "本地两组使用同一合成事实、政策、模型、预算和评分口径。新任务由编码助手编写，",
             "作者知道任务，不是客户语料或严格盲测。历史80段已经参与开发，只作回归。", "",
             "| 分组 | 基线通过 | 候选通过 | 基线确定性通过 | 候选确定性通过 |"
             " 评分未判定（前/后） |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, comp in comparisons.items():
        b, a = comp["before"], comp["after"]
        lines.append(f"| {name} | {b['passed']}/{b['episodes']} | "
                     f"{a['passed']}/{a['episodes']} | {b['deterministic_passed']} | "
                     f"{a['deterministic_passed']} | {b['judge_unresolved']}/"
                     f"{a['judge_unresolved']} |")
    lines += ["", "严格通过同时要求状态/约束与语义评分通过。未判定保守计未通过，",
              "不能全部称为已确认的客服错误；评分器只通过了同供应商成对样例校准。", "",
              "## 外部零售对照", "",
              "固定原版114题、每题3次、两组共684段。官方分数不包含本地额外判据。", "",
              "| 方案 | 已完成 | 平均reward（全部尝试） | pass^1 | pass^3 | 执行异常 |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ("llm_agent", "acs_loop"):
        r = tau[arm]
        lines.append(f"| {arm} | {r['episodes']} | {r['mean_reward']:.1%} | "
                     f"{r['pass^1']:.1%} | {r['pass^3']:.1%} | {r['errors']} |")
    lines += ["", "ACS 内部输入/调用预算与官方 Agent 不完全相同；该成绩不验证本地RAG、",
              "页面、SQLite权限或审核。两组使用官方系统提示和政策，本店提示词改动的效果",
              "由本地对照检验。外部费用与用量不完整，上游cost=0不是免费。", "",
              "## 运行与复核边界", "",
              "页面和容器验收的逐项结果见 summary.json 的 acceptance；原始截图和日志保留。",
              "浏览器驱动首轮有异步等待错误，日志保留；最终以独立申请完成整条DOM流程。",
              "容器首轮有持久路径和启动就绪顺序错误；保留记录，修正驱动后在独立卷复验。",
              "首轮τ冒烟安装了浏览器依赖，另用锁定环境完成冒烟和正式批次，两个批次均保留。",
              "各实验并行执行，耗时受同时运行批次、缓存和服务波动影响，不能作生产SLA或纯性能因果结论。",
              "分项失败、前后成败配对、消耗、来源hash均保留于 summary.json；没有剔除坏尝试。", ""]
    if provenance is not None:
        lines += ["## 账户恢复与证据来源", "",
                  "用户更换并充值 API 后，历史回归两组和τ对照均在本目录重新完整运行，",
                  "未拼接上次中断批次的成功尝试。公开模型配置及冻结运行条件未变；",
                  "账户、缓存和供应商状态变化仍可能影响观测结果。", "",
                  "新增24段任务的四个已完成对照批次及页面验收复用先前完整证据；",
                  "容器真实模型写入与重启、删除重建恢复在新独立卷重新完成。",
                  "来源、复用批次hash及解释边界见 [provenance.json](../provenance.json)。",
                  "这些已见任务不重新称为盲测或未见验证。", ""]
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    (args.out / "REPORT.md").write_text("\n".join(lines))
    print(f"Complete evidence report: {args.out}")


if __name__ == "__main__":
    main()
