"""Owner-approved unrestricted cumulative API use for the loopback synthetic UI."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from acs.config import Settings  # noqa: E402
from acs.interactive_demo import (  # noqa: E402
    InteractiveLedger,
    interactive_app,
    interactive_model,
    load_grant,
)
from acs.local_submission import init_formal_fixture  # noqa: E402


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--price-review", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid port")
    root = args.state_dir.resolve()
    grant = load_grant(args.authorization, root)
    settings = Settings(_env_file=args.env_file)
    if not settings.has_llm_key:
        parser.error("No configured model credential")
    if args.init:
        init_formal_fixture(root)
    shutil.copy2(args.price_review, root / "price_review.json")
    ledger = InteractiveLedger(root / "fees.jsonl", expires_at=grant["expires_at"])

    @contextmanager
    def factory():
        with interactive_model(root, settings, ledger) as model:
            yield model

    app = interactive_app(
        root, factory, origin=f"http://127.0.0.1:{args.port}", secret_values=(settings.llm_api_key,)
    )
    print(f"Live interactive Demo: http://127.0.0.1:{args.port}; cumulative API use unrestricted")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        workers=1,
        access_log=False,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
