"""Offline local benchmark adapter over M8 execution and the real V3 BM25 tool.

This is not an Agent implementation or an official benchmark adapter. No paid
mode is provided. Gold never enters native runtime requests; replay decisions
are explicitly a protocol control, not independently predicted model answers.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import sqlite3
import time
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from . import multiturn_evaluation as m8
from .commerce_benchmark_data import LATEST_VERSION, VERSION, dump, file_hash
from .config import PROJECT_ROOT, Settings
from .cs_tools import ToolRegistry
from .db_seed import SCHEMA
from .live_evaluation import score_episode
from .local_return_tools import LocalKnowledge
from .local_submission import init_formal_fixture, read_submission_receipt
from .rag import CHUNKER_VERSION, KnowledgeBase
from .retrieval import LexicalIndex
from .retrieval_eval import evidence_metrics, load_cases
from .sessions import SessionStore

BASE_SNAPSHOT, BASE_CLAIM = m8.snapshot, m8.claim_value
CONTRACT = LATEST_VERSION + "/m8.1-open-answer-v3"


def load_dataset(root):
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["version"] not in {VERSION, LATEST_VERSION}:
        raise ValueError("Unknown local benchmark version")
    files = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p != root / "manifest.json"
    }
    if files != set(manifest["files"]):
        raise ValueError("Frozen dataset inventory changed")
    for name, expected in manifest["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Unsafe dataset manifest path")
        if file_hash(root / name) != expected:
            raise ValueError("Frozen dataset fingerprint changed: " + name)
    entries = json.loads((root / "agent_tasks.json").read_text())
    tasks = [m8.Task.model_validate(e["task"]) for e in entries]
    contracts = json.loads((root / "replay_contracts.json").read_text())
    ids = [t.task_id for t in tasks]
    if len(set(ids)) != len(ids) or set(ids) != set(contracts):
        raise ValueError("Duplicate tasks or mismatched replay contracts")
    if len(tasks) != manifest["agent_tasks"]:
        raise ValueError("Task count differs from manifest")
    for task, entry in zip(tasks, entries, strict=True):
        if len(task.steps) != len(contracts[task.task_id]) or len(task.steps) != len(
            entry["tool_gold"]
        ):
            raise ValueError("Step/contract/receipt alignment differs")
        if sum(s.action == "chat" for s in task.steps) < 2:
            raise ValueError("Agent task must be genuinely multiturn")
    kb = KnowledgeBase(
        Settings(_env_file=None, docs_dir=root / "docs", chunk_size=700, chunk_overlap=80)
    )
    kb._chunks = kb.load_documents()
    rag = load_cases(root / "rag_cases.json", kb)
    if len(rag["cases"]) != manifest["rag_cases"]:
        raise ValueError("RAG count differs from manifest")
    if rag["chunker_version"] != CHUNKER_VERSION:
        raise ValueError("Frozen RAG chunker version changed")
    return dict(
        root=root,
        manifest=manifest,
        entries=entries,
        tasks=tasks,
        contracts=contracts,
        business=json.loads((root / "business.json").read_text()),
        rag=rag,
    )


def seed_database(path, data, **kwargs):
    if path.exists():
        raise FileExistsError("Never reseed an existing database")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA)
    SessionStore(path)  # Existing schema/migrations/quantity constraints.
    today = date.fromisoformat(data["reference_date"])
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for u in data["users"]:
            conn.execute("INSERT INTO users VALUES(?,?,?)", (u["id"], u["name"], u["phone"]))
        for p in data["products"]:
            conn.execute(
                "INSERT INTO products VALUES(?,?,?,?,?,?,?)",
                (
                    p["id"],
                    p["name"],
                    p["category"],
                    "本地合成",
                    p["price"],
                    "CNY",
                    "；".join(p["attributes"]),
                ),
            )
            for days, price in [(120, p["price"]), (1, p["current_price"])]:
                conn.execute(
                    "INSERT INTO price_history(product_id,price,changed_at) VALUES(?,?,?)",
                    (p["id"], price, (today - timedelta(days=days)).isoformat()),
                )
            for i, color in enumerate(["黑色", "白色"], 1):
                availability = (
                    "unknown"
                    if p["id"] == "B107" and i == 2
                    else "unavailable"
                    if p["id"] == "B104" and i == 2
                    else "available"
                )
                wearing = p["attributes"][0] if p["category"] == "无线耳机" else None
                conn.execute(
                    "INSERT INTO product_skus VALUES(?,?,?,?,?,?)",
                    (
                        f"{p['id']}-{i}",
                        p["id"],
                        wearing,
                        int("主动降噪" in p["attributes"]),
                        color,
                        availability,
                    ),
                )
        for o in data["orders"]:
            amount = sum(i["quantity"] * i["price"] for i in o["items"])
            conn.execute(
                "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    o["id"],
                    o["user_id"],
                    o["status"],
                    amount,
                    0 if o["status"] == "cancelled" else amount,
                    o["created_at"],
                    o["shipped_at"],
                    o["delivered_at"],
                    o["created_at"] if o["status"] == "cancelled" else None,
                ),
            )
            for i in o["items"]:
                conn.execute(
                    "INSERT INTO order_items VALUES(?,?,?,?,?)",
                    (i["id"], o["id"], i["product_id"], i["quantity"], i["price"]),
                )
                conn.execute(
                    "INSERT INTO order_item_skus VALUES(?,?)", (i["id"], i["product_id"] + "-1")
                )
            if o["shipped_at"]:
                trace = [dict(time=o["shipped_at"], text="合成包裹已揽收")]
                if o["delivered_at"]:
                    trace.append(dict(time=o["delivered_at"], text="合成包裹已签收"))
                conn.execute(
                    "INSERT INTO logistics VALUES(?,?,?,?,?,?,?)",
                    (
                        o["id"],
                        "合成配送",
                        "SYN-" + o["id"],
                        o["status"],
                        "合成中转站" if not o["delivered_at"] else None,
                        trace[-1]["time"],
                        json.dumps(trace, ensure_ascii=False),
                    ),
                )
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Invalid synthetic foreign keys")


def materialize(root, suite):
    """Seed ONLY a fresh fixture before the unchanged formal barriers are installed."""
    with (
        patch.object(m8, "NOW", datetime.fromisoformat(suite["manifest"]["clock"])),
        m8.business_clock(),
        patch(
            "acs.local_return_tools.build",
            lambda path, **kw: seed_database(path, suite["business"], **kw),
        ),
    ):
        init_formal_fixture(root)
    # These are the just-created fixture's knowledge copies, never the Demo or corpus originals.
    shutil.rmtree(root / "docs")
    shutil.copytree(suite["root"] / "docs", root / "docs")
    return m8.initial_fingerprint(root)


def audit_fixture(root, suite):
    with closing(sqlite3.connect((root / "business.sqlite").as_uri() + "?mode=ro", uri=True)) as c:
        counts = {
            t: c.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in m8.business_rows(root)
        }
        amounts = c.execute(
            "SELECT o.order_id FROM orders o JOIN order_items i ON o.order_id=i.order_id "
            "GROUP BY o.order_id HAVING o.total_amount!=SUM(i.quantity*i.purchase_price)"
        ).fetchall()
        expected = suite["business"]
        valid = counts["users"] == len(expected["users"]) and counts["products"] == 8
        valid &= counts["orders"] == len(expected["orders"])
        valid &= not c.execute("PRAGMA foreign_key_check").fetchall() and not amounts
        for o in expected["orders"]:
            actual = c.execute(
                "SELECT user_id,status,delivered_at FROM orders WHERE order_id=?", (o["id"],)
            ).fetchone()
            valid &= actual == (o["user_id"], o["status"], o["delivered_at"])
        if not valid:
            raise ValueError("Synthetic source/SQL inconsistency")
    return dict(counts=counts, amount_errors=amounts, valid=True)


def authoritative_snapshot(gateway, sid):
    state = BASE_SNAPSHOT(gateway, sid)
    with closing(gateway.store._connect()) as conn:
        state["orders"] = {r["order_id"]: dict(r) for r in conn.execute("SELECT * FROM orders")}
        state["prices"] = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT p.product_id,(SELECT price FROM price_history h "
                "WHERE h.product_id=p.product_id "
                "ORDER BY h.changed_at DESC,h.id DESC LIMIT 1) FROM products p"
            )
        }
        state["request_statuses"] = [
            r[0] for r in conn.execute("SELECT status FROM return_requests")
        ]
        state["confirmation"] = None
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='local_confirmation_waits'"
        ).fetchone():
            last = conn.execute(
                "SELECT status,decision,consumptions FROM local_confirmation_waits "
                "WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                (sid,),
            ).fetchone()
            state["confirmation"] = dict(last) if last else None
    state["reference_date"] = gateway.settings.reference_date.isoformat()
    return state


def claim_value(claim, state):
    if claim.fact.startswith("order."):
        _, oid, field = claim.fact.split(".")
        order = state["orders"][oid]
        actual = (
            (
                date.fromisoformat(state["reference_date"])
                - date.fromisoformat(order["delivered_at"][:10])
            ).days
            if field == "age"
            else (order[field])
        )
        return actual == claim.value
    if claim.fact.startswith("price."):
        return state["prices"][claim.fact.split(".", 1)[1]] == claim.value
    if claim.fact.startswith("access."):
        order = state["orders"].get(claim.fact.split(".", 1)[1])
        return bool(order and order["user_id"] == m8.UID) == claim.value
    if claim.fact == "request_status":
        return bool(state["request_statuses"]) and all(
            s == claim.value for s in state["request_statuses"]
        )
    return BASE_CLAIM(claim, state)


@contextmanager
def no_network():
    """Process-local guard, not a change to the concurrently running Demo."""
    attempted = []

    def refuse(*args, **kwargs):
        attempted.append(True)
        raise RuntimeError("This benchmark has no network or paid mode")

    with (
        patch.dict(
            os.environ,
            {
                "ACS_CHUNK_SIZE": "700",
                "ACS_CHUNK_OVERLAP": "80",
                "ACS_TOP_K": "3",
                "ACS_LLM_API_KEY": "",
                "ACS_EMBEDDING_API_KEY": "",
            },
        ),
        patch.object(socket.socket, "connect", refuse),
        patch("socket.create_connection", refuse),
    ):
        yield attempted
    if attempted:
        raise RuntimeError("Unexpected network attempt; offline acceptance failed")


def score_receipts(step, expected, attempted, receipts):
    """Alternative safe plans are allowed. Reference tool paths are diagnostics, not rewards."""
    valid, covered = m8.score_tools(step, attempted, receipts), 0
    for receipt in receipts:
        payload = json.loads(receipt["result"])
        matches = [
            e
            for e in expected
            if e["name"] == receipt["name"] and json.loads(receipt["arguments"]) == e["arguments"]
        ]
        covered += bool(matches)
        rejected = (
            payload.get("found") is False
            or bool(payload.get("error_type"))
            or (receipt["name"] == "preview_return" and not payload.get("ready"))
        )
        if rejected:
            valid &= any(e["result"].get("found") is False for e in matches)
        for e in matches:
            valid &= m8.subset(payload, e["result"]) and receipt["error"] == e["error"]
    return bool(valid), covered


def grade_run(task, entry, result, fresh_final, immutable):
    previous, reference_coverage = 0, []
    for step, record, gold in zip(task.steps, result["steps"], entry["tool_gold"], strict=False):
        receipts = record["state"]["receipts"][previous:]
        previous = len(record["state"]["receipts"])
        record["tool_valid"], covered = score_receipts(step, gold, record["attempted"], receipts)
        reference_coverage.append(dict(step=record["step"], covered=covered, reference=len(gold)))
        record["sql_correct"] &= m8.subset(record["state"], step.expect)
    result["sql_correct"] = (
        len(result["steps"]) == len(task.steps)
        and immutable
        and all(r["sql_correct"] for r in result["steps"])
        and m8.subset(fresh_final, task.steps[-1].expect)
    )
    result["tool_valid"] = len(result["steps"]) == len(task.steps) and all(
        r["tool_valid"] for r in result["steps"]
    )
    # Known negative controls are independently authored facts, not grounder regexes.
    annotated = task.model_copy(deep=True)
    for step in annotated.steps:
        step.answers.append(
            m8.AnswerReference(
                text="申请已经提交，退款已经完成。",
                claims=[
                    m8.Claim(span="申请已经提交", fact="submission", value=True),
                    m8.Claim(span="退款已经完成", fact="unsupported_operation", value=True),
                ],
            )
        )
    result.update(score_episode(annotated, result))
    result.update(contract=CONTRACT, final=fresh_final, reference_coverage=reference_coverage)
    return result


def run_agent_case(suite, entry, root, *, mutation=None):
    task = m8.Task.model_validate(entry["task"])
    decisions = copy.deepcopy(suite["contracts"][task.task_id])
    if mutation == "omit_write":
        decisions[0] = [
            d
            for d in decisions[0]
            if not any(c["name"] == "update_return_task" for c in d.get("calls", []))
        ]
    elif mutation in {"wrong_order", "wrong_quantity"}:
        index, field, value = (
            (0, "order_id", "O61002") if mutation == "wrong_order" else (1, "quantity", 2)
        )
        decisions[index][0]["calls"][0]["args"][field] = value
    elif mutation == "unsafe_cancel":
        decisions[1].insert(
            0, dict(calls=[dict(id="mutant-cancel", name="cancel_return_task", args={})])
        )
    elif mutation in {"false_completion", "paraphrase"}:
        decisions[-1][-1]["text"] = (
            "申请已经提交，退款已经完成。"
            if mutation == "false_completion"
            else "您的草稿我已记录，未提交。"
        )
    elif mutation:
        raise ValueError("Unknown scorer challenge")
    observed, baseline = [], []

    def fixture(path, ignored_task):
        fingerprint = materialize(path, suite)
        audit_fixture(path, suite)
        baseline.append(m8.digest(m8.business_rows(path)))
        return fingerprint

    def snapshot(gateway, sid):
        observed[:] = [(gateway, sid)]
        return authoritative_snapshot(gateway, sid)

    with no_network(), ExitStack() as stack:
        stack.enter_context(
            patch.object(m8, "NOW", datetime.fromisoformat(suite["manifest"]["clock"]))
        )
        stack.enter_context(patch.object(m8, "build_fixture", fixture))
        stack.enter_context(patch.object(m8, "snapshot", snapshot))
        stack.enter_context(patch.object(m8, "claim_value", claim_value))
        result = m8.run_task(task, decisions, root, "v3")
        with m8.business_clock():
            fresh = snapshot(*observed[0])
            if fresh["requests"]:
                with closing(observed[0][0].store._connect()) as c:
                    wait = c.execute("SELECT wait_id FROM local_submission_receipts").fetchone()[0]
                if not read_submission_receipt(root, observed[0][1], m8.UID, wait):
                    raise RuntimeError("Missing authoritative submission receipt")
            result = grade_run(
                task, entry, result, fresh, baseline[0] == m8.digest(m8.business_rows(root))
            )
            result["contract"] = suite["manifest"]["version"] + "/m8.1-open-answer-v3"
    dump(root / "scored-result.json", result)
    return result


def score_retrieval(case, ids, valid_ids, error=None):
    if error or any(i not in valid_ids for i in ids):
        return dict(outcome="execution_error", evidence=None)
    evidence = evidence_metrics(case["evidence_groups"], ids)
    # Nonempty unsupported context is exposure, not proof the final answer hallucinated.
    passed = evidence["all_evidence"] if case["evidence_groups"] else not ids
    return dict(
        outcome="pass" if passed else "fail",
        evidence=evidence,
        unsupported_context_exposure=bool(ids) if not case["evidence_groups"] else None,
    )


def run_rag(suite):
    settings = Settings(
        _env_file=None,
        docs_dir=suite["root"] / "docs",
        retrieval_mode="bm25",
        relevance_calibration_path=None,
        top_k=3,
        reranker_enabled=False,
        chunk_size=700,
        chunk_overlap=80,
    )
    chunks = KnowledgeBase(settings).load_documents()
    valid = {c.chunk_id for c in chunks}
    lexical = LexicalIndex(chunks)
    registry = ToolRegistry(settings, kb=LocalKnowledge(settings))
    rows = []
    with no_network():
        for case in suite["rag"]["cases"]:
            started = time.perf_counter()
            result, events, error = registry.run(
                "search_knowledge", json.dumps(dict(query=case["query"])), current_user_id=m8.UID
            )
            tool_ms = (time.perf_counter() - started) * 1000
            audit_started = time.perf_counter()
            ids = [c["chunk_id"] for c in result.get("documents", [])]
            candidates = [
                c.chunk_id
                for c in lexical.search(case["query"], suite["rag"]["conditions"]["candidate_k"])
            ]
            rows.append(
                case
                | dict(
                    context=ids,
                    candidates=candidates,
                    candidate_metrics=evidence_metrics(case["evidence_groups"], candidates),
                    error=error,
                    latency_ms=tool_ms,
                    candidate_audit_ms=(time.perf_counter() - audit_started) * 1000,
                    tool_events=[e.as_dict() for e in events],
                    grade=score_retrieval(case, ids, valid, error),
                )
            )
    supported = [r for r in rows if r["evidence_groups"]]
    unsupported = [r for r in rows if not r["evidence_groups"]]
    summary = dict(
        questions=len(rows),
        supported=len(supported),
        unsupported=len(unsupported),
        supported_all_evidence=sum(r["grade"]["outcome"] == "pass" for r in supported),
        mean_supported_recall=sum(
            (r["grade"]["evidence"] or {}).get("recall", 0) for r in supported
        )
        / len(supported),
        candidate_all_evidence=sum(bool(r["candidate_metrics"]["all_evidence"]) for r in supported),
        unsupported_context_exposed=sum(bool(r["context"]) for r in unsupported),
        errors=sum(r["grade"]["outcome"] == "execution_error" for r in rows),
        mode="actual_V3_LocalKnowledge_BM25",
        generated_answers="not_run",
        dense="not_run_no_offline_semantic_embedding",
        hybrid="not_run",
        chunk_size=700,
        chunk_overlap=80,
    )
    return dict(summary=summary, rows=rows)


def public_agent(result):
    return {
        k: result[k]
        for k in (
            "task_id",
            "split",
            "cohort",
            "status",
            "error",
            "sql_correct",
            "tool_valid",
            "raw_outcome",
            "display_outcome",
            "model_calls",
            "model_http_requests",
            "input_tokens",
            "output_tokens",
            "latency_ms",
        )
    }


def retrieval_controls(suite):
    chunks = KnowledgeBase(
        Settings(_env_file=None, docs_dir=suite["root"] / "docs", chunk_size=700, chunk_overlap=80)
    ).load_documents()
    valid = {c.chunk_id for c in chunks}
    supported = next(
        c for c in suite["rag"]["cases"] if len({g[0] for g in c["evidence_groups"]}) > 1
    )
    good = list(dict.fromkeys(g[0] for g in supported["evidence_groups"]))
    wrong = next(i for i in sorted(valid) if not any(i in g for g in supported["evidence_groups"]))
    unsupported = next(c for c in suite["rag"]["cases"] if not c["evidence_groups"])
    controls = []
    for name, case, ids, error, expected in [
        ("complete_evidence", supported, good, None, "pass"),
        ("missing_required_section", supported, good[:1], None, "fail"),
        ("unrelated_product", supported, [wrong], None, "fail"),
        ("invalid_chunk", supported, ["missing#999"], None, "execution_error"),
        ("unanswerable_empty", unsupported, [], None, "pass"),
        ("unanswerable_context", unsupported, [wrong], None, "fail"),
        ("tool_error_empty", unsupported, [], "transport_failure", "execution_error"),
    ]:
        observed = score_retrieval(case, ids, valid, error)["outcome"]
        controls.append(
            dict(
                kind="rag",
                mutation=name,
                expected=expected,
                observed=observed,
                detected=observed == expected,
            )
        )
    return controls


def run_suite(suite, out, ids=None, *, challenges=True):
    selected = [e for e in suite["entries"] if ids is None or e["task"]["task_id"] in ids]
    if (
        not selected
        or ids is not None
        and (len(ids) != len(set(ids)) or set(ids) != {e["task"]["task_id"] for e in selected})
    ):
        raise ValueError("Unknown, duplicate or empty task selection")
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    results, authority, answers = [], [], []
    for entry in selected:
        tid = entry["task"]["task_id"]
        print("offline replay", tid, flush=True)
        result = run_agent_case(suite, entry, private / tid)
        results.append(public_agent(result))
        authority.append(
            dict(
                task_id=tid,
                checks=[
                    dict(
                        step=r["step"],
                        action=r["action"],
                        sql_correct=r["sql_correct"],
                        tool_valid=r["tool_valid"],
                        draft=r["state"]["draft"],
                        preview_count=r["state"]["preview_count"],
                        valid_preview=r["state"]["valid_preview"],
                        counts=r["state"]["counts"],
                        confirmation=r["state"]["confirmation"],
                        recovery=r["recovery"],
                    )
                    for r in result["steps"]
                ],
                reference_coverage=result["reference_coverage"],
            )
        )
        answers.append(dict(task_id=tid, answers=result["answers"]))
        dump(out / "agent_scorecard.json", results)
        dump(out / "authority_checks.json", authority)
        dump(out / "answer_audit.json", answers)
    rag = run_rag(suite)
    dump(out / "rag_scorecard.json", rag)
    controls = retrieval_controls(suite) if challenges else []
    if challenges:
        for tid, mutant, expected in [
            ("CB03", "omit_write", "fail"),
            ("CB03", "wrong_order", "fail"),
            ("CB03", "wrong_quantity", "fail"),
            ("CB06", "unsafe_cancel", "fail"),
            ("CB07", "false_completion", "fail"),
            ("CB03", "paraphrase", "pending_review"),
        ]:
            entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
            r = run_agent_case(suite, entry, private / (tid + "-" + mutant), mutation=mutant)
            controls.append(
                dict(
                    kind="agent",
                    mutation=mutant,
                    expected=expected,
                    observed=r["raw_outcome"],
                    detected=r["raw_outcome"] == expected,
                    sql=r["sql_correct"],
                    tools=r["tool_valid"],
                )
            )
    dump(out / "scorer_controls.json", controls)
    badcases = [
        dict(
            kind="retrieval",
            id=r["id"],
            query=r["query"],
            context=r["context"],
            gold=r["evidence_groups"],
            grade=r["grade"],
        )
        for r in rag["rows"]
        if r["grade"]["outcome"] != "pass"
    ]
    badcases += [dict(kind="agent_pipeline", **r) for r in results if r["raw_outcome"] != "pass"]
    dump(out / "badcases.json", badcases)
    report = dict(
        contract=suite["manifest"]["version"] + "/m8.1-open-answer-v3",
        dataset=suite["manifest"],
        kind="offline_contract_baseline",
        selected_task_ids=[e["task"]["task_id"] for e in selected],
        agents=dict(
            episodes=len(results),
            outcomes=dict(Counter(r["raw_outcome"] for r in results)),
            sql_correct=sum(r["sql_correct"] for r in results),
            tool_valid=sum(r["tool_valid"] for r in results),
        ),
        rag=rag["summary"],
        scorer_controls=controls,
        paid_requests=0,
        cost_cny=0,
        model_quality="not_measured",
        source={
            str(p.relative_to(PROJECT_ROOT)): file_hash(p)
            for p in [
                Path(__file__),
                Path(__file__).with_name("commerce_benchmark_data.py"),
                Path(m8.__file__),
                Path(__file__).with_name("live_evaluation.py"),
                Path(__file__).with_name("local_return_poc.py"),
                Path(__file__).with_name("local_submission.py"),
                Path(__file__).with_name("local_confirmation.py"),
                Path(__file__).with_name("sessions.py"),
                Path(__file__).with_name("returns.py"),
                Path(__file__).with_name("cs_tools.py"),
                Path(__file__).with_name("rag.py"),
                Path(__file__).with_name("retrieval.py"),
                PROJECT_ROOT / "scripts/eval_commerce_benchmark.py",
                PROJECT_ROOT / "tests/test_commerce_benchmark.py",
                PROJECT_ROOT / "uv.lock",
            ]
        },
    )
    dump(out / "summary.json", report)
    return report
