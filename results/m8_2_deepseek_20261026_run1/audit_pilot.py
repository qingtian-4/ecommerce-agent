"""Read-only audit of this frozen live batch; never invokes a model or resumes Graph.

Public files contain synthetic-state summaries. Full answers, SQL evidence and
blind identity mappings remain in the ignored, restricted evidence directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_live_readiness import load_contract  # noqa: E402

from acs.local_return_tools import TOOLS, WRITES, formal_barriers  # noqa: E402

OUT = Path(__file__).resolve().parent
PRIVATE = OUT / ".private-evidence"
SOURCE_TABLES = (
    "users",
    "products",
    "orders",
    "order_items",
    "logistics",
    "price_history",
    "product_skus",
    "order_item_skus",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def save(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def readonly(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def graph_audit(base, binding, receipts, turns, revision):
    path = base / "graph.sqlite"
    if not path.exists():
        return dict(applicable=False, reason="old_loop_uses_SessionStore_not_Graph")
    with closing(readonly(path)) as conn:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1"
        ).fetchone()
        values = JsonPlusSerializer().loads_typed((row["type"], row["checkpoint"]))[
            "channel_values"
        ]
        assert row["thread_id"] == binding["thread_id"]
        assert values["turn_id"] == binding["turn_id"] == turns[-1]["turn_id"]
        assert values["expected_revision"] == revision
        messages = values["messages"]
        calls = [c["id"] for m in messages for c in getattr(m, "tool_calls", [])]
        results = [m.tool_call_id for m in messages if m.type == "tool"]
        assert set(calls) == set(results) == {r["call_id"] for r in receipts}
        assert len(calls) == len(set(calls)) == len(results)
        assert messages[-1].type == "ai" and not messages[-1].tool_calls
        pending = conn.execute(
            "SELECT COUNT(*) FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
            (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]),
        ).fetchone()[0]
        assert pending == 0
        return dict(
            applicable=True,
            checkpoint_count=conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0],
            messages=len(messages),
            tool_messages=len(results),
            pending_writes=pending,
            last_turn_matches_sql=True,
            revision_matches_sql=True,
            all_tool_ids_match_sql=True,
            terminal_answer=True,
            restart_or_resume_executed=False,
        )


def audit():
    os.umask(0o077)
    tasks, _, _ = load_contract()
    rows = json.loads((PRIVATE / "episodes.json").read_text())
    events = [json.loads(line) for line in (PRIVATE / "fees.jsonl").read_text().splitlines()]
    assert len(rows) == 4
    assert [r["task_id"] + ":" + r["arm"] for r in rows] == [
        "D02:old",
        "D02:v3",
        "D04:old",
        "D04:v3",
    ]
    assert not any(r["type"] == "mock_http_observed" for r in events)
    assert not any(r["type"] in {"stop", "experiment_stop"} for r in events)
    initial = json.loads((PRIVATE / "execution-control/initial_fingerprints.json").read_text())
    packets, keys, checks, score = [], [], [], []
    for row in rows:
        tid, arm = row["task_id"], row["arm"]
        task = next(t for t in tasks if t.task_id == tid)
        base = PRIVATE / (tid + "-" + arm)
        reference = PRIVATE / "execution-control/initial-reference" / (tid + "-" + arm)
        expected = next(r for r in initial if (r["task_id"], r["arm"]) == (tid, arm))
        assert row["initial_fingerprint"] == expected["fingerprint"]
        outputs = json.loads((base / "model-outputs.json").read_text())
        raw_calls = [c for response in outputs for c in response.get("tool_calls", [])]
        assert not any(response.get("invalid_tool_calls") for response in outputs)
        episode = tid + ":" + arm
        intents = [
            r["call"] for r in events if r["type"] == "tool_intent" and r["episode"] == episode
        ]
        delivered = [
            r["receipt"] for r in events if r["type"] == "tool_receipt" and r["episode"] == episode
        ]
        reservations = [r for r in events if r["type"] == "reserved" and r["episode"] == episode]
        ends = [r for r in events if r["type"] == "request_end" and r["episode"] == episode]
        assert len(reservations) == len(ends) == row["model_http_requests"] <= 8
        assert all(r["status"] == "completed" and r["usage"] for r in ends)
        assert {r["request_id"] for r in reservations} == {r["request_id"] for r in ends}
        with (
            closing(readonly(base / "business.sqlite")) as conn,
            closing(readonly(reference / "business.sqlite")) as before,
        ):
            assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            for table in SOURCE_TABLES:
                assert [
                    tuple(r) for r in conn.execute("SELECT * FROM " + table + " ORDER BY 1")
                ] == [tuple(r) for r in before.execute("SELECT * FROM " + table + " ORDER BY 1")]
            sid = conn.execute("SELECT session_id FROM conversations").fetchone()[0]
            receipts = [
                dict(r) for r in conn.execute("SELECT * FROM local_receipts ORDER BY rowid")
            ]
            ids = [c["id"] for c in raw_calls]
            assert len(ids) == len(set(ids)) == len(receipts)
            assert set(ids) == {r["call_id"] for r in receipts} == {c["id"] for c in intents}
            assert set(ids) == {c["id"] for c in delivered}
            assert all(r["error"] is None and r["session_id"] == sid for r in receipts)
            attempts = [c for step in row["steps"] for c in step["attempted"]]
            assert {c["id"] for c in attempts} == set(ids)
            unsafe = [
                c
                for step in row["steps"]
                for c in step["attempted"]
                if c["name"] not in task.steps[step["step"]].allowed_tools
                or c["name"] in task.forbidden_tools
            ]
            assert not unsafe
            for call in raw_calls:
                receipt = next(r for r in receipts if r["call_id"] == call["id"])
                args = (
                    TOOLS[call["name"]].model_validate(call["args"]).model_dump(exclude_unset=True)
                )
                assert receipt["name"] == call["name"] and json.loads(receipt["arguments"]) == args
            stored = conn.execute(
                "SELECT data_json FROM return_tasks WHERE session_id=?", (sid,)
            ).fetchone()
            draft = json.loads(stored[0]) if stored else None
            assert draft == row["final"]["draft"]
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            counts = {
                t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] if t in tables else None
                for t in (
                    "return_previews",
                    "return_requests",
                    "return_confirmations",
                    "return_audit",
                    "return_reviews",
                    "local_confirmation_waits",
                    "local_submission_receipts",
                )
            }
            assert not any(counts.values())
            triggers = dict(conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            assert all(triggers[n] == sql for n, sql in formal_barriers().items())
            turns = [
                dict(r)
                for r in conn.execute(
                    "SELECT turn_id,status,messages_json FROM conversation_turns ORDER BY sequence"
                )
            ]
            assert len(turns) == 2 and all(t["status"] == "completed" for t in turns)
            binding = dict(conn.execute("SELECT * FROM local_threads").fetchone())
            assert binding["turn_id"] == turns[-1]["turn_id"]
            graph = graph_audit(base, binding, receipts, turns, (draft or {}).get("revision", 0))
            evidence_catalog = [
                dict(r)
                for r in conn.execute(
                    "SELECT product_id,name,category,brand,price,attributes FROM products"
                )
            ]
            orders = [
                dict(r)
                for r in conn.execute(
                    "SELECT order_id,status,total_amount,paid_amount,"
                    "delivered_at FROM orders WHERE user_id='USER001'"
                )
            ]
            items = [
                dict(r)
                for r in conn.execute(
                    "SELECT i.id,i.order_id,i.product_id,i.quantity "
                    "FROM order_items i JOIN orders o USING(order_id) "
                    "WHERE o.user_id='USER001'"
                )
            ]
        sql_summary = dict(
            task_id=tid,
            arm=arm,
            source_facts_unchanged=True,
            initial_fingerprint_matches=True,
            business_counts=counts,
            draft={
                k: v
                for k, v in (draft or {}).items()
                if k in ("status", "order_id", "item_id", "quantity", "reason", "revision")
            },
            tool_calls=len(raw_calls),
            unique_receipts=len(receipts),
            authoritative_ids_and_args_match=True,
            tool_errors=0,
            unsafe_action_attempts=len(unsafe),
            unknown_writes=0,
            write_calls=[c["name"] for c in raw_calls if c["name"] in WRITES],
            turns_completed=2,
            graph=graph,
            source_barriers_intact=True,
        )
        checks.append(sql_summary)
        costs = row["cost"]
        score.append(
            dict(
                task_id=tid,
                arm=arm,
                execution=row["status"],
                sql_correct=row["sql_correct"],
                frozen_tool_valid=row["tool_valid"],
                frozen_task_outcome=row["raw_outcome"],
                natural_language_review="pending_review",
                unsafe_action_attempts=len(unsafe),
                http_requests=len(reservations),
                tool_calls=len(raw_calls),
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cached_tokens=costs["roles"]["agent"]["cached_tokens"],
                episode_latency_ms=row["latency_ms"],
                model_http_latency_ms=costs["roles"]["agent"]["latency_ms"],
                reserved_cny=costs["reserved_cny"],
                peak_estimated_cny=costs["known_peak_estimated_cny"],
                timeband_estimated_cny=costs["known_timeband_estimated_cny"],
                cost_complete=costs["cost_complete"],
                runtime_error=row["error"],
                failure_class=row["failure_class"],
            )
        )
        conversation = []
        for step in row["steps"]:
            conversation.append(dict(role="user", text=task.steps[step["step"]].text))
            for kind in ("raw", "display"):
                identity = uuid4().hex
                packets.append(
                    dict(
                        review_id=identity,
                        conversation=list(conversation),
                        answer=step[kind],
                        evidence=step["state"],
                        product_catalog=evidence_catalog,
                        owner_orders=orders,
                        owner_items=items,
                        logical_business_time="2026-10-25T04:00:00+00:00",
                        review_fields=dict(
                            fact=None,
                            goal=None,
                            unsupported_business_claim=None,
                            evidence_citations=[],
                            unknown_reason="",
                            reviewer_id="",
                            reviewed_at=None,
                        ),
                    )
                )
                keys.append(
                    dict(review_id=identity, task_id=tid, arm=arm, step=step["step"], kind=kind)
                )
            conversation.append(dict(role="assistant", text=step["display"]))

    assert len(packets) == 16
    random.SystemRandom().shuffle(packets)
    save(PRIVATE / "independent-review-items.json", packets)
    save(PRIVATE / "independent-review-operator-key.json", keys)
    changed = sum(s["raw"] != s["display"] for r in rows for s in r["steps"])
    save(
        OUT / "review_status.json",
        dict(
            answer_items=16,
            unique_answer_turns=8,
            raw_display_identical_pairs=8 - changed,
            changed_answers=changed,
            reviewed_items=0,
            pending_items=16,
            independent_review_performed=False,
            automatic_grounder_used_as_judge=False,
            false_correction_rate=None,
            correction_rate=None,
            miss_rate=None,
            temporary_stream_text_measured=False,
            reviewer_packet_canonical_sha256=digest(packets),
            reviewer_packet_file_sha256=hashlib.sha256(
                (PRIVATE / "independent-review-items.json").read_bytes()
            ).hexdigest(),
            operator_key_published=False,
        ),
    )
    save(
        OUT / "sql_tool_graph_audit.json",
        dict(
            mode="readonly_SQL_and_checkpoint_decode",
            no_graph_resume=True,
            sql_and_tool_ids_verified=True,
            episodes=checks,
        ),
    )
    save(
        OUT / "paired_audit_scorecard.json",
        dict(
            kind="real_deepseek_development_pilot",
            source_sha="b5356662fdc63c9649d4cdf1c57c6a0e3254ece9",
            episodes=score,
            complete_execution_pairs=2,
            deterministic_contract_failed_episodes=4,
            natural_language_adjudicated_episodes=0,
            pending_answer_review_items=16,
            task_outcome_is_not_answer_faithfulness=True,
            paid_bill_observed=False,
        ),
    )
    total = sum(Decimal(s["reserved_cny"]) for s in score)
    assert total <= 5 and sum(s["http_requests"] for s in score) == 17 <= 40
    print("Read-only SQL, tool receipts, native checkpoints and 17 known HTTP usages reconciled")


if __name__ == "__main__":

    def no_network(*args, **kwargs):
        raise AssertionError("Analysis must not send HTTP or replay an Episode")

    with patch.object(socket.socket, "connect", no_network):
        audit()
