"""Frozen, budget-capped real ChatService A/B; dry run is mandatory before paid execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from scripts.eval_p1_live import Ledger, MeteredLLM  # noqa: E402
from scripts.review_order_candidate_scope import business_state  # noqa: E402
from tests.conftest import ScriptedLLM, ScriptedResponse, ScriptedToolCall  # noqa: E402

from acs.chat_service import ChatService  # noqa: E402
from acs.config import Settings, get_settings  # noqa: E402
from acs.cs_tools import ToolRegistry  # noqa: E402
from acs.llm import LLMClient  # noqa: E402
from acs.scenarios import build_scenario, scenario_settings  # noqa: E402
from acs.service_agent import CustomerServiceAgent  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402


class RecordedLLM(MeteredLLM):
    def chat_stream(self, *args, **kwargs):
        try:
            for value in super().chat_stream(*args, **kwargs):
                if not isinstance(value, str):
                    self.pending["raw_response_text"] = value.text
                yield value
        finally:
            self.ledger.save()


def run(cases_path, output, *, live, preflight):
    cases = json.loads(cases_path.read_text())
    case_hash = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    original = get_settings() if live else Settings(_env_file=None, llm_api_key="")
    if live:
        proof = json.loads((preflight / "summary.json").read_text())
        if (
            proof["case_sha256"] != case_hash
            or not proof["complete_first_trial"]
            or proof["live"]
            or not proof["all_business_unchanged"]
        ):
            raise ValueError("Matching successful no-network preflight required")
        if (
            not original.has_llm_key
            or original.llm_model != "deepseek-flash"
            or urlsplit(original.llm_base_url).hostname != "api.deepseek.com"
        ):
            raise ValueError(
                "Only the configured funded official DeepSeek Flash endpoint is allowed"
            )
    output.mkdir(parents=True, exist_ok=False)
    ledger = Ledger(output / "requests.json", limit=Decimal("1"), max_requests=200)
    files = sorted((ROOT / "src/acs").glob("*.py")) + [
        Path(__file__),
        ROOT / "scripts/eval_p1_live.py",
    ]
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if live and sources != json.loads((preflight / "manifest.json").read_text())["source_sha256"]:
        raise ValueError("Source changed after dry run")
    manifest = {
        "case_sha256": case_hash,
        "source_sha256": sources,
        "live": live,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "model": original.llm_model,
        "protocol": cases["protocol"],
        "seed": None,
        "p0": True,
        "p1": True,
        "max_retries": 0,
        "knowledge_mode": "documents",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    records = []

    def forbidden(*args, **kwargs):
        raise AssertionError("No non-stream API, embedding, reranker, or dry-run network")

    with (
        TemporaryDirectory(prefix="acs-delivery-ab-") as temp,
        patch.object(LLMClient, "chat", forbidden),
        patch.object(LLMClient, "embed", forbidden),
        patch.object(LLMClient, "rerank", forbidden),
        nullcontext() if live else patch.object(socket.socket, "connect", forbidden),
        nullcontext() if live else patch.object(socket.socket, "connect_ex", forbidden),
    ):
        source = Path(temp) / "source"
        build_scenario(source)
        max_trials = cases["protocol"]["max_trials"] if live else 1
        previous_charge = Decimal(0)
        for trial in range(1, max_trials + 1):
            if trial > 1 and ledger.limit - ledger.spent < previous_charge * Decimal(
                "1.2"
            ) + Decimal(".10"):
                manifest["repeat_stop"] = (
                    "Insufficient conservative budget for another complete trial"
                )
                break
            trial_start = ledger.spent
            for index, case in enumerate(cases["cases"]):
                for enabled in (False, True) if (index + trial) % 2 else (True, False):
                    if ledger.halted:
                        break
                    root = Path(temp) / f"{trial}-{case['id']}-{enabled}"
                    shutil.copytree(source, root)
                    settings = scenario_settings(original, root).model_copy(
                        update={
                            "return_state_check_enabled": True,
                            "order_candidate_render_enabled": True,
                            "order_constraint_enabled": enabled,
                            "order_aggregation_enabled": enabled,
                            "max_steps": 4,
                            "max_tool_calls": 6,
                            "max_api_requests": 4,
                            "turn_timeout_s": 90,
                            "llm_max_output_tokens": 1024,
                            "llm_thinking": False,
                            "llm_temperature": 0,
                            "llm_timeout_s": 60,
                            "embedding_api_key": "",
                        }
                    )
                    store = SessionStore(settings.db_path)
                    sid = store.create("USER001")
                    if case.get("preload_draft"):
                        raw = "我想退S20017。"
                        tid = store.begin_turn(sid, "USER001", raw)
                        store.update_return_task(sid, "USER001", tid, {"order_id": "S20017"})
                        store.finish_turn(
                            sid,
                            "USER001",
                            tid,
                            [
                                {"role": "user", "content": raw},
                                {"role": "assistant", "content": "草稿已记录，商品待确认。"},
                            ],
                            {"answer": "草稿已记录，商品待确认。"},
                        )
                    before = business_state(store)
                    row = {"case": case["id"], "trial": trial, "enabled": enabled, "turns": []}
                    first_call = len(ledger.calls)
                    for query in case["queries"]:
                        store = SessionStore(settings.db_path)
                        if live:
                            llm = RecordedLLM(settings, ledger)
                        else:
                            tool = (
                                "get_order"
                                if "查一下S20003" in query or "SKU" in query
                                else "list_orders"
                            )
                            args = (
                                '{"order_id":"S20003"}'
                                if "S20003" in query
                                else '{"order_id":"S20008"}'
                            )
                            responses = [ScriptedResponse(text="已收到。")]
                            if (
                                query.startswith("我想退")
                                or "统计" in query
                                or "多少" in query
                                or "SKU" in query
                                or "查一下" in query
                            ):
                                responses = [
                                    ScriptedResponse(
                                        wants_tools=True,
                                        tool_calls=[
                                            ScriptedToolCall(
                                                "lookup",
                                                tool,
                                                args if tool == "get_order" else "{}",
                                            )
                                        ],
                                    ),
                                    ScriptedResponse(text="S20013 黑色；25 件商品。"),
                                ]
                            llm = ScriptedLLM(responses)
                        agent = CustomerServiceAgent(
                            settings=settings, llm=llm, registry=ToolRegistry(settings)
                        )
                        started = perf_counter()
                        first_visible = None
                        frames = []
                        try:
                            for update in ChatService(settings, store).run_turn(
                                query, "USER001", sid, agent=agent
                            ):
                                if first_visible is None and update.result.answer:
                                    first_visible = (perf_counter() - started) * 1000
                                frames.append(
                                    {
                                        "phase": update.phase,
                                        "answer": update.result.answer,
                                        "history": update.history,
                                        "stop_reason": update.result.stop_reason,
                                    }
                                )
                            snapshot = store.load(sid, "USER001")
                            row["turns"].append(
                                {
                                    "query": query,
                                    "result": asdict(update.result),
                                    "frames": frames,
                                    "task": update.task_view,
                                    "first_visible_ms": first_visible,
                                    "elapsed_ms": (perf_counter() - started) * 1000,
                                    "synchronized": snapshot["last_result"]["answer"]
                                    == agent.messages[-1]["content"]
                                    == update.result.answer,
                                }
                            )
                        except Exception as exc:
                            row["turns"].append(
                                {"query": query, "error_type": type(exc).__name__, "frames": frames}
                            )
                            ledger.halted = "turn_exception"
                        finally:
                            if live:
                                llm._client.close()
                        if ledger.halted:
                            break
                    row["business_unchanged"] = before == business_state(store)
                    row["request_sequences"] = list(range(first_call + 1, len(ledger.calls) + 1))
                    if not row["business_unchanged"]:
                        ledger.halted = "unsafe_business_write"
                    records.append(row)
                    encoded = json.dumps(row, ensure_ascii=False)
                    if original.llm_api_key:
                        encoded = encoded.replace(original.llm_api_key, "<redacted>")
                    with (output / "raw.jsonl").open("a") as stream:
                        stream.write(encoded + "\n")
                    print(
                        case["id"],
                        trial,
                        enabled,
                        "requests",
                        len(ledger.calls),
                        "guard",
                        str(ledger.spent),
                        "halt",
                        ledger.halted,
                        flush=True,
                    )
                if ledger.halted:
                    break
            previous_charge = ledger.spent - trial_start
            if ledger.halted:
                break
    ledger.save()
    summary = {
        "live": live,
        "case_sha256": case_hash,
        "conversations": len(records),
        "complete_first_trial": sum(r["trial"] == 1 for r in records) == 2 * len(cases["cases"]),
        "all_business_unchanged": all(r["business_unchanged"] for r in records),
        "actual_requests": len(ledger.calls),
        "guard_spent_cny": str(ledger.spent),
        "halted": ledger.halted,
        "repeat_stop": manifest.get("repeat_stop"),
        "semantic_review": ("pending source-grounded review; dry fake usage is not real tokens"),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args()
    run(args.cases, args.out, live=args.live, preflight=args.preflight)
