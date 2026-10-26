"""Build or run the separate, network-forbidden local commerce benchmark."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs import multiturn_evaluation as m8  # noqa: E402
from acs.commerce_benchmark import (  # noqa: E402
    audit_fixture,
    load_dataset,
    materialize,
    no_network,
    run_suite,
)
from acs.commerce_benchmark_data import (  # noqa: E402
    DEFAULT_SUITE,
    LATEST_VERSION,
    SEED,
    VERSION,
    build_dataset,
)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["build", "fixture", "verify", "run"], default="run")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--revision", choices=[VERSION, LATEST_VERSION], default=LATEST_VERSION)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--without-controls", action="store_true")
    args = parser.parse_args()
    if args.mode != "verify" and args.out is None:
        parser.error("--out must be a NEW directory")
    if args.mode == "build":
        result = build_dataset(args.out.resolve(), args.seed, revision=args.revision)
    else:
        suite = load_dataset(args.suite.resolve())
        if args.mode == "verify":
            result = suite["manifest"]
        elif args.mode == "fixture":
            with (
                no_network(),
                patch.object(m8, "NOW", datetime.fromisoformat(suite["manifest"]["clock"])),
                m8.business_clock(),
            ):
                fingerprint = materialize(args.out.resolve(), suite)
                result = audit_fixture(args.out.resolve(), suite) | dict(fingerprint=fingerprint)
        else:
            result = run_suite(
                suite, args.out.resolve(), args.ids, challenges=not args.without_controls
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
