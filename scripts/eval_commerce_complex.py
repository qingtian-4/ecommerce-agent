"""Frozen complex cases, entity-tool probes and offline runtimes. No paid execution mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acs.commerce_complex_data import ROOT, build, load  # noqa: E402
from acs.commerce_complex_eval import probe_entities, run_offline  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["build", "verify", "entities", "agents"], default="verify")
    p.add_argument("--suite", type=Path, default=ROOT)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    if args.mode != "verify" and args.out is None:
        p.error("--out is required")
    if args.mode == "build":
        report = build(args.out)
    else:
        suite = load(args.suite)
        report = (suite["manifest"] if args.mode == "verify" else
                  {"entities": probe_entities, "agents": run_offline}[args.mode](suite, args.out))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
