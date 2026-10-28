"""Shared episode accounting for the pinned tau pilot; never business-state evidence.

Reuse RunBudget's exception and PocBudget's non-refundable reservations/journal.
One POSIX-locked journal contains every role and episode of ONE frozen experiment.
No transport is allowed to send a request until its reservation is durable.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from acs.config import is_peak_now
from acs.runtime import BudgetExceeded, BudgetJournal


@dataclass(frozen=True)
class EpisodeLimits:
    requests: int = 12  # All roles, including failures/attempts.
    tools: int = 12
    steps: int = 40
    model_timeout_s: float = 60
    episode_timeout_s: float = 240


# Official CNY tariff checked 2026-10-23; reserves always use PEAK cache-miss.
# The pilot requires a saved official price snapshot before enabling real HTTP.
PRICE = {
    "model": "deepseek-flash",
    "checked": "2026-10-23",
    "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    "peak_input_cny_per_million": 2,
    "peak_cache_cny_per_million": 0.04,
    "peak_output_cny_per_million": 8,
    "offpeak_input_cny_per_million": 1,
    "offpeak_cache_cny_per_million": 0.02,
    "offpeak_output_cny_per_million": 4,
    "usd_source": "https://api-docs.deepseek.com/quick_start/pricing/",
    "peak_input_usd_per_million": 0.3,
    "peak_cache_usd_per_million": 0.006,
    "peak_output_usd_per_million": 1.2,
}


class EpisodeLedger:
    def __init__(self, path, *, cap_cny=5, max_requests=48, clock=time.time,
                 uncapped_authorization=None):
        uncapped = cap_cny is None and max_requests is None
        if uncapped and (not isinstance(uncapped_authorization, str)
                         or not uncapped_authorization.strip()):
            raise ValueError("Uncapped accounting requires explicit human authorization")
        if not uncapped and (cap_cny is None or max_requests is None
                             or not 0 < cap_cny <= 5 or not 0 < max_requests <= 48):
            raise ValueError("Pilot limits cannot exceed 5 CNY / 48 HTTP requests")
        self.journal, self.clock = BudgetJournal(path), clock
        policy = {"type": "policy", "cap_cny": cap_cny, "max_requests": max_requests,
                  "price": PRICE, "reservation_refundable": False,
                  "input_bound": "2 * actual serialized HTTP UTF-8 bytes + 4096"}
        if uncapped:
            policy.update(quota_mode="uncapped", user_instruction_reference=uncapped_authorization)
        with self.journal.transaction() as (rows, append):
            if not rows:
                append(policy)
            elif rows[0] != policy:
                raise ValueError("Frozen ledger policy differs")
        self.cap_cny = None if uncapped else Decimal(str(cap_cny))
        self.max_requests = max_requests

    def begin(self, episode, limits=None):
        limits = limits or EpisodeLimits()
        if min(asdict(limits).values()) <= 0:
            raise ValueError("Episode limits must be positive")
        with self.journal.transaction() as (rows, append):
            previous = [r for r in rows if r["type"] == "episode" and r["episode"] == episode]
            if previous:
                if previous[0]["limits"] != asdict(limits):
                    raise ValueError("Cannot reset or change a resumed episode's limits")
            else:
                now = self.clock()
                append({"type": "episode", "episode": episode, "limits": asdict(limits),
                        "started": now, "deadline": now + limits.episode_timeout_s})

    def _state(self, rows, episode):
        return next(r for r in rows if r["type"] == "episode" and r["episode"] == episode)

    def restrict_window(self, name, *, added_cny, added_requests):
        """A stricter persisted allowance in THIS journal; never a fresh ledger/deadline."""
        amount = Decimal(str(added_cny))
        if (not name or not amount.is_finite() or amount <= 0
                or type(added_requests) is not int or added_requests <= 0):
            raise ValueError("Positive incremental limits and a stable name are required")
        with self.journal.transaction() as (rows, append):
            previous = next((r for r in rows if r["type"] == "restriction"
                             and r["name"] == name), None)
            if previous:
                if (previous["added_cny"], previous["added_requests"]) != (
                        str(amount), added_requests):
                    raise ValueError("Cannot reset an existing allowance")
                return previous
            attempts = [r for r in rows if r["type"] == "reserved"]
            total = sum((Decimal(r["reserved_cny"]) for r in attempts), Decimal(0))
            row = {"type": "restriction", "name": name, "added_cny": str(amount),
                   "added_requests": added_requests, "baseline_requests": len(attempts),
                   "baseline_reserved_cny": str(total),
                   "request_ceiling": (min(self.max_requests, len(attempts) + added_requests)
                                       if self.max_requests is not None
                                       else len(attempts) + added_requests),
                   "reserved_ceiling_cny": str(min(self.cap_cny, total + amount)
                                               if self.cap_cny is not None else total + amount)}
            append(row)
            return row

    def _check(self, rows, append, episode):
        stopped = [r for r in rows if r["type"] == "stop" and r["episode"] == episode]
        if stopped:
            raise BudgetExceeded(stopped[0]["reason"])
        state = self._state(rows, episode)
        if self.clock() >= state["deadline"]:
            self._stop(append, episode, "episode_deadline")
        return state

    def _stop(self, append, episode, reason):
        append({"type": "stop", "episode": episode, "reason": reason, "time": self.clock()})
        raise BudgetExceeded(reason)

    def remaining(self, episode):
        with self.journal.transaction() as (rows, append):
            state = self._check(rows, append, episode)
            return min(state["deadline"] - self.clock(), state["limits"]["model_timeout_s"])

    def reserve(self, episode, role, body):
        if role not in {"agent", "user", "judge"}:
            raise ValueError("Unknown paid role")
        payload = json.loads(body)
        if payload.get("model") != PRICE["model"]:
            raise ValueError("Only the verified deepseek-flash tariff is allowed")
        output = payload.get("max_tokens", payload.get("max_completion_tokens"))
        if (payload.get("n", 1) != 1 or (payload.get("thinking") or {}).get(
                "type", "disabled") != "disabled" or (
                "max_tokens" in payload and "max_completion_tokens" in payload)):
            raise ValueError("Single non-thinking completion and one output cap required")
        if not isinstance(output, int) or not 0 < output <= 1024:
            raise ValueError("Explicit 1..1024 output cap required")
        bound = 2 * len(body) + 4096
        cost = (Decimal(bound) * 2 + Decimal(output) * 8) / 1_000_000
        with self.journal.transaction() as (rows, append):
            state = self._check(rows, append, episode)
            attempts = [r for r in rows if r["type"] == "reserved"]
            if self.max_requests is not None and len(attempts) >= self.max_requests:
                self._stop(append, episode, "global_request_limit")
            if sum(r["episode"] == episode for r in attempts) >= state["limits"]["requests"]:
                self._stop(append, episode, "episode_request_limit")
            total = sum((Decimal(r["reserved_cny"]) for r in attempts), Decimal(0))
            if self.cap_cny is not None and total + cost > self.cap_cny:
                self._stop(append, episode, "global_cost_limit")
            for restriction in (r for r in rows if r["type"] == "restriction"):
                if len(attempts) >= restriction["request_ceiling"]:
                    self._stop(append, episode, "phase_request_limit")
                if total + cost > Decimal(restriction["reserved_ceiling_cny"]):
                    self._stop(append, episode, "phase_cost_limit")
            row = {"type": "reserved", "episode": episode, "role": role,
                   "request_id": uuid4().hex, "model": payload["model"], "time": self.clock(),
                   "input_token_bound": bound, "output_token_bound": output,
                   "body_sha256": hashlib.sha256(body).hexdigest(), "reserved_cny": str(cost)}
            append(row)
            return row

    def finish(self, reservation, *, status, usage=None, latency_ms=0):
        with self.journal.transaction() as (rows, append):
            rid, episode = reservation["request_id"], reservation["episode"]
            if any(r["type"] == "request_end" and r["request_id"] == rid for r in rows):
                raise ValueError("Duplicate HTTP receipt")
            row = {"type": "request_end", "request_id": rid, "episode": episode,
                   "role": reservation["role"], "status": status, "usage": usage,
                   "latency_ms": latency_ms, "time": self.clock(), "estimated_cny": None,
                   "estimated_usd": None}
            valid = (isinstance(usage, dict) and all(
                type(usage.get(k)) is int and usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")))
            if status == "completed" and valid:
                prompt, output = usage["prompt_tokens"], usage["completion_tokens"]
                cache = usage.get("prompt_cache_hit_tokens", (
                    usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
                if (type(cache) is not int or not 0 <= cache <= prompt
                        or prompt > reservation["input_token_bound"]
                        or output > reservation["output_token_bound"]):
                    row["status"] = "invalid_usage"
                else:
                    row["cached_tokens"] = cache
                    # Conservative PEAK estimate; actual bill may apply offpeak/discounts.
                    row["estimated_cny"] = str((Decimal(prompt-cache)*2
                        + Decimal(cache)*Decimal("0.04") + Decimal(output)*8) / 1_000_000)
                    row["estimated_usd"] = str((Decimal(prompt-cache)*Decimal("0.3")
                        + Decimal(cache)*Decimal("0.006")
                        + Decimal(output)*Decimal("1.2")) / 1_000_000)
                    peak = is_peak_now(datetime.fromtimestamp(reservation["time"], UTC))
                    row["timeband"] = "peak" if peak else "offpeak"
                    row["timeband_estimated_cny"] = str(Decimal(row["estimated_cny"])
                        * (Decimal(1) if peak else Decimal("0.5")))
            elif status == "completed":
                row["status"] = "usage_missing"
            append(row)
            if row["status"] in {"usage_missing", "invalid_usage"}:
                self._stop(append, episode, row["status"])
            elif status != "completed":
                state = self._state(rows, episode)
                reason = ("episode_deadline" if self.clock() >= state["deadline"] else
                          "model_timeout" if status == "TimeoutError" else
                          "model_interrupted" if status == "CancelledError" else
                          "model_request_failed")
                append({"type": "stop", "episode": episode, "reason": reason,
                        "time": self.clock()})

    def event(self, episode, kind, **fields):
        with self.journal.transaction() as (_, append):
            append({"type": kind, "episode": episode, "time": self.clock(), **fields})

    def stop(self, episode, reason):
        with self.journal.transaction() as (rows, append):
            if not any(r["type"] == "stop" and r["episode"] == episode for r in rows):
                append({"type": "stop", "episode": episode, "reason": reason,
                        "time": self.clock()})

    def step(self, episode):
        with self.journal.transaction() as (rows, append):
            state = self._check(rows, append, episode)
            used = sum(r["type"] == "step" and r["episode"] == episode for r in rows)
            if used >= state["limits"]["steps"]:
                self._stop(append, episode, "orchestrator_step_limit")
            append({"type": "step", "episode": episode, "number": used+1, "time": self.clock()})

    def tool_intent(self, episode, call):
        with self.journal.transaction() as (rows, append):
            state = self._check(rows, append, episode)
            calls = [r for r in rows if r["type"] == "tool_intent" and r["episode"] == episode]
            if any(r["call"]["id"] == call["id"] for r in calls):
                self._stop(append, episode, "state_uncertain")
            if len(calls) >= state["limits"]["tools"]:
                self._stop(append, episode, "episode_tool_limit")
            append({"type": "tool_intent", "episode": episode, "call": call, "time": self.clock()})

    def tool_receipt(self, episode, receipt):
        with self.journal.transaction() as (rows, append):
            relevant = [r for r in rows if r.get("episode") == episode]
            intent = next((r for r in relevant if r["type"] == "tool_intent"
                           and r["call"]["id"] == receipt["id"]), None)
            if (intent is None or receipt.get("requestor", "assistant") != intent[
                    "call"].get("requestor", "assistant") or any(r["type"] == "tool_receipt"
                        and r["receipt"]["id"] == receipt["id"] for r in relevant)):
                self._stop(append, episode, "adapter_protocol_error")
            append({"type": "tool_receipt", "episode": episode, "receipt": receipt,
                    "time": self.clock()})

    def rows(self, episode=None):
        return [r for r in self.journal.rows() if episode is None or r.get("episode") == episode]

    def summary(self, episode=None):
        rows = self.rows(episode)
        reservations = [r for r in rows if r["type"] == "reserved"]
        ends = [r for r in rows if r["type"] == "request_end"]
        return {"requests": len(reservations),
                "reserved_cny": str(sum((Decimal(r["reserved_cny"])
                                         for r in reservations), Decimal(0))),
                "known_peak_estimated_cny": str(sum((Decimal(r["estimated_cny"])
                    for r in ends if r["estimated_cny"] is not None), Decimal(0))),
                "known_timeband_estimated_cny": str(sum((Decimal(r["timeband_estimated_cny"])
                    for r in ends if r.get("timeband_estimated_cny") is not None), Decimal(0))),
                "cost_complete": len(ends) == len(reservations) and all(
                    r["estimated_cny"] is not None for r in ends),
                "unknown_requests": len(reservations) - sum(
                    r["estimated_cny"] is not None for r in ends),
                "roles": {role: {"requests": sum(r["role"] == role for r in reservations),
                    "reserved_cny": str(sum((Decimal(r["reserved_cny"]) for r in reservations
                                              if r["role"] == role), Decimal(0))),
                    "known_peak_estimated_cny": str(sum((Decimal(r["estimated_cny"])
                        for r in ends if r["role"] == role and r["estimated_cny"] is not None),
                        Decimal(0))),
                    "unknown_requests": sum(r["role"] == role for r in reservations) - sum(
                        r["role"] == role and r["estimated_cny"] is not None for r in ends),
                    "cost_complete": sum(r["role"] == role for r in reservations) == sum(
                        r["role"] == role and r["estimated_cny"] is not None for r in ends),
                    "known_timeband_estimated_cny": str(sum((
                        Decimal(r["timeband_estimated_cny"]) for r in ends if r["role"] == role
                        and r.get("timeband_estimated_cny") is not None), Decimal(0))),
                    "cached_tokens": sum(r.get("cached_tokens", 0)
                                         for r in ends if r["role"] == role),
                    "prompt_tokens": sum((r["usage"] or {}).get("prompt_tokens", 0)
                        for r in ends if r["role"] == role),
                    "completion_tokens": sum((r["usage"] or {}).get("completion_tokens", 0)
                        for r in ends if r["role"] == role),
                    "latency_ms": sum(r["latency_ms"] for r in ends if r["role"] == role)}
                    for role in ("agent", "user", "judge")},
                "stop_reasons": [r["reason"] for r in rows if r["type"] == "stop"]}
