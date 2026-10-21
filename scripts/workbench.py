"""One persistent synthetic workspace for customer and authenticated administrator UIs."""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acs.config import get_settings  # noqa: E402
from acs.scenarios import build_scenario  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo", action="store_true", help="Explicitly enable reliability and V2 demo features"
    )
    parser.add_argument("--admin", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--scenario", type=Path, default=Path.home() / ".local/share/ecommerce-agent/workbench-v2"
    )
    args = parser.parse_args()
    if not args.scenario.exists():
        build_scenario(args.scenario)
    if args.demo:
        original = get_settings()
        for flag in (
            "RETURN_STATE_CHECK",
            "ORDER_CANDIDATE_RENDER",
            "ORDER_CONSTRAINT",
            "ORDER_AGGREGATION",
            "WORKFLOW_ROUTER",
            "RUNTIME_RECOVERY",
            "EXECUTION_PROGRESS",
        ):
            os.environ["ACS_" + flag + "_ENABLED"] = "true"
        if not original.admin_username or not original.admin_password.get_secret_value():
            os.environ["ACS_ADMIN_USERNAME"] = "demo-reviewer"
            os.environ["ACS_ADMIN_PASSWORD"] = "demo-test-password"
            print("Local synthetic demo administrator: demo-reviewer / demo-test-password")
        get_settings.cache_clear()
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    entry = ROOT / ("admin_app.py" if args.admin else "app.py")
    port = args.port or (7861 if args.admin else 7860)
    sys.argv = [
        str(entry),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--scenario",
        str(args.scenario.resolve()),
    ]
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
