"""Build expanded inputs, run real local retrieval, or paired offline business contracts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acs.commerce_benchmark_v2 import agent_run, load  # noqa: E402
from acs.commerce_benchmark_v2_data import ROOT, build  # noqa: E402


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        choices=["build", "verify", "agents", "retrieval", "pilot-plan", "simulator"],
        default="verify",
    )
    p.add_argument("--suite", type=Path, default=ROOT)
    p.add_argument("--out", type=Path)
    p.add_argument("--ids", nargs="+")
    p.add_argument("--model-root", type=Path)
    a = p.parse_args()
    if a.mode not in {"verify", "pilot-plan"} and a.out is None:
        p.error("--out is required")
    if a.mode == "build":
        result = build(a.out.resolve())
    else:
        suite = load(a.suite.resolve())
        if a.mode == "verify":
            result = suite["manifest"]
        elif a.mode == "agents":
            result = agent_run(suite, a.out.resolve(), a.ids)
        elif a.mode == "simulator":
            from acs.commerce_benchmark_v2 import simulator_smoke

            result = simulator_smoke(suite, a.out.resolve())
        elif a.mode == "pilot-plan":
            from acs.live_evaluation import pilot_plan

            result = pilot_plan() | dict(
                contract="local-commerce-v2/m8.1-open-answer-v3",
                task_ids=["EC02-1", "EC03-1"],
                order=["EC02-1:old", "EC02-1:v3", "EC03-1:old", "EC03-1:v3"],
                task_scope="seen development pilot; EC02 validation family is now opened",
                authorization="user permits paid APIs; bind fresh source/price before execute",
            )
        else:
            if a.model_root is None:
                p.error("--model-root required for real retrieval")
            from acs.commerce_retrieval_comparison import run

            result = run(suite, a.out.resolve(), {k: a.model_root / k for k in ["dense", "rerank"]})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
