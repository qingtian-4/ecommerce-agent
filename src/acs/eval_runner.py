"""Evaluation runner: run the golden cases and compute layered metrics.

Design rules, each chosen to keep a number interpretable:

* **Metrics are computed only where they apply.** A case with no
  `required_documents` does not count toward retrieval recall. Every metric
  reports `applicable`, `passed` and `coverage` together, because a rate without
  its denominator is how a report turns into a marketing claim.

* **`forbidden_tools` is three-state.** `[...]` declares prohibitions, `[]`
  declares "no prohibition for this case" (counts as a pass), and omitting the
  key means the dimension is *not evaluated* for that case. Collapsing the third
  state into a pass would inflate the rate; the log prints coverage explicitly.

* **Conjunctive success, per-dimension reporting.** `success` requires every
  applicable dimension to pass -- a weighted score hides which part broke, and
  ECom-Bench's multiplicative reward cannot attribute failure at all. But each
  dimension is also reported on its own so a failure distribution is meaningful.

* **Passes are recorded, not averaged away.** With `--passes 3` the runner also
  reports `pass^3` (all three attempts succeed) and a flip rate (attempts
  disagree), because single observations cannot distinguish an unstable agent
  from a noisy one -- and instability is already suspected here.

No production code is modified by this module; it only reads what the agent
already produces.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# This file lives in src/acs/, so the project root is parents[2]. Getting this
# wrong is easy and fails at import time with a confusing path, so it is spelled
# out rather than inlined.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = PROJECT_ROOT / "data" / "eval" / "cases.yaml"
SCORING_VERSION = 2

from acs.config import Settings, get_settings  # noqa: E402
from acs.service_agent import SYSTEM_PROMPT, AgentResult, CustomerServiceAgent  # noqa: E402


# --------------------------------------------------------------------------- #
# what the agent actually did, extracted from the trace
# --------------------------------------------------------------------------- #
@dataclass
class Observed:
    """Facts derived purely from the trace and the answer."""

    tool_sequence: list[str] = field(default_factory=list)
    tool_args: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    retrieved_document_ids: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    retrieved_scores: list[float] = field(default_factory=list)
    stop_reason: str = ""
    answer: str = ""
    errors: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    total_tokens: int = 0
    cost_cny: str = "0"
    error_types: list[str] = field(default_factory=list)
    api_calls: int = 0
    unpriced_models: list[str] = field(default_factory=list)
    usages: list[dict[str, Any]] = field(default_factory=list)


def observe(result: AgentResult) -> Observed:
    """Extract an `Observed` from one AgentResult.

    Tool arguments come back as the raw JSON *string* the model produced, because
    that is what the trace stores. Parsing here (rather than in the agent) keeps
    the agent simple and makes a malformed argument visible as a parse failure in
    the evaluation instead of an exception mid-run.
    """
    observed = Observed(
        stop_reason=result.stop_reason,
        answer=result.answer,
        errors=list(result.errors),
        latency_ms=float(result.totals.get("latency_ms", 0.0) or 0.0),
        total_tokens=int(result.totals.get("total_tokens", 0) or 0),
        cost_cny=str(result.totals.get("cost_cny", "0")),
        api_calls=int(result.totals.get("calls", 0)),
        unpriced_models=list(result.totals.get("unpriced_models", [])),
        usages=list(result.totals.get("usages", [])),
    )

    for event in result.trace:
        if event.type == "tool_call":
            observed.tool_sequence.append(event.label)
            raw = event.detail.get("arguments", "{}")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = {"__unparsable__": raw}
            observed.tool_args.setdefault(event.label, []).append(parsed)
        elif event.type == "retrieval":
            for doc in event.detail.get("documents", []):
                observed.retrieved_document_ids.append(str(doc.get("document_id", "")))
                observed.retrieved_chunk_ids.append(str(doc.get("chunk_id", "")))
                observed.retrieved_scores.append(float(doc.get("score", 0.0)))
        elif event.type == "error":
            observed.error_types.append(str(event.detail.get("error_type", "unknown")))

    return observed


# --------------------------------------------------------------------------- #
# per-case scoring
# --------------------------------------------------------------------------- #
@dataclass
class Score:
    """Per-dimension results. `None` means not applicable for this case."""

    tools_complete: bool | None = None       # required_tools subset of observed
    tools_exact: bool | None = None          # no extra and no missing calls
    no_forbidden_tools: bool | None = None   # None when the case does not declare
    args_ok: bool | None = None
    order_ok: bool | None = None             # observed order matches required order
    retrieval_recall: bool | None = None     # all required docs retrieved
    retrieval_mrr: float | None = None
    containment_ok: bool | None = None
    forbidden_text_ok: bool | None = None
    stop_reason_ok: bool | None = None
    no_errors: bool | None = None
    # `success` is the end-to-end answer: right tools, no forbidden calls, right
    # arguments, retrieved what was needed, said the right things, finished clean.
    success: bool = False
    # `strict_success` additionally forbids redundant calls. Reported separately
    # because the headline metric saturates at 1.0 while waste is still
    # measurable -- and waste is what RQ-A is about.
    strict_success: bool = False
    failure_reasons: list[str] = field(default_factory=list)
    strict_failure_reasons: list[str] = field(default_factory=list)


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def _text_pattern(needle: str) -> str:
    text = _normalise_text(needle)
    pattern = re.escape(text)
    return rf"(?<!\d){pattern}(?!\d)" if text.isdigit() else pattern


# Characters that, immediately before a `must_not_contain` phrase, invert its
# meaning. "不在保修范围内" must not count as "在保修范围内".
_NEGATIONS = ("不", "非", "未", "无", "没", "别", "勿")
_NEGATION_WINDOW = 2  # A local heuristic, not a semantic parser.


def _contains_unnegated(haystack: str, needle: str) -> bool:
    """True if `needle` appears WITHOUT an immediately preceding negation.

    A deliberate heuristic. It catches the common Chinese negation patterns that
    a substring test trips over; it is not a parser. See the module docstring in
    docs/EVAL_PLAN.md for the stated limitation.
    """
    low = _normalise_text(haystack)
    for match in re.finditer(_text_pattern(needle), low):
        idx = match.start()
        window = low[max(0, idx - _NEGATION_WINDOW) : idx]
        if not any(neg in window for neg in _NEGATIONS):
            return True
    return False


def score_case(case: dict[str, Any], observed: Observed) -> Score:
    score = Score()
    reasons: list[str] = []

    required = list(case.get("required_tools", []))
    actual = list(observed.tool_sequence)
    actual_set, required_set = set(actual), set(required)

    # --- tools ---------------------------------------------------------- #
    score.tools_complete = required_set.issubset(actual_set)
    if not score.tools_complete:
        reasons.append(f"missing_tools={sorted(required_set - actual_set)}")

    extra = [t for t in actual if t not in required_set]
    score.tools_exact = Counter(actual) == Counter(required)
    if extra:
        reasons.append(f"extra_tools={sorted(set(extra))}")

    # Three-state semantics: absent key -> dimension not evaluated.
    if "forbidden_tools" in case:
        forbidden = set(case.get("forbidden_tools") or [])
        violations = actual_set & forbidden
        score.no_forbidden_tools = not violations
        if violations:
            reasons.append(f"forbidden_tools_called={sorted(violations)}")

    # --- tool arguments ------------------------------------------------- #
    required_args = case.get("required_tool_args") or {}
    if required_args:
        ok = True
        for tool, expected_args in required_args.items():
            calls = observed.tool_args.get(tool, [])
            if not calls:
                ok = False
                reasons.append(f"no_call_for_args:{tool}")
                continue
            # Pass if ANY call to that tool carries the expected arguments.
            if not any(
                all(str(call.get(k)) == str(v) for k, v in expected_args.items())
                for call in calls
            ):
                ok = False
                reasons.append(f"wrong_args:{tool}")
        score.args_ok = ok

    # --- call order ----------------------------------------------------- #
    if len(required) >= 2:
        # Only the relative order of required tools is checked; extra calls are
        # ignored here so this dimension stays independent of tools_exact.
        filtered = [t for t in actual if t in required_set]
        deduped: list[str] = []
        for t in filtered:
            if t not in deduped:
                deduped.append(t)
        score.order_ok = deduped == required
        if not score.order_ok:
            reasons.append(f"order={deduped}!={required}")

    # --- retrieval ------------------------------------------------------ #
    required_docs = list(case.get("required_documents", []))
    if required_docs:
        hit_ids = observed.retrieved_document_ids
        score.retrieval_recall = all(d in hit_ids for d in required_docs)
        if not score.retrieval_recall:
            reasons.append(f"missing_docs={[d for d in required_docs if d not in hit_ids]}")
        ranks = [hit_ids.index(d) + 1 for d in required_docs if d in hit_ids]
        score.retrieval_mrr = round(1.0 / min(ranks), 4) if ranks else 0.0

    # --- answer content ------------------------------------------------- #
    must = list(case.get("must_contain", []))
    alternatives = list(case.get("must_contain_any", []))
    if must or alternatives:
        missing = [m for m in must if not _contains_unnegated(observed.answer, m)]
        missing.extend(
            f"one_of={group}"
            for group in alternatives
            if not any(_contains_unnegated(observed.answer, m) for m in group)
        )
        score.containment_ok = not missing
        if missing:
            reasons.append(f"missing_text={missing}")

    must_not = list(case.get("must_not_contain", []))
    if must_not:
        # Negation-aware: "不在保修范围内" must not trip "在保修范围内".
        present = [m for m in must_not if _contains_unnegated(observed.answer, m)]
        score.forbidden_text_ok = not present
        if present:
            reasons.append(f"forbidden_text={present}")

    # --- control -------------------------------------------------------- #
    if case.get("expected_stop_reason"):
        score.stop_reason_ok = observed.stop_reason == case["expected_stop_reason"]
        if not score.stop_reason_ok:
            reasons.append(f"stop_reason={observed.stop_reason}")
    elif observed.stop_reason == "max_steps":
        score.stop_reason_ok = False
        reasons.append("stop_reason=max_steps")

    score.no_errors = not observed.errors
    if not score.no_errors:
        reasons.append(f"errors={observed.error_types or ['unknown']}")

    # --- conjunctive success -------------------------------------------- #
    checks = [
        score.tools_complete,
        score.no_forbidden_tools,
        score.args_ok,
        score.retrieval_recall,
        score.containment_ok,
        score.forbidden_text_ok,
        score.stop_reason_ok,
        score.no_errors,
    ]
    score.success = all(c for c in checks if c is not None)
    score.failure_reasons = reasons

    # Strict: the same, plus no redundant calls at all.
    redundant = max(0, len(actual) - len(required))
    strict_checks = [*checks, score.tools_exact]
    score.strict_success = all(c for c in strict_checks if c is not None)
    score.strict_failure_reasons = list(reasons)
    if redundant:
        score.strict_failure_reasons.append(f"redundant_calls={redundant}")
    return score


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _rate(values: list[bool]) -> dict[str, Any]:
    if not values:
        return {"applicable": 0, "passed": 0, "rate": None}
    passed = sum(1 for v in values if v)
    return {
        "applicable": len(values),
        "passed": passed,
        "rate": round(passed / len(values), 4),
    }


def _numbers(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 2),
        "p50": round(ordered[len(ordered) // 2], 2),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
        "max": round(ordered[-1], 2),
    }


def summarise(rows: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-attempt rows into the headline metrics."""

    def dim(key: str) -> dict[str, Any]:
        return _rate([r["score"][key] for r in rows if r["score"].get(key) is not None])

    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({c["category"] for c in cases}):
        subset = [r for r in rows if r["category"] == category]
        by_category[category] = {
            "attempts": len(subset),
            "cases": len({r["case_id"] for r in subset}),
            "success_rate": round(
                sum(1 for r in subset if r["score"]["success"]) / len(subset), 4
            )
            if subset
            else None,
            "strict_success_rate": round(
                sum(1 for r in subset if r["score"]["strict_success"]) / len(subset), 4
            )
            if subset
            else None,
            "latency_ms": _numbers([r["latency_ms"] for r in subset]),
        }

    # pass^k over all attempts of the same case.
    per_case: dict[str, list[bool]] = {}
    for row in rows:
        per_case.setdefault(row["case_id"], []).append(row["score"]["success"])
    per_case_strict: dict[str, list[bool]] = {}
    for row in rows:
        per_case_strict.setdefault(row["case_id"], []).append(
            row["score"]["strict_success"]
        )
    passes = max((len(v) for v in per_case.values()), default=0)
    all_pass = [all(v) for v in per_case.values() if len(v) == passes]
    flipped = [cid for cid, v in per_case.items() if len(set(v)) > 1]

    return {
        "attempts": len(rows),
        "cases": len(per_case),
        "passes": passes,
        "success_rate": round(sum(1 for r in rows if r["score"]["success"]) / len(rows), 4)
        if rows
        else None,
        "strict_success_rate": round(
            sum(1 for r in rows if r["score"]["strict_success"]) / len(rows), 4
        )
        if rows
        else None,
        "strict_pass_hat_k": round(
            sum(1 for v in per_case_strict.values() if v and all(v)) / len(per_case_strict), 4
        )
        if per_case_strict
        else None,
        "pass_hat_k": round(sum(1 for v in all_pass if v) / len(all_pass), 4) if all_pass else None,
        "flip_rate": round(len(flipped) / len(per_case), 4) if per_case else None,
        "flipped_cases": sorted(flipped),
        "dimensions": {
            "tools_complete": dim("tools_complete"),
            "tools_exact": dim("tools_exact"),
            "no_forbidden_tools": dim("no_forbidden_tools"),
            "args_ok": dim("args_ok"),
            "call_order_ok": dim("order_ok"),
            "retrieval_recall": dim("retrieval_recall"),
            "answer_containment": dim("containment_ok"),
            "no_forbidden_text": dim("forbidden_text_ok"),
            "stop_reason_ok": dim("stop_reason_ok"),
            "no_errors": dim("no_errors"),
        },
        "redundant_calls": _numbers([r["redundant_calls"] for r in rows]),
        "latency_ms": _numbers([r["latency_ms"] for r in rows]),
        "total_tokens": _numbers([float(r["total_tokens"]) for r in rows]),
        "cost_cny": str(sum((Decimal(r["cost_cny"]) for r in rows), Decimal("0"))),
        "cost_is_partial": any(r.get("unpriced_models") for r in rows),
        "api_calls": sum(r.get("api_calls", 0) for r in rows),
        "by_category": by_category,
    }


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def run(
    *,
    passes: int = 3,
    only: str | None = None,
    case_id: str | None = None,
    settings: Settings | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    out_dir = out_dir or (
        PROJECT_ROOT / "results" / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    if (out_dir / "summary.json").exists():
        raise FileExistsError(f"evaluation output already exists: {out_dir}; choose a new --out")
    out_dir.mkdir(parents=True, exist_ok=True)

    data = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    if only:
        cases = [c for c in cases if c["category"] == only]
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
    if not cases:
        raise SystemExit(f"no cases matched (only={only!r}, case_id={case_id!r})")

    rows: list[dict[str, Any]] = []
    total = len(cases) * passes
    done = 0

    for case in cases:
        for attempt in range(1, passes + 1):
            # A fresh agent per attempt: no history bleed between attempts, and
            # `pass^k` must measure the same task from the same starting state.
            try:
                agent = CustomerServiceAgent(settings=settings, current_user_id=case["user"])
                result = agent.chat(case["query"])
                observed = observe(result)
            except Exception as exc:  # noqa: BLE001 - a crash is a data point
                observed = Observed(
                    stop_reason="exception",
                    answer="",
                    errors=[f"{type(exc).__name__}: {exc}"],
                )

            score = score_case(case, observed)
            rows.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "attempt": attempt,
                    "query": case["query"],
                    **asdict(observed),
                    "redundant_calls": max(
                        0,
                        len(observed.tool_sequence) - len(set(case.get("required_tools", []))),
                    ),
                    "score": asdict(score),
                }
            )
            done += 1
            flag = "ok  " if score.success else "FAIL"
            print(
                f"[{done:>3}/{total}] {flag} {case['id']:<34} "
                f"tools={'+'.join(observed.tool_sequence) or '(none)':<40} "
                f"{observed.latency_ms:>7.0f}ms",
                flush=True,
            )

    summary = summarise(rows, cases)
    summary["run"] = {
        "scoring_version": SCORING_VERSION,
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True
        ).stdout.strip(),
        "cases_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "knowledge_sha256": hashlib.sha256(
            b"".join(
                path.name.encode() + b"\0" + path.read_bytes()
                for path in sorted(settings.docs_dir.glob("*.md"))
            )
        ).hexdigest(),
        "settings": {
            name: getattr(settings, name)
            for name in (
                "llm_model", "llm_temperature", "llm_thinking", "llm_max_output_tokens",
                "llm_assume_peak", "llm_usd_to_cny", "embedding_model", "top_k", "max_steps",
            )
        },
        "reference_date": (settings.reference_date or datetime.now().date()).isoformat(),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out_dir / "per_case.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    with (out_dir / "failures.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            if not row["score"]["success"]:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    print(f"results: {out_dir}")
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(
        f"attempts={summary['attempts']}  cases={summary['cases']}  passes={summary['passes']}"
    )
    print(
        f"success_rate={summary['success_rate']}  "
        f"pass^{summary['passes']}={summary['pass_hat_k']}  "
        f"flip_rate={summary['flip_rate']}"
    )
    print(
        f"strict_success_rate={summary['strict_success_rate']}  "
        f"strict_pass^{summary['passes']}={summary['strict_pass_hat_k']}"
        "   <-- strict also forbids redundant tool calls"
    )
    if summary["success_rate"] == 1.0 and (summary["strict_success_rate"] or 0) < 1.0:
        print(
            "NOTE: the headline metric is saturated. Optimise strict_success_rate;"
            " the gap is wasted tool calls."
        )
    if summary["flipped_cases"]:
        print(f"unstable cases: {summary['flipped_cases']}")
    print("-" * 78)
    print(f"{'dimension':<24}{'passed/applicable':>20}{'rate':>10}{'coverage':>12}")
    total_attempts = summary["attempts"]
    for name, stat in summary["dimensions"].items():
        rate = "n/a" if stat["rate"] is None else f"{stat['rate']:.3f}"
        coverage = f"{stat['applicable'] / total_attempts:.0%}" if total_attempts else "n/a"
        print(f"{name:<24}{stat['passed']:>10}/{stat['applicable']:<9}{rate:>10}{coverage:>12}")
    print("-" * 78)
    print("by category:")
    for name, stat in summary["by_category"].items():
        lat = stat["latency_ms"]
        p50 = lat.get("p50", "n/a")
        print(
            f"  {name:<20} cases={stat['cases']:<3} attempts={stat['attempts']:<4} "
            f"success={stat['success_rate']} strict={stat.get('strict_success_rate')} "
            f"p50={p50}ms"
        )
    print("-" * 78)
    print(f"redundant tool calls : {summary['redundant_calls']}")
    print(f"latency (all)        : {summary['latency_ms']}")
    print(f"tokens (all)         : {summary['total_tokens']}")
    print(f"priced cost (CNY)    : {summary['cost_cny']} (partial={summary['cost_is_partial']})")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--only", help="restrict to one category")
    parser.add_argument("--case", help="restrict to one case id (read-only filter)")
    args = parser.parse_args()

    print_summary(run(passes=args.passes, only=args.only, case_id=args.case))
