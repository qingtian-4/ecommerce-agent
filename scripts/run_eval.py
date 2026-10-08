"""Run the golden eval set. Thin CLI over `acs.eval_runner`.

    uv run python scripts/run_eval.py --passes 3
    uv run python scripts/run_eval.py --only security
    uv run python scripts/run_eval.py --case c-return-window-inside --passes 1

The runner itself lives in the package so it can be imported and unit-tested;
this file only parses arguments. It deliberately does NOT rewrite cases.yaml --
an earlier version mutated the golden set in place when `--case` was passed,
which would have silently shrunk the dataset every time it was used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.eval_runner import print_summary, run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden evaluation set")
    parser.add_argument("--passes", type=int, default=3, help="attempts per case")
    parser.add_argument("--only", help="restrict to one category")
    parser.add_argument("--case", dest="case_id", help="restrict to one case id")
    parser.add_argument(
        "--out",
        default=None,
        help="output directory (default results/baseline)",
    )
    args = parser.parse_args()

    summary = run(
        passes=args.passes,
        only=args.only,
        case_id=args.case_id,
        out_dir=Path(args.out) if args.out else None,
    )
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
