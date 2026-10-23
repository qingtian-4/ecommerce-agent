"""Durable, non-refundable request reservations for the <=5 CNY live PoC only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import messages_to_dict


class PocBudget(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, path: Path, *, max_output_tokens: int):
        self.path, self.max_output_tokens = path, max_output_tokens
        self.started = {}
        if not path.exists():
            self._append({
                "type": "policy", "cap_cny": 5, "max_requests": 40,
                "input_usd_per_million": 0.3, "output_usd_per_million": 1.2,
                "usd_to_cny": 7.2, "cache": "all input charged at peak miss",
                "reservation": "2 * serialized UTF-8 bytes + 4096 input tokens; max output",
                "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
                "pricing_checked": "2026-10-23", "refund_reservations": False,
            })

    def _append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            os.fsync(out.fileno())

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        params = kwargs.get("invocation_params", {})
        if params.get("model", params.get("model_name")) != "deepseek-flash":
            raise ValueError("PoC 费用表仅核准 deepseek-flash")
        wire = {"messages": [messages_to_dict(batch) for batch in messages],
                "tools": params.get("tools", [])}
        input_bound = 2 * len(json.dumps(wire, ensure_ascii=False).encode()) + 4096
        reserve = (input_bound * 0.3 + self.max_output_tokens * 1.2) * 7.2 / 1_000_000
        reservations = [r for r in self.rows() if r["type"] == "reserved"]
        if len(reservations) >= 40 or sum(r["reserved_cny"] for r in reservations) + reserve > 5:
            raise RuntimeError("PoC 请求数或5元保守记账上限已到，停止请求")
        self._append({"type": "reserved", "run_id": str(run_id),
                      "input_token_bound": input_bound,
                      "output_token_bound": self.max_output_tokens,
                      "reserved_cny": reserve})
        self.started[str(run_id)] = perf_counter()

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage = {}
        for batch in response.generations:
            for generation in batch:
                usage = getattr(generation.message, "usage_metadata", None) or usage
        if not usage:
            self._append({"type": "usage_missing", "run_id": str(run_id)})
            raise RuntimeError("供应商未返回 usage，保留全额预留并停止")
        input_tokens, output_tokens = usage["input_tokens"], usage["output_tokens"]
        self._append({"type": "completed", "run_id": str(run_id), "usage": usage,
                      "estimated_peak_miss_cny": (input_tokens * 0.3 + output_tokens * 1.2)
                      * 7.2 / 1_000_000,
                      "elapsed_ms": (perf_counter() - self.started[str(run_id)]) * 1000})
        reservation = next(r for r in self.rows()
                           if r["type"] == "reserved" and r["run_id"] == str(run_id))
        if (input_tokens > reservation["input_token_bound"]
                or output_tokens > reservation["output_token_bound"]):
            raise RuntimeError("实际 Token 超过保守预留，停止并核查计费")

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._append({"type": "failed", "run_id": str(run_id),
                      "error_type": type(error).__name__, "usage_unknown": True})
