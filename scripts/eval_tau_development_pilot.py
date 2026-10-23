"""Frozen, serial 33/34 Development Pilot. Requires offline gates, never retries.

No default paid execution: --freeze records conditions; --run consumes that exact
manifest once. User authorization for this experiment: <=5 CNY and <=48 HTTP calls.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from check_tau_langgraph_adapter import digest, verify_upstream  # noqa: E402

from acs.config import Settings  # noqa: E402
from acs.episode_budget import PRICE, EpisodeLedger, EpisodeLimits  # noqa: E402

ARMS = ["acs_loop_receipt_guard", "langgraph"]
LIMITS = EpisodeLimits(requests=24, tools=24, steps=80, episode_timeout_s=300)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def fingerprints():
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted([
        *ROOT.joinpath("src/acs").glob("*.py"),
        *ROOT.joinpath("scripts").glob("tau_*.py"), Path(__file__),
        ROOT / "tests/test_episode_budget.py",
    ])}


def validate_gates(path):
    # Gate evidence must be from this exact source, not an old green XML file.
    gate = json.loads(path.read_text())
    if gate["source"] != fingerprints():
        raise ValueError("Offline evidence belongs to different source")
    for name in ("pytest", "ruff", "secrets"):
        if gate[name]["exit_code"] != 0:
            raise ValueError("Offline gate failed: " + name)
    suite = ET.parse(path.parent / gate["pytest"]["junit"]).getroot()
    if (not suite.findall(".//testcase") or any(suite.findall(".//" + tag)
            for tag in ("failure", "error", "skipped"))):
        raise ValueError("Tests failed, empty or silently skipped")


def manifest(upstream):
    return {"scope": "Development Pilot, exposed tasks; no generalization or pass^k claim",
            "upstream": verify_upstream(upstream), "order": [
                {"task": task, "arm": arm} for task in ("33", "34") for arm in ARMS],
            "roles": {r: "deepseek-flash" for r in ("agent", "user", "judge")},
            "sampling": {"temperature": 0, "max_tokens": 1024, "thinking": "disabled",
                         "max_retries": 0},
            "limits": asdict(LIMITS), "global_requests": 48, "global_cap_cny": 5,
            "workers": 1, "seed": 20261023, "evaluation": "official ALL / strict_replay",
            "receipt_guard": "enabled for BOTH arms; old default is unchanged",
            "price": PRICE, "source": fingerprints(), "python": sys.version,
            "packages": {p: importlib.metadata.version(p) for p in (
                "tau2", "openai", "litellm", "langchain", "langgraph", "httpx")}}


def attach_host_hooks(orchestrator, ledger, episode, out):
    """Instrument this instance; original step, environment and scorer run unchanged."""
    original_step = orchestrator.step
    original_execute = orchestrator.environment.get_response

    def execute(call):
        ledger.remaining(episode)
        ledger.event(episode, "tool_execute_started", call=call.model_dump(mode="json"))
        reply = original_execute(call)
        ledger.event(episode, "tool_execute_returned", receipt=reply.model_dump(mode="json"))
        return reply

    def step():
        ledger.step(episode)
        try:
            return original_step()
        finally:
            write_json(out / "partial.json", {
                "messages": [m.model_dump(mode="json") for m in orchestrator.get_trajectory()],
                "db_hash": orchestrator.environment.get_db_hash(),
                "step_count": orchestrator.step_count,
                "note": "diagnostic only; not an atomic DB/checkpoint restore image"})

    orchestrator.environment.get_response = execute
    orchestrator.step = step


def price_preflight(settings, out):
    """Free official GETs; no chat request. Save hashes + numeric tariff, no credentials."""
    import httpx

    snapshots = {}
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for locale, url in (("cny", PRICE["source"]), ("usd", PRICE["usd_source"])):
            response = client.get(url)
            response.raise_for_status()
            snapshots[locale] = {"url": url, "sha256": hashlib.sha256(response.content).hexdigest(),
                                 "retrieved_at": time.time()}
            # Snapshot fingerprint alone does not verify numbers. Operator must supply
            # this session's reviewed tariff record with the corresponding content hash.
            reviewed = json.loads((out / "price_review.json").read_text())
            if reviewed["tariff"] != PRICE:
                raise ValueError("Reviewed tariff differs from frozen price")
            if reviewed[locale]["sha256"] != snapshots[locale]["sha256"]:
                raise ValueError("Official pricing changed since review; no paid HTTP allowed")
        balance = client.get("https://api.deepseek.com/user/balance", headers={
            "Authorization": "Bearer " + settings.llm_api_key})
        balance.raise_for_status()
        data = balance.json()
    cny = [r for r in data["balance_infos"] if r["currency"] == "CNY"]
    if not data["is_available"] or not cny or sum(
            Decimal(r["total_balance"]) for r in cny) < 5:
        raise ValueError("CNY account and >=5 CNY available are required; no unverified FX")
    write_json(out / "price_preflight.json", {"sources": snapshots, "tariff": PRICE,
        "currency": "CNY", "sufficient_balance": True,
        "note": "Balance amount/credentials excluded; estimates differ from provider invoice."})


def run(out, upstream, env_file):
    validate_gates(out / "gates.json")
    frozen = json.loads((out / "manifest.json").read_text())
    if frozen != manifest(upstream):
        raise ValueError("Frozen configuration/source/upstream differs")
    # A durable exclusive marker forbids automatic restarts/reruns of paid episodes.
    # Recovery APIs/tests retain budgets; reconciliation of live runs is manual.
    with (out / "RUN_STARTED").open("x") as started:
        started.write(str(time.time()))
    settings = Settings(_env_file=env_file)
    if settings.llm_model != "deepseek-flash" or settings.llm_base_url.rstrip("/") not in {
            "https://api.deepseek.com", "https://api.deepseek.com/v1"}:
        raise ValueError("Pilot requires the verified direct DeepSeek model/endpoint")
    settings = settings.model_copy(update={"llm_temperature": 0, "llm_max_output_tokens": 1024,
        "llm_thinking": False, "llm_timeout_s": LIMITS.model_timeout_s})
    price_preflight(settings, out)
    os.environ["TAU2_DATA_DIR"] = str(upstream / "data")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="CRITICAL")
    from tau2.domains.retail.environment import get_environment, get_tasks
    from tau2.evaluator import evaluator_nl_assertions
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.runner.build import build_user
    from tau2.runner.simulation import run_simulation
    from tau_episode import make_agent, sync_client

    ledger = EpisodeLedger(out / "ledger.jsonl")
    tasks = {t.id: t for t in get_tasks("base")}
    scorecard, halt = [], False
    for item in frozen["order"]:
        episode = item["task"] + "-" + item["arm"]
        entry = {"episode": episode, **item, "reward": None, "db_reward": None}
        if halt:
            scorecard.append(entry | {"status": "not_started_after_failure_or_budget_stop"})
            continue
        episode_out = out / "intermediate" / episode
        episode_out.mkdir(parents=True, exist_ok=False)
        ledger.begin(episode, LIMITS)
        started = time.perf_counter()
        environment = get_environment()
        initial_db = environment.get_db_hash()
        with ExitStack() as stack:
            clients = {role: stack.enter_context(sync_client(settings, ledger, episode, role))
                       for role in ("user", "judge")}
            llm_args = {"temperature": 0, "max_tokens": 1024, "num_retries": 0,
                        "extra_body": {"thinking": {"type": "disabled"}}, "timeout": 60}
            user = build_user("user_simulator", environment, tasks[item["task"]],
                              llm="openai/deepseek-flash",
                              llm_args=llm_args)
            # LLMConfigMixin deepcopies plain configuration at construction. SDK
            # clients own locks and must be attached after that copy, never pickled.
            user.llm_args["client"] = clients["user"]
            evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = "openai/deepseek-flash"
            evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = llm_args | {
                "client": clients["judge"]}
            agent = make_agent(item["arm"], environment, settings, ledger, episode,
                               episode_out / "graph.sqlite")
            orchestrator = Orchestrator("retail", agent, user, environment, tasks[item["task"]],
                max_steps=LIMITS.steps, max_errors=10, seed=frozen["seed"],
                timeout=LIMITS.episode_timeout_s)
            attach_host_hooks(orchestrator, ledger, episode, episode_out)
            try:
                result = run_simulation(orchestrator)
                raw = result.model_dump(mode="json")
                # Upstream LiteLLM can return cost=0 for an unrecognized tariff. Replace
                # exported USER cost/usage with HTTP receipts, preserving message contents.
                user_ends = iter(r for r in ledger.rows(episode)
                    if r["type"] == "request_end" and r["role"] == "user")
                for message in raw["messages"]:
                    if message["role"] == "user" and message.get("usage"):
                        receipt = next(user_ends)
                        message["usage"] = {k: receipt["usage"][k]
                            for k in ("prompt_tokens", "completion_tokens")}
                        message["cost"] = (float(receipt["estimated_usd"])
                            if receipt["estimated_usd"] is not None else None)
                raw["user_cost"] = sum(m.get("cost") or 0 for m in raw["messages"]
                                       if m["role"] == "user")
                entry.update(status="evaluated", reward=result.reward_info.reward,
                    db_reward=(result.reward_info.reward_breakdown or {}).get("DB"),
                    reward_info=result.reward_info.model_dump(mode="json"),
                    termination=result.termination_reason.value)
            except Exception as exc:
                # No retry and no fabricated zero reward for an unscored run.
                entry.update(status="failed_unscored", error_type=type(exc).__name__,
                    error=str(exc).replace(settings.llm_api_key, "[REDACTED]")[:1000])
                raw = {"messages": [m.model_dump(mode="json")
                                    for m in orchestrator.get_trajectory()],
                       "error": entry["error"]}
                halt = True
            finally:
                agent.stop(state=getattr(orchestrator, "agent_state", None))
            with gzip.open(out / (episode + ".trace.json.gz"), "wt", encoding="utf-8") as trace:
                json.dump(raw, trace, ensure_ascii=False)
        entry.update(duration_s=time.perf_counter()-started, initial_db=initial_db,
                     final_db=environment.get_db_hash(), accounting=ledger.summary(episode),
                     tool_executions=sum(r["type"] == "tool_execute_started"
                                         for r in ledger.rows(episode)))
        if entry["accounting"]["stop_reasons"] or ledger.summary()["requests"] >= 48:
            halt = True
        if entry["accounting"]["stop_reasons"]:
            entry["status"] = "stopped_" + entry["accounting"]["stop_reasons"][0]
        scorecard.append(entry)
        write_json(out / "scorecard.json", {"scope": frozen["scope"], "episodes": scorecard,
                                              "accounting": ledger.summary()})
        print(json.dumps({k: entry[k] for k in ("episode", "status", "reward", "db_reward")}),
              flush=True)
    write_json(out / "scorecard.json", {"scope": frozen["scope"], "episodes": scorecard,
                                          "accounting": ledger.summary()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.freeze:
        args.out.mkdir(parents=True, exist_ok=False)
        write_json(args.out / "manifest.json", manifest(args.upstream.resolve()))
        print("Frozen 4 development episodes; paid HTTP requests: 0")
    else:
        if args.env_file is None:
            parser.error("--run requires an explicit --env-file (never copied into artifacts)")
        run(args.out, args.upstream.resolve(), args.env_file)


if __name__ == "__main__":
    main()
