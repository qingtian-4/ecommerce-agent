"""Freeze and run v2 development smoke cases through the shared service.

Automatic checks cover execution bounds and unchanged business state. They do
not grade answer correctness; raw answers must be reviewed separately.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.chat_service import ChatService  # noqa: E402
from acs.config import get_settings  # noqa: E402
from acs.scenarios import data_fingerprint, scenario_settings, sha256  # noqa: E402


def counts(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in (
            "return_requests", "return_confirmations", "return_audit",
        )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("data/scenarios/v2"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    settings = scenario_settings(get_settings(), args.scenario)
    args.out.mkdir(parents=True, exist_ok=False)
    cases_path = args.scenario / "smoke_cases.json"
    cases = json.loads(cases_path.read_text())["cases"]
    manifest = json.loads((args.scenario / "manifest.json").read_text())
    implementation = {str(p): sha256(p) for p in sorted(Path("src/acs").glob("*.py"))}
    implementation["scripts/eval_scenario_smoke.py"] = sha256(Path(__file__))
    frozen = {"cases_sha256": sha256(cases_path), "scenario": manifest,
              "implementation_sha256": implementation, "model": settings.llm_model,
              "temperature": settings.llm_temperature, "thinking": settings.llm_thinking,
              "knowledge_mode": settings.knowledge_mode, "turn_timeout_s": settings.turn_timeout_s,
              "context_max_chars": settings.context_max_chars,
              "max_api_requests": settings.max_api_requests, "split": "development"}
    (args.out / "frozen_manifest.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n",
    )
    (args.out / "cases.json").write_bytes(cases_path.read_bytes())
    results = []
    with ((args.out / "raw.jsonl").open("x") as raw,
          TemporaryDirectory(prefix="acs-v2-eval-") as temp):
        for case in cases:
            path = Path(temp) / (case["id"] + ".db")
            with closing(sqlite3.connect(settings.db_path)) as src, closing(
                sqlite3.connect(path),
            ) as dst:
                src.backup(dst)
            service = ChatService(settings.model_copy(update={"db_path": path}))
            before = counts(path)
            turns, sid = [], None
            for query in case["turns"]:
                updates = []
                for update in service.run_turn(query, case["user_id"], sid):
                    sid = update.session_id
                    updates.append(update)
                final = updates[-1]
                turns.append({"query": query, "result": asdict(final.result),
                              "elapsed_ms": final.elapsed_ms, "stream_updates": len(updates),
                              "task_view": final.task_view})
            used = [e["label"] for t in turns for e in t["result"]["trace"]
                    if e["type"] == "tool_call"]
            checks = {
                "answered": all(t["result"]["stop_reason"] == "answered" for t in turns),
                "required_tools": set(case["required_tools"]) <= set(used),
                "context_bounded": all(all(c <= settings.context_max_chars for c in
                                           t["result"]["totals"].get("model_input_chars", []))
                                       for t in turns),
                "business_unchanged": data_fingerprint(path) == manifest["tables_sha256"],
                "no_submission": before == counts(path),
            }
            record = {"id": case["id"], "turns": turns, "checks": checks,
                      "manual_answer_review": "pending"}
            raw.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw.flush()
            results.append(record)
            print(json.dumps({"id": case["id"], "checks": checks}), flush=True)
    totals = [t["result"]["totals"] for r in results for t in r["turns"]]
    reasons = Counter(t["result"]["stop_reason"] for r in results for t in r["turns"])
    summary = {"cases": len(results), "execution_checks_passed": sum(
        all(r["checks"].values()) for r in results
    ), "stop_reasons": dict(reasons),
        "api_requests": sum(t["calls"] for t in totals),
        "total_tokens": sum(t["total_tokens"] for t in totals),
        "raw_sha256": sha256(args.out / "raw.jsonl"),
        "boundary": "单遍开发冒烟，仅检查执行与副作用；不是回答正确率或泛化指标。"}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
