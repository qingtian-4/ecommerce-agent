"""Offline M8.2 preparation only: no permit file, model invocation or task grading.

Reuses frozen M8.1 execution helpers. The inspect fault touches only a NEW copy
of historical synthetic MockTransport evidence, never the historical database.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
from argparse import Namespace
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_live_readiness import execute, load_contract  # noqa: E402

from acs.local_return_tools import formal_barriers  # noqa: E402
from acs.multiturn_evaluation import build_fixture, business_clock  # noqa: E402

OUT = Path(__file__).resolve().parent
PRIVATE = OUT / ".private-evidence"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    os.umask(0o077)
    calls = []

    def no_network(*args, **kwargs):
        calls.append("blocked")
        raise AssertionError("M8.2 preflight forbids all network/model calls")

    with patch.object(socket.socket, "connect", no_network):
        tasks, _, manifest = load_contract()
        forbidden_output = PRIVATE / "live-output-must-not-exist"
        try:
            execute(
                Namespace(
                    mode="live",
                    out=forbidden_output,
                    authorization=None,
                    price_review=None,
                    env_file=None,
                )
            )
        except ValueError as exc:
            refusal = str(exc)
            assert "Fresh explicit paid authorization required" in refusal
        else:
            raise AssertionError("Live entry accepted missing M8.2 permission")
        assert not forbidden_output.exists()
        initial = []
        for tid in ("D02", "D04"):
            task = next(t for t in tasks if t.task_id == tid)
            for arm in ("old", "v3"):
                fixture = PRIVATE / "initial" / (tid + "-" + arm)
                with business_clock():
                    fingerprint = build_fixture(fixture, task)
                db = fixture / "business.sqlite"
                with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                    counts = {
                        table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                        for table in (
                            "return_tasks",
                            "return_previews",
                            "return_requests",
                            "return_confirmations",
                            "return_audit",
                            "return_reviews",
                            "local_receipts",
                        )
                    }
                    assert not any(counts.values())
                    triggers = dict(
                        conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")
                    )
                    assert all(triggers[n] == sql for n, sql in formal_barriers().items())
                initial.append(
                    dict(
                        task_id=tid,
                        arm=arm,
                        fingerprint=fingerprint,
                        db_sha256=sha(db),
                        counts=counts,
                        barriers_verified=True,
                    )
                )
        assert initial[0]["fingerprint"] == initial[1]["fingerprint"]
        assert initial[2]["fingerprint"] == initial[3]["fingerprint"]

        # Existing OFFLINE evidence is a diagnostic input, NOT a new model result.
        source = ROOT / "results/m8_1_sdk_fixed_20261025_run1/.private-evidence"
        copied = PRIVATE / "inspect-copy"
        shutil.copytree(source, copied / ".private-evidence")
        ledger = copied / ".private-evidence/fees.jsonl"
        cached = copied / ".private-evidence/episodes.json"
        before_hashes = [sha(ledger), sha(cached)]
        before = execute(Namespace(mode="inspect", out=copied))
        db = copied / ".private-evidence/D04-old/business.sqlite"
        db.write_bytes(b"Intentionally invalid NEW synthetic copy; original untouched")
        (copied / ".private-evidence/D04-v3/graph.sqlite").write_bytes(b"Invalid graph copy")
        after = execute(Namespace(mode="inspect", out=copied))
        assert before == after and before_hashes == [sha(ledger), sha(cached)]
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.execute("SELECT COUNT(*) FROM return_tasks").fetchone()
        except sqlite3.DatabaseError as exc:
            sql_error = type(exc).__name__
        else:
            raise AssertionError("Independent SQL check failed to detect corrupted copy")
        assert not calls

    result = dict(
        kind="offline_preflight_not_model_pilot",
        contract=manifest["contract"],
        model_http_requests=0,
        paid_cost_cny=0,
        valid_authorization_file_created=False,
        absent_authorization_refusal=refusal,
        live_output_created=False,
        network_connect_attempts=len(calls),
        initial_fixtures=initial,
        inspect_boundary=dict(
            prior_mock_cache_source=str(source.relative_to(ROOT)),
            corruption_only_in_new_copy=True,
            SQL_and_Graph_not_reconciled=True,
            independent_sql_error=sql_error,
            inspect_report_unchanged_after_corruption=True,
            ledger_and_cache_files_unchanged=True,
            cached_complete_pairs=after["complete_pairs"],
            authority_verification=False,
        ),
    )
    with (OUT / "offline_checks.json").open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print("Offline gates and four initial fixtures verified; inspect is NOT SQL reconciliation")


if __name__ == "__main__":
    main()
