"""Delete individual prompt clauses in an isolated process; never edit live code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs import eval_runner, service_agent  # noqa: E402

CLAUSES = {
    "order_only": "查订单状态、金额、签收日期只需 `get_order`；",
    "logistics_scope": (
        "问订单到哪了，先查订单再查物流；仅问物流轨迹或揽收，只需 `get_logistics`。"
    ),
    "policy_required": "退货与价保先查订单，再检索对应政策，包括已取消订单；",
    "full_query": (
        "9. **用完整问题检索**，合并相关条件。已有结果足够回答时就结束，\n"
        "   只有缺少必要依据才补查。纯政策与规格咨询不调用业务工具。\n"
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    original = service_agent.SYSTEM_PROMPT
    try:
        for name, clause in CLAUSES.items():
            if original.count(clause) != 1:
                raise ValueError(f"prompt clause changed: {name}")
            prompt = original.replace(clause, "", 1)
            service_agent.SYSTEM_PROMPT = eval_runner.SYSTEM_PROMPT = prompt
            output = args.out / name
            output.mkdir()
            (output / "prompt.txt").write_text(prompt)
            summary = eval_runner.run(
                passes=1, cases_path=ROOT / "data/eval/holdout.yaml", out_dir=output,
            )
            (output / "ablation.json").write_text(json.dumps({
                "removed_clause": clause,
                "prompt_sha256": summary["run"]["prompt_sha256"],
                "method": "one clause removed; same first-pass cases and settings; no live edits",
            }, ensure_ascii=False, indent=2) + "\n")
    finally:
        service_agent.SYSTEM_PROMPT = eval_runner.SYSTEM_PROMPT = original


if __name__ == "__main__":
    main()
