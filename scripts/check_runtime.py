"""Check local readiness and optionally the running UI, without calling the model."""

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.readiness import check_ready  # noqa: E402
from acs.scenarios import scenario_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Optional UI liveness URL")
    parser.add_argument("--scenario", type=Path)
    args = parser.parse_args()
    try:
        settings = get_settings()
        if args.scenario:
            settings = scenario_settings(settings, args.scenario)
        report = check_ready(settings)
        if args.url:
            with urlopen(args.url, timeout=3) as response:  # noqa: S310
                report["ui_alive"] = response.status == 200
            if not report["ui_alive"]:
                raise RuntimeError("界面未就绪")
    except Exception as exc:  # noqa: BLE001 - do not print settings/credentials in health logs
        print(json.dumps({"ready": False, "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
