"""Productized UI on the existing loopback V3 service; offline by default."""

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
from acs.shop_demo_model import offline_factory  # noqa: E402
from acs.shop_frontend import create_shop_app, install_shop  # noqa: E402


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--dataset", type=Path,
                        help="Versioned shop-realism-v1 data; requires a fresh --init")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--price-review", type=Path)
    args = parser.parse_args()
    root = args.state_dir.resolve()
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid port")
    if args.init:
        if args.dataset:
            from acs.shop_demo_data import materialize

            materialize(root, args.dataset)
        else:
            init_formal_fixture(root)
    elif args.dataset:
        parser.error("--dataset requires --init and never overwrites an existing fixture")
    origin = f"http://127.0.0.1:{args.port}"
    if args.live:
        if not all([args.env_file, args.authorization, args.price_review]):
            parser.error("Live mode requires existing explicit authorization, env and price review")
        grant = load_grant(args.authorization, root)
        settings = Settings(_env_file=args.env_file)
        if not settings.has_llm_key:
            parser.error("No configured model credential")
        if args.price_review.resolve() != (root / "price_review.json").resolve():
            shutil.copy2(args.price_review, root / "price_review.json")
        ledger = InteractiveLedger(root / "fees.jsonl", expires_at=grant["expires_at"])

        @contextmanager
        def factory():
            with interactive_model(root, settings, ledger) as model:
                yield model

        app = install_shop(
            interactive_app(root, factory, origin=origin, secret_values=(settings.llm_api_key,)),
            origin=origin,
        )
    else:
        app = create_shop_app(
            root,
            offline_factory(root),
            origin=origin,
            runtime_mode="fixed_script",
            answer_streaming=True,
            budget_notice=" 离线SDK脚本，未连接付费模型，不代表真实模型质量。",
        )
    print(f"V3 shop UI: {origin}/shop", flush=True)
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
