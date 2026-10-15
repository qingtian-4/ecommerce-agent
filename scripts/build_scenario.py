"""Generate a separate v2 snapshot without overwriting existing business data."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.scenarios import build_scenario  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/scenarios/v2"))
    args = parser.parse_args()
    manifest = build_scenario(args.out)
    print(json.dumps({"snapshot": str(args.out), "counts": manifest["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
