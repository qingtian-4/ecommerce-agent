"""Rebuild the public-source demo into a fresh directory without any API call."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acs.shop_demo_data import LEGACY_VERSION, VERSION, build_dataset  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--revision", choices=[LEGACY_VERSION, VERSION], default=VERSION)
    args = parser.parse_args()
    build_dataset(args.out, revision=args.revision)
