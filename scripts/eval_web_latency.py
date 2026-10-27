"""Measure local stream persistence on disk, using fixed text and no model/network."""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from langchain_core.messages import AIMessageChunk  # noqa: E402

from acs.commerce_benchmark import materialize  # noqa: E402
from acs.commerce_benchmark_v2 import load  # noqa: E402
from acs.web_service import AnswerStreamCallback, WebEngine  # noqa: E402


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    root = out / ".private-evidence" / "runtime"
    materialize(root, load(ROOT / "data/eval/commerce_benchmark_v2_2"))
    engine = WebEngine(root, lambda: nullcontext(None))
    engine.start()
    try:
        started = time.perf_counter()
        result = engine.gateway.registry._list_orders(current_user_id="USER001")
        lookup = time.perf_counter() - started
        callback = AnswerStreamCallback(engine, "offline", "fixed-text")
        run_id = uuid4()
        callback.on_chat_model_start({}, [], run_id=run_id)
        text = "订单状态来自数据库，请从候选中选择需要查询的订单。" * 40
        started = time.perf_counter()
        for i in range(0, len(text), 3):
            part = text[i:i+3]
            callback.on_llm_new_token(part, run_id=run_id,
                                     chunk=SimpleNamespace(message=AIMessageChunk(content=part)))
        callback.complete(text)
        elapsed = time.perf_counter() - started
        rows, cursor = [], 0
        while batch := engine.events("offline", cursor):
            rows.extend(batch)
            cursor = batch[-1]["id"]
        result = dict(
            protocol="fixed-text-disk-stream-v1", paid_requests=0,
            text_characters=len(text), chunk_characters=3,
            stream_persistence_seconds=elapsed,
            deltas=sum(e["type"] == "answer_delta" for e in rows),
            final_exact=rows[-1]["data"]["text"] == text,
            owned_orders=len(result["orders"]), order_lookup_seconds=lookup,
            journal_mode=engine.wal_anchor.execute("PRAGMA journal_mode").fetchone()[0],
            note="Real disk, synthetic fixed text; not a real-model latency estimate.",
        )
        (out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    finally:
        engine.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
