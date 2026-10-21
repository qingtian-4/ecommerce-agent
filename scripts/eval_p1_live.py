"""Small, explicitly budgeted live P1 switch comparison; no retries or paid judge."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.review_order_candidate_scope import business_state  # noqa: E402
from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import get_settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.llm import LLMClient  # noqa: E402
from acs.runtime import BudgetExceeded  # noqa: E402
from acs.scenarios import build_scenario, data_fingerprint, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


class Ledger:
    """Reserve a worst-case call before sending it; unknown usage stops the experiment."""

    def __init__(self, path, *, limit=Decimal("1"), max_requests=72,
                 input_guard=Decimal("3"), output_guard=Decimal("10")):
        self.path, self.limit, self.max_requests = path, limit, max_requests
        self.input_guard, self.output_guard = input_guard, output_guard
        self.spent = Decimal(0)
        self.calls = []
        self.halted = None

    def save(self):
        self.path.write_text(json.dumps({"spent_guard_cny": str(self.spent),
                                        "halted": self.halted, "calls": self.calls},
                                       ensure_ascii=False, indent=2) + "\n")

    def begin(self, kwargs):
        # UTF-8 bytes exceed BPE text tokens; extra headroom covers tool/chat framing.
        request = json.dumps(kwargs, ensure_ascii=False)
        input_bound = len(request.encode()) + 4096
        reserve = (input_bound * self.input_guard
                   + kwargs["max_tokens"] * self.output_guard) / 1_000_000
        if self.halted or len(self.calls) >= self.max_requests or self.spent + reserve > self.limit:
            self.halted = self.halted or "request_or_money_limit"
            self.save()
            raise BudgetExceeded("本次付费实验预算已达上限")
        record = {"sequence": len(self.calls) + 1, "at_utc": datetime.now(UTC).isoformat(),
                  "reserved_cny": str(reserve), "request": json.loads(request)}
        self.calls.append(record)
        self.save()
        return record

    def complete(self, record, usage, provider_usage=None):
        if (usage is None or usage.usage_missing or usage.prompt_tokens <= 0
                or usage.completion_tokens < 0
                or usage.total_tokens != usage.prompt_tokens + usage.completion_tokens):
            self.spent += Decimal(record["reserved_cny"])
            self.halted = "usage_missing_or_request_failed"
            record.update(usage_missing=True, charge_guard_cny=record["reserved_cny"])
        else:
            charge = (usage.prompt_tokens * self.input_guard
                      + usage.completion_tokens * self.output_guard) / 1_000_000
            self.spent += charge
            record.update(usage=usage.as_dict(), provider_usage=provider_usage,
                          charge_guard_cny=str(charge))
            if charge > Decimal(record["reserved_cny"]):
                self.halted = "token_reservation_exceeded"
        self.save()


class MeteredLLM(LLMClient):
    def __init__(self, settings, ledger):
        super().__init__(settings=settings)
        self.ledger = ledger
        self.pending = None

    def _record(self, resp, **kwargs):
        usage = super()._record(resp, **kwargs)
        provider_usage = getattr(resp, "usage", None)
        self.pending["provider_model"] = getattr(resp, "model", None)
        self.pending["provider_response_id"] = getattr(resp, "id", None)
        self.ledger.complete(self.pending, usage,
                             provider_usage.model_dump() if provider_usage is not None else None)
        return usage

    def chat_stream(self, messages, *, tools=None, stage="unspecified", **options):
        self.pending = self.ledger.begin(self._chat_kwargs(messages, tools=tools, **options))
        started = perf_counter()
        try:
            for item in super().chat_stream(messages, tools=tools, stage=stage, **options):
                if isinstance(item, str) and "first_content_ms" not in self.pending:
                    self.pending["first_content_ms"] = (perf_counter() - started) * 1000
                if not isinstance(item, str):
                    self.pending["finish_reason"] = item.finish_reason
                yield item
        except Exception as exc:
            self.pending["error_type"] = type(exc).__name__
            raise
        finally:
            if "charge_guard_cny" not in self.pending:
                self.ledger.complete(self.pending, None)
            self.ledger.save()


def run(cases_path: Path, output: Path, *, live: bool):
    frozen = json.loads(cases_path.read_text())
    original = get_settings()
    if live and (not original.has_llm_key or original.llm_model != "deepseek-flash"
                 or urlsplit(original.llm_base_url).hostname != "api.deepseek.com"):
        raise ValueError("Live budget only supports the configured official DeepSeek Flash")
    output.mkdir(parents=True, exist_ok=False)
    source_paths = [*sorted((ROOT / "src/acs").glob("*.py")), Path(__file__),
                    ROOT / "scripts/review_order_candidate_scope.py", ROOT / "tests/conftest.py"]
    manifest = {"live": live, "head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "case_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "sources_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in source_paths},
        "protocol": frozen["protocol"], "model": original.llm_model,
        "model_alias_revision_note": "Alias is not a pinned snapshot; record returned model",
        "api_host": urlsplit(original.llm_base_url).hostname,
        "seed": None, "seed_note": "Client has no seed parameter; temperature0 is not determinism",
        "knowledge_mode": "documents", "return_state_check_enabled": False,
        "max_tool_calls": 6, "turn_timeout_s": 90, "max_steps": 4,
        "context_max_chars": original.context_max_chars,
        "llm_max_output_tokens": 1024, "llm_temperature": 0, "llm_thinking": False,
        "max_retries": 0, "llm_timeout_s": 60}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    ledger = Ledger(output / "requests.json")

    def safe_json(value):
        encoded = json.dumps(value, ensure_ascii=False)
        if original.llm_api_key:
            encoded = encoded.replace(original.llm_api_key, "<redacted>")
        return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted-key>", encoded)

    def forbidden(*args, **kwargs):
        raise RuntimeError("No non-stream LLM, embedding or reranking API in this experiment")

    records = []
    with TemporaryDirectory(prefix="acs-p1-live-") as temp, patch.object(
        LLMClient, "chat", forbidden,
    ), patch.object(LLMClient, "embed", forbidden), patch.object(LLMClient, "rerank", forbidden), (
        patch("socket.socket.connect", forbidden) if not live else nullcontext()
    ), (patch("socket.socket.connect_ex", forbidden) if not live else nullcontext()):
        source = Path(temp) / "source"
        manifest["scenario"] = build_scenario(source)
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        assert data_fingerprint(source / "business.db") == frozen["source_fingerprint"]
        with (output / "raw.jsonl").open("x") as raw:
            for index, case in enumerate(frozen["cases"]):
                for enabled in ((False, True) if index % 2 == 0 else (True, False)):
                    if ledger.halted:
                        break
                    directory = Path(temp) / f"{case['id']}-{enabled}"
                    shutil.copytree(source, directory)
                    settings = scenario_settings(original.model_copy(update={
                        "order_candidate_render_enabled": enabled,
                        "return_state_check_enabled": False, "llm_temperature": 0,
                        "llm_thinking": False, "llm_max_output_tokens": 1024,
                        "llm_timeout_s": 60, "llm_assume_peak": True,
                        "max_steps": 4, "max_api_requests": 4, "max_tool_calls": 6,
                        "turn_timeout_s": 90, "reranker_enabled": False,
                        "embedding_api_key": "",
                    }), directory)
                    store = SessionStore(settings.db_path)
                    before = business_state(store)
                    sid = store.create("USER001")
                    service = ChatService(settings, store)
                    first_call = len(ledger.calls)
                    record = {"case": case["id"], "enabled": enabled, "live": live, "turns": []}
                    for turn_index, query in enumerate(case["turns"]):
                        # Rebuild the Agent each turn: subsequent turns restore actual SQLite state.
                        llm = MeteredLLM(settings, ledger) if live else ScriptedLLM([
                            ScriptedResponse(wants_tools=True, tool_calls=[
                                ScriptedToolCall("orders", "list_orders", "{}"),
                            ]), ScriptedResponse(text="离线连通检查完成。"),
                        ])
                        agent = CustomerServiceAgent(settings=settings, llm=llm,
                                                     registry=ToolRegistry(settings))
                        started, first_text = perf_counter(), None
                        try:
                            for update in service.run_turn(query, "USER001", sid, agent=agent):
                                if update.result.answer and first_text is None:
                                    first_text = (perf_counter() - started) * 1000
                            result = asdict(update.result)
                            snapshot = store.load(sid, "USER001")
                            record["turns"].append({
                                "query": query, "result": result, "task_view": update.task_view,
                                "elapsed_ms": (perf_counter() - started) * 1000,
                                "first_visible_text_ms": first_text,
                                "synchronized": (snapshot["last_result"]["answer"]
                                                 == agent.messages[-1]["content"]
                                                 == result["answer"]),
                                "restored": turn_index > 0,
                            })
                        except Exception as exc:
                            record["turns"].append({"query": query,
                                                    "error_type": type(exc).__name__})
                            ledger.halted = "turn_exception"
                        if ledger.halted:
                            break
                    record["business_unchanged"] = before == business_state(store)
                    record["paid_request_sequences"] = [
                        r["sequence"] for r in ledger.calls[first_call:]]
                    # Observe next input size on a separate SQLite copy using no paid LLM.
                    clone = Path(temp) / f"next-{case['id']}-{enabled}.db"
                    with closing(sqlite3.connect(settings.db_path)) as src, closing(
                        sqlite3.connect(clone)
                    ) as dst:
                        src.backup(dst)
                    next_settings = settings.model_copy(update={"db_path": clone})
                    fake = ScriptedLLM([ScriptedResponse(text="离线字符观察。")])
                    observer = CustomerServiceAgent(settings=next_settings, llm=fake,
                                                    registry=ToolRegistry(next_settings))
                    last = list(ChatService(next_settings).run_turn(
                        "我先看看，稍后再选。", "USER001", sid, agent=observer,
                    ))[-1]
                    record["offline_next_input_chars"] = last.result.totals["model_input_chars"][0]
                    record["offline_observer_usage_excluded"] = True
                    records.append(record)
                    raw.write(safe_json(record) + "\n")
                    raw.flush()
                    print(json.dumps({"case": case["id"], "enabled": enabled,
                                      "paid_requests": len(ledger.calls),
                                      "budget_guard_cny": str(ledger.spent),
                                      "halted": ledger.halted}), flush=True)
    ledger.save()
    summary = {"completed_conversations": len(records), "planned_conversations": 16,
               "paid_requests": len(ledger.calls), "guard_cny": str(ledger.spent),
               "halted": ledger.halted, "live": live,
               "all_business_unchanged": all(r["business_unchanged"] for r in records),
               "manual_review": "pending; do not use fake-format audit as a natural-language judge"}
    (output / "summary.json").write_text(safe_json(summary) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Allow metered paid requests")
    args = parser.parse_args()
    run(args.cases, args.out, live=args.live)
