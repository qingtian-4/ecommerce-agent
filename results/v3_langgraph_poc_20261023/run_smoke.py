"""Run each frozen prompt once in a fresh process; preserve failures, never retry for score."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads((HERE / "smoke_plan.json").read_text())
    db = args.state_dir / "business.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with closing(sqlite3.connect(db)) as conn:
        (HERE / "initial_business.sql").write_text("\n".join(conn.iterdump()) + "\n")
    results = []
    for case in plan["cases"]:
        output = HERE / f"live_{case['id']}.jsonl"
        if case["id"] == "p1":
            output = HERE / "live_p1_resumed.jsonl"
            elapsed = None
            exit_code = 0
        else:
            if output.exists():
                raise FileExistsError(f"Refuse to overwrite/retry {case['id']}")
            started = perf_counter()
            with output.open("w") as out, output.with_suffix(".stderr").open("w") as err:
                proc = subprocess.run([
                    sys.executable, "scripts/langgraph_poc.py", "--state-dir", str(args.state_dir),
                    "--env-file", str(args.env_file), "ask", case["session"], case["text"],
                ], cwd=ROOT, stdout=out, stderr=err, timeout=180)
            elapsed, exit_code = (perf_counter() - started) * 1000, proc.returncode
        events = [json.loads(line) for line in output.read_text().splitlines()]
        final = [e for e in events if e["type"] == "run_completed"]
        results.append({"id": case["id"], "session": case["session"],
                        "text": case["text"], "exit_code": exit_code, "elapsed_ms": elapsed,
                        "completed": bool(final), "answer": final[-1]["detail"]["answer"]
                        if final else None,
                        "tools": [e["detail"]["name"] for e in events
                                  if e["type"] == "tool_started"],
                        "evidence": output.name})
        after = hashlib.sha256(db.read_bytes()).hexdigest()
        (HERE / "smoke_results.json").write_text(json.dumps({
            "results": results, "business_sha256_before": before,
            "business_sha256_after": after, "business_unchanged": before == after,
            "fresh_process_each_turn": True,
        }, ensure_ascii=False, indent=2) + "\n")
        shutil.copyfile(args.state_dir / "requests.jsonl", HERE / "requests.jsonl")
        print(case["id"], "completed" if final else "failed", flush=True)
        if exit_code or not final or before != after:
            raise RuntimeError("Stop on failed case or unexpected business mutation")


if __name__ == "__main__":
    main()
