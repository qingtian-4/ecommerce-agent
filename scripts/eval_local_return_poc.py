"""Frozen local development smoke. Each stage is a NEW CLI process, never a benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from tau_pilot_artifacts import export_ledger  # noqa: E402

from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.local_return_tools import LocalReturnGateway, fixture_settings, init_fixture  # noqa: E402
from acs.sessions import SessionStore  # noqa: E402

STAGES = [
    "我要退O10024，先保存这个订单的退货草稿；顺便说明七天无理由退货的运费规则。",
    (
        "退这单的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件。"
        "请保存草稿，先不要预览。"
    ),
    "先插问P003充电器有几个接口？之前退货草稿保留，不用修改。",
    "回到退货，把同订单商品更正为磁吸充电宝；数量1件，无理由，未拆封且满足全部二次销售条件。请生成预览，不能正式申请。",
    "刚才无理由退货的期限和运费是什么？不要取消或修改草稿。",
    None,
    "取消退货草稿",
    (
        "重新退O10024的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件，"
        "请生成最新预览，不能正式申请。"
    ),
]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(out, env_file, price_review):
    os.umask(0o077)
    for args in (["git", "diff", "--quiet"], ["git", "diff", "--cached", "--quiet"]):
        subprocess.run(args, cwd=ROOT, check=True)
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    runtime = private / "runtime"
    init_fixture(runtime)
    (runtime / "price_review.json").write_bytes(price_review.read_bytes())
    files = [
        *sorted((ROOT / "src/acs").glob("*.py")),
        ROOT / "uv.lock",
        ROOT / "scripts/local_return_poc.py",
        Path(__file__),
    ]
    manifest = {
        "kind": "development-smoke-not-benchmark",
        "inputs": STAGES,
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "fingerprints": {str(p.relative_to(ROOT)): fingerprint(p) for p in files},
        "price_review": json.loads(price_review.read_text()),
        "cap_cny": 3,
        "max_paid_requests": 32,
        "automatic_retry": False,
        "model": "deepseek-flash",
        "temperature": 0,
        "max_tokens": 1024,
        "thinking": False,
        "model_timeout_s": 60,
        "total_deadline_s": 3600,
        "fixture": json.loads((runtime / "fixture.json").read_text()),
        "kb_sha256": {p.name: fingerprint(p) for p in sorted((runtime / "docs").glob("*.md"))},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    settings = fixture_settings(runtime)
    store = SessionStore(settings.db_path)
    sid = store.create("USER001")
    gateway = LocalReturnGateway(settings)
    records = []
    previous = None
    old_preview = None

    def snapshot(name):
        with sqlite3.connect(settings.db_path) as src, sqlite3.connect(private / name) as dst:
            src.backup(dst)

    snapshot("before.sqlite")
    for index, text in enumerate(STAGES, 1):
        cmd = [
            sys.executable,
            "scripts/local_return_poc.py",
            "--state-dir",
            str(runtime),
            "--env-file",
            str(env_file),
            "resume" if text is None else "ask",
            sid,
        ]
        if text is not None:
            cmd.append(text)
        start = __import__("time").perf_counter()
        try:
            process = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired as exc:
            process = subprocess.CompletedProcess(
                cmd,
                124,
                (exc.stdout or b" ").decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                "Smoke stage deadline exceeded; no automatic retry",
            )
        latency = (__import__("time").perf_counter() - start) * 1000
        (private / f"stage-{index}.stdout.jsonl").write_text(process.stdout)
        (private / f"stage-{index}.stderr.txt").write_text(process.stderr)
        snapshot(f"after-{index}.sqlite")
        business = gateway.state(sid, "USER001")
        task, preview = business["task"].get("task"), business["preview"]
        checks = {}
        if process.returncode == 0:
            events = [json.loads(line) for line in process.stdout.splitlines()]
            done = events[-1]
            checks["completed"] = done["type"] == "completed"
            if index == 1:
                checks["order_draft"] = bool(task and task["order_id"] == "O10024")
            if index in (2, 8):
                checks["earphone"] = bool(task and task["item_id"] == 4 and task["quantity"] == 1)
            if index in (3, 5, 6):
                checks["consult_preserves_revision"] = bool(
                    task and previous and task["revision"] == previous["revision"]
                )
            if index == 4:
                checks["correction"] = bool(task and task["item_id"] == 5 and task["quantity"] == 1)
                checks["preview"] = bool(preview and preview["ready"])
                old_preview = preview["preview_id"] if preview else None
            if index == 6:
                checks["restart_historical_answer"] = done["detail"].get(
                    "answer_is_historical", False
                )
            if index == 7:
                checks["explicit_cancel"] = bool(
                    task and task["status"] == "cancelled" and not preview
                )
            if index == 8:
                checks["new_preview"] = bool(preview and preview["preview_id"] != old_preview)
        with store.transaction() as conn:
            formal = {
                t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                for t in (
                    "return_requests",
                    "return_confirmations",
                    "return_reviews",
                    "return_audit",
                )
            }
            checks["no_formal_writes"] = all(v == 0 for v in formal.values())
            receipts = [dict(r) for r in conn.execute("SELECT * FROM local_receipts")]
            preview_row = (
                conn.execute(
                    "SELECT revision FROM return_previews WHERE preview_id=?",
                    (preview["preview_id"],),
                ).fetchone()
                if preview
                else None
            )
        summary = EpisodeLedger(runtime / "fees.jsonl", cap_cny=3, max_requests=32).summary()
        record = {
            "stage": index,
            "final_answer_present": bool(done["detail"].get("answer"))
            if process.returncode == 0
            else False,
            "preview_revision": preview_row[0] if preview_row else None,
            "preview_valid_at_stage_end": bool(preview and preview["ready"]),
            "returncode": process.returncode,
            "latency_ms": latency,
            "checks": checks,
            "requests_cumulative": summary["requests"],
            "draft_revision": task.get("revision") if task else None,
            "draft_status": task.get("status") if task else None,
            "tool_calls_cumulative": len(receipts),
            "formal_counts": formal,
            "passed": process.returncode == 0 and all(checks.values()),
        }
        records.append(record)
        previous = task
        # Failure is preserved; no automatic rerun or changed task. Stop all later paid requests.
        if not record["passed"]:
            break
    ledger = EpisodeLedger(runtime / "fees.jsonl", cap_cny=3, max_requests=32)
    export_ledger(ledger, out)
    score = {
        "kind": "local-development-smoke",
        "source_sha": manifest["source_sha"],
        "stages": records,
        "completed_all": len(records) == len(STAGES) and all(r["passed"] for r in records),
        "cost": ledger.summary(),
        "raw_evidence": ".private-evidence (local only, ignored by Git)",
    }
    (out / "scorecard.json").write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(score, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--price-review", type=Path, required=True)
    args = parser.parse_args()
    run(args.out, args.env_file, args.price_review)
