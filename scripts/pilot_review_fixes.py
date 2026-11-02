"""Small authorized integration probe; existing ledger, synthetic fixture, no judge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from local_return_poc import RequestCapture, verify_price  # noqa: E402

from acs.config import Settings  # noqa: E402
from acs.episode_budget import EpisodeLedger, EpisodeLimits  # noqa: E402
from acs.episode_transport import EpisodeCallback, EpisodeSyncTransport  # noqa: E402
from acs.intent_planner import IntentPlanner  # noqa: E402
from acs.langgraph_poc import deepseek_model  # noqa: E402
from acs.local_return_tools import fixture_settings  # noqa: E402
from acs.local_submission import KIND, FormalReturnPoc, init_formal_fixture  # noqa: E402
from acs.memory import MemoryStore  # noqa: E402
from acs.response_evidence import ResponseEvidence  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

CASES = ROOT / "data/eval/review_regression_v1/live_cases.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--authorization-reference", required=True)
    parser.add_argument("--price-review", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    out = args.out.resolve()
    if out.exists():
        raise SystemExit("Refuse to overwrite: " + str(out))
    settings = Settings(_env_file=ROOT / ".env")
    if not settings.has_llm_key:
        raise SystemExit("No configured model credential")
    out.mkdir(parents=True)
    shutil.copyfile(args.price_review, out / "price_review.json")
    verify_price(out, settings)
    files = ["src/acs/intent_planner.py", "src/acs/local_return_poc.py",
             "src/acs/local_submission.py", "src/acs/memory.py", "src/acs/sessions.py",
             "src/acs/web_service.py", "scripts/pilot_review_fixes.py"]
    manifest = {
        "kind": "known_case_integration_probe", "dataset_sha256": sha(CASES),
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                               cwd=ROOT, text=True).strip(),
        "implementation_sha256": {name: sha(ROOT / name) for name in files},
        "authorization_reference": args.authorization_reference,
        "limits": {"requests": 12, "cap_cny": 1, "body_bytes": 32768,
                   "episode_timeout_s": 180}, "llm_judge_calls": 0,
        "note": "Explicit tool guidance; tests SDK/wiring/accounting, not general model ability.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    fixture = private / "fixture"
    init_formal_fixture(fixture)
    store = SessionStore(fixture_settings(fixture, kind=KIND).db_path)
    manifest["fixture"] = json.loads((fixture / "fixture.json").read_text())
    manifest["business_snapshot_sha256"] = sha(store.db_path)
    manifest["price_review_sha256"] = sha(out / "price_review.json")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    memory = MemoryStore(store.db_path)
    os.environ["ACS_V3_MEMORY"] = "1"
    ledger = EpisodeLedger(out / "fees.jsonl", cap_cny=1, max_requests=12)
    ledger.begin("review", EpisodeLimits(requests=12, tools=32, steps=16,
                                        episode_timeout_s=180))
    ledger.evidence = ResponseEvidence(private, secrets=(settings.llm_api_key,))
    rows = []
    transport = EpisodeSyncTransport(ledger, "review", "agent",
                                    lambda: RequestCapture(ledger, ledger.evidence),
                                    max_body_bytes=32768)
    with httpx.Client(transport=transport) as client:
        model = deepseek_model(settings, http_client=client,
                               callbacks=[EpisodeCallback(ledger, "review")])
        for case in json.loads(CASES.read_text())["cases"]:
            if case["memory"]:
                memory.add_fact("USER001", case["memory"])
            sid = store.create("USER001")
            before = ledger.summary()["requests"]
            row = {"id": case["id"], "query": case["query"]}
            try:
                with FormalReturnPoc(fixture, "USER001", model=model, read_only=True,
                                     intent_routing=True,
                                     intent_planner=IntentPlanner.for_chat_model(model)) as poc:
                    events = list(poc.stream(sid, case["query"]))
                row["runtime_status"] = events[-1].type
                row["answer"] = events[-1].detail.get("answer", "")
                row["plan"] = next(e.detail for e in events if e.type == "intent_plan")
            except Exception as exc:
                row.update(runtime_status="error", error_type=type(exc).__name__)
            row["http_requests"] = ledger.summary()["requests"] - before
            with sqlite3.connect(store.db_path) as conn:
                row["business_requests"] = conn.execute(
                    "SELECT count(*) FROM return_requests").fetchone()[0]
                receipts = [(name, json.loads(payload)) for name, payload in conn.execute(
                    "SELECT name,result FROM local_receipts WHERE session_id=? ORDER BY rowid",
                    (sid,))]
                own_orders = conn.execute("SELECT count(*) FROM orders WHERE user_id='USER001'"
                                          ).fetchone()[0]
            row["receipts"] = [{"tool": name, "found": payload.get("found"),
                                "error_type": payload.get("error_type"),
                                "complete_catalog": payload.get("complete_catalog"),
                                "count": payload.get("count")}
                               for name, payload in receipts]
            row["checks"] = {
                "planner_via_host": row.get("plan", {}).get("source") == "llm",
                "required_routes": set(case["required"]) <= set(
                    row.get("plan", {}).get("goals", [])),
                "read_only": row["business_requests"] == 0,
                "runtime_completed": row["runtime_status"] == "completed",
            }
            if case["id"] == "L01":
                row["checks"]["orders_receipt"] = any(
                    name == "list_orders" and len(payload.get("orders", [])) == own_orders
                    for name, payload in receipts)
                row["checks"]["complete_catalog_receipt"] = any(
                    name == "list_products" and payload.get("complete_catalog") is True
                    for name, payload in receipts)
            else:
                row["checks"]["foreign_order_denied"] = any(
                    name == "get_order" and payload.get("error_type") == "forbidden"
                    for name, payload in receipts)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if row["runtime_status"] == "error":
                break
    result = {"rows": rows, "usage": ledger.summary(), "llm_judge_calls": 0,
              "note": "Two known synthetic probes; no success-rate or blind-test claim."}
    (out / "scorecard.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    shutil.copyfile(CASES, out / "cases.json")
    print(json.dumps(result["usage"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
