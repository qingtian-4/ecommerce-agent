"""M8 isolated authored multiturn evaluation; never used by the customer runtime.

Contracts exercise real runtimes, not model quality. Reference annotations are
kept outside model requests; novel answers require independent blind review.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, convert_to_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict, Field

from .answer_grounding import collect_evidence, review_answer
from .cs_tools import ToolRegistry, TraceEvent
from .episode_budget import EpisodeLedger, EpisodeLimits
from .llm import LLMResponse, ToolCall, Usage
from .local_return_tools import TOOLS, WRITES, ReconciliationError, TurnContext
from .local_submission import FormalReturnPoc, init_formal_fixture
from .model_response_contract import require_response
from .runtime import BudgetExceeded
from .service_agent import CustomerServiceAgent

UID = "USER001"
NOW = datetime(2026, 10, 25, 4, tzinfo=UTC)
LIMITS = EpisodeLimits(requests=16, tools=24, steps=64, episode_timeout_s=600)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    span: str
    fact: str
    value: object


class AnswerReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    claims: list[Claim]
    annotation: str = "authored_complete_reference"


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = "chat"
    text: str = ""
    expect: dict
    allowed_tools: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    allowed_errors: list[str] = Field(default_factory=list)
    answers: list[AnswerReference] = Field(default_factory=list)
    answer_requires: list[str] = Field(default_factory=list)


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    split: str
    cohort: str = "common"
    goal: str
    fixture_index: int
    forbidden_tools: list[str] = Field(
        default_factory=lambda: [
            "confirm_and_submit",
            "review_return",
            "refund",
            "exchange",
            "raw_sql",
        ]
    )
    allowed_effects: str = "Only the explicitly expected draft/preview/application transitions"
    forbidden_effects: str = "No review/refund/exchange or mutation of source orders/products"
    steps: list[Step]
    success: str = "Every SQL/tool check and fully annotated final answer must pass"
    failure: str = "Any forbidden effect, SQL mismatch, false claim or unresolved answer"


@contextmanager
def business_clock():
    """Logical synthetic clock only; real model/episode time and latency stay real."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return NOW.date()

    with ExitStack() as stack:
        for module in ("sessions", "returns", "local_confirmation", "answer_grounding"):
            stack.enter_context(patch("acs." + module + ".datetime", FrozenDatetime))
        for module in ("db_seed", "local_return_tools"):
            stack.enter_context(patch("acs." + module + ".date", FrozenDate))
        yield


def build_fixture(root, task):
    from .db_seed import build

    def seeded(path, **kwargs):
        build(path, **kwargs)
        i = task.fixture_index
        with sqlite3.connect(path) as conn:
            for suffix, owner in (("1", UID), ("2", UID), ("3", "USER002")):
                oid = f"O8{i:03d}{suffix}"
                conn.execute(
                    "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        oid,
                        owner,
                        "delivered",
                        6 * (200 + i),
                        6 * (200 + i),
                        "2026-10-20T00:00:00",
                        "2026-10-21T00:00:00",
                        "2026-10-23T00:00:00",
                        None,
                    ),
                )
                for offset, product in enumerate(("P001", "P004")):
                    item = 1000 + i * 10 + (int(suffix) - 1) * 2 + offset
                    conn.execute(
                        "INSERT INTO order_items VALUES(?,?,?,?,?)",
                        (item, oid, product, 3, 200 + i),
                    )
            # New synthetic facts, not a copy of the historical six-turn database.
            conn.execute(
                "UPDATE users SET name=?,phone=? WHERE user_id=?",
                (f"M8合成客户{i}", "00000000000", UID),
            )

    # Extend ONLY the brand-new seed before original formal barriers are installed.
    # No existing fixture is broadened and no trigger is dropped or disabled.
    with patch("acs.local_return_tools.build", seeded):
        init_formal_fixture(root)
    return initial_fingerprint(root)


def business_rows(root):
    with sqlite3.connect(root / "business.sqlite") as conn:
        rows = {
            t: conn.execute("SELECT * FROM " + t + " ORDER BY 1").fetchall()
            for t in (
                "users",
                "products",
                "orders",
                "order_items",
                "logistics",
                "price_history",
                "product_skus",
                "order_item_skus",
            )
        }
    return rows


def initial_fingerprint(root):
    return digest(
        {
            "sql": business_rows(root),
            "docs": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((root / "docs").glob("*.md"))
            },
        }
    )


def load_suite(path):
    manifest = json.loads((path / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen dataset fingerprint mismatch: " + name)
    for name, expected in manifest.get("implementation", {}).items():
        source = Path(__file__).resolve().parents[2] / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen implementation mismatch: " + name)
    tasks = [Task.model_validate(t) for t in json.loads((path / "tasks.json").read_text())]
    if len(tasks) != 30 or len({t.task_id for t in tasks}) != 30:
        raise ValueError("Exactly 30 independent tasks required")
    if [sum(t.split == s for t in tasks) for s in ("development", "test")] != [10, 20]:
        raise ValueError("Frozen 10/20 split mismatch")
    return tasks, json.loads((path / "contracts.json").read_text()), manifest


def snapshot(gateway, sid):
    with closing(gateway.store._connect()) as conn:
        task = gateway.store._task(conn, sid)
        counts = {
            t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
            for t in ("return_requests", "return_confirmations", "return_audit", "return_reviews")
        }
        previews = [
            dict(r)
            for r in conn.execute(
                "SELECT preview_id,revision,expires_at FROM return_previews "
                "WHERE session_id=? ORDER BY created_at,rowid",
                (sid,),
            )
        ]
        receipts = [
            dict(r)
            for r in conn.execute(
                "SELECT call_id,name,arguments,result,error FROM local_receipts "
                "WHERE session_id=? ORDER BY rowid",
                (sid,),
            )
        ]
        products = {
            r[0]: [a for a in r[1].split("；") if a]
            for r in conn.execute("SELECT product_id,attributes FROM products")
        }
    current = gateway.service.latest_preview(sid, UID)
    return {
        "draft": task,
        "preview_count": len(previews),
        "valid_preview": bool(current),
        "requests": counts["return_requests"],
        "counts": counts,
        "previews": previews,
        "receipts": receipts,
        "products": products,
        "policy": __import__("acs.returns", fromlist=["return_policy"]).return_policy(
            gateway.settings
        ),
    }


def subset(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(actual[k], v) for k, v in expected.items()
        )
    return actual == expected


def claim_value(claim, state):
    if claim.fact.startswith("draft."):
        return (state["draft"] or {}).get(claim.fact.split(".", 1)[1]) == claim.value
    if claim.fact == "preview_count":
        return state["preview_count"] == claim.value
    if claim.fact == "valid_preview":
        return state["valid_preview"] == claim.value
    if claim.fact == "submission":
        return bool(state["requests"]) == claim.value
    if claim.fact == "submission_unverified":
        return not state["requests"]
    if claim.fact == "unsupported_operation":
        return claim.value is False  # Fixture permanently forbids refund/review/exchange.
    if claim.fact == "history_exists":
        return (state["previous_preview_count"] > 0) == claim.value
    if claim.fact.startswith("product."):
        return claim.value in state["products"][claim.fact.split(".", 1)[1]]
    if claim.fact == "shipping":
        return state["policy"]["shipping"]["no_reason"] == claim.value
    if claim.fact in {"nonassertion", "clarification"}:
        return claim.value is True  # Frozen manual speech-act annotation, not SQL success.
    raise ValueError("Unknown claim type " + claim.fact)


def assess_answer(text, references, state):
    # Exact authored references certify only their annotated text. A novel answer
    # is NEVER declared faithful merely because no regular expression fired.
    ref = next((r for r in references if r.text == text), None)
    if ref is None:
        return {"status": "unresolved", "claims": [], "unresolved": 1}
    values = [
        {"span": c.span, "fact": c.fact, "correct": claim_value(c, state)} for c in ref.claims
    ]
    if not values or any(c.span not in text for c in ref.claims):
        raise ValueError("Incomplete authored answer annotation")
    return {
        "status": "faithful" if all(c["correct"] for c in values) else "unfaithful",
        "claims": values,
        "unresolved": 0,
    }


def score_tools(step, attempted, receipts):
    successful = []
    for r in receipts:
        payload = json.loads(r["result"])
        rejected = isinstance(payload, dict) and (
            payload.get("found") is False
            or bool(payload.get("error_type"))
            or (r["name"] == "preview_return" and not payload.get("ready"))
        )
        if r["error"] is None and not rejected:
            successful.append(r["name"])
    return (
        all(c["name"] in step.allowed_tools for c in attempted)
        and all(n in successful for n in step.required_tools)
        and all(r["error"] is None or r["name"] in step.allowed_errors for r in receipts)
        and len({r["call_id"] for r in receipts}) == len(receipts)
    )


def schemas_for(gateway):
    registry = ToolRegistry(gateway.settings)
    registry.bind_return_task(gateway.store, "", "")
    specs = {s["function"]["name"]: s for s in registry.schemas()}
    result = []
    for name, args in TOOLS.items():
        description = specs[name]["function"]["description"]
        if name == "preview_return":
            description = FormalReturnPoc.preview_description
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": args.model_json_schema(),
                },
            }
        )
    return result


def normalized(value):
    # Audit equality excludes opaque generated IDs, never product/order IDs.
    return re.sub(
        r"\b[0-9a-f]{32}\b",
        "<opaque-id>",
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
    )


class EvaluationModel(BaseChatModel):
    tap: object
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self):
        return "m8-controlled-model-boundary"

    def bind_tools(self, tools, **kwargs):
        return self  # Both arms invoke the exact shared canonical tool schemas below.

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.tap.invoke(messages))])


class ModelTap:
    def __init__(self, gateway, ledger, episode, model=None, limits=LIMITS):
        self.gateway, self.ledger, self.episode = gateway, ledger, episode
        self.model, self.limits = model, limits
        self.context = None
        self.queue, self.requests, self.attempted, self.pending = [], [], [], []
        self.responses = []
        self.schemas = schemas_for(gateway)
        self.revision, self.prior = 0, []

    def invoke(self, messages):
        self.ledger.remaining(self.episode)
        decisions = sum(r["type"] == "model_decision" for r in self.ledger.rows(self.episode))
        if decisions >= self.limits.requests:
            raise BudgetExceeded("episode_model_limit")
        self.ledger.step(self.episode)
        facts = self.gateway.state(self.context.session_id, UID)
        self.revision = ((facts["task"] or {}).get("task") or {}).get("revision", 0)
        self.prior = []
        native = [m for m in convert_to_messages(messages) if m.type != "system"]
        projected = [
            SystemMessage(content=FormalReturnPoc.prompt),
            *native,
            SystemMessage(content="当前应用核验事实：" + json.dumps(facts, ensure_ascii=False)),
        ]
        from langchain_core.messages import convert_to_openai_messages

        record = {"messages": convert_to_openai_messages(projected), "tools": self.schemas}
        # Match the locked ChatOpenAI serializer: ToolMessage.name/status are
        # internal metadata, not fields sent to the chat-completions endpoint.
        for message in record["messages"]:
            if message["role"] == "tool":
                message.pop("name", None)
            elif message["role"] == "assistant" and message.get("tool_calls"):
                message["content"] = message.get("content") or None
        self.requests.append(record)
        self.ledger.event(self.episode, "model_decision", request_digest=digest(record))
        if self.model is None:
            if not self.queue:
                raise RuntimeError("Frozen model outputs exhausted; no implicit replay")
            item = self.queue.pop(0)
            response = AIMessage(
                content=item.get("text", ""),
                tool_calls=item.get("calls", []),
                response_metadata={"finish_reason": "tool_calls" if item.get("calls") else "stop"},
            )
        else:
            response = self.model.bind_tools(self.schemas).invoke(projected)
        used_ids = {c["id"] for r in self.responses for c in r.get("tool_calls", [])}
        self.responses.append(response.model_dump())
        require_response(
            response.content,
            [dict(id=c["id"], name=c["name"], arguments=c["args"]) for c in response.tool_calls],
            response.response_metadata.get("finish_reason"),
            known_tools=set(TOOLS),
            used_ids=used_ids,
            invalid_calls=response.invalid_tool_calls,
        )
        self.pending = list(response.tool_calls)
        self.attempted.extend(response.tool_calls)
        return response

    def attach_meter(self):
        execute = self.gateway.execute

        def metered(name, args, call_id, context, **kwargs):
            old = [
                r
                for r in self.ledger.rows(self.episode)
                if r["type"] == "tool_intent" and r["call"]["id"] == call_id
            ]
            if old:
                with closing(self.gateway.store._connect()) as conn:
                    known = conn.execute(
                        "SELECT 1 FROM local_receipts WHERE session_id=? "
                        "AND turn_id=? AND call_id=?",
                        (context.session_id, context.turn_id, call_id),
                    )
                    if not known.fetchone():
                        raise ReconciliationError("Unknown tool result; never replay write")
            else:
                self.ledger.tool_intent(
                    self.episode, {"id": call_id, "name": name, "arguments": args}
                )
            result = execute(name, args, call_id, context, **kwargs)
            if not old:
                self.ledger.tool_receipt(self.episode, {"id": call_id, "error": result["error"]})
            return result

        self.gateway.execute = metered


class OldModelBridge:
    def __init__(self, tap):
        self.tap = tap

    def reset_usage(self):
        pass

    def totals(self):
        return {"calls": len(self.tap.requests)}

    def chat(self, messages, **kwargs):
        m = self.tap.invoke(messages)
        return LLMResponse(
            m.text,
            Usage(model="m8-controlled", stage="agent"),
            [
                ToolCall(c["id"], c["name"], json.dumps(c["args"], ensure_ascii=False))
                for c in m.tool_calls
            ],
            finish_reason=m.response_metadata["finish_reason"],
        )


class OldGatewayBridge:
    def __init__(self, tap):
        self.tap = tap

    def schemas(self):
        return self.tap.schemas

    def run(self, name, args, *, current_user_id):
        call = self.tap.pending.pop(0)
        if current_user_id != UID or name != call["name"] or json.loads(args) != call["args"]:
            raise PermissionError("Loop tool dispatch diverged from model call")
        receipt = self.tap.gateway.execute(
            name,
            call["args"],
            call["id"],
            self.tap.context,
            expected_revision=self.tap.revision,
            prior_writes=self.tap.prior,
        )
        if name in WRITES:
            self.tap.prior.append(call["id"])
        return (
            receipt,
            [
                TraceEvent(type="tool_call", label=name, detail={}),
                TraceEvent(type="tool_result", label=name, detail=receipt),
            ],
            receipt["error"],
        )


class SimulatedLostReceipt(RuntimeError):
    pass


def run_task(task, contracts, root, arm, *, model=None, ledger=None, limits=LIMITS):
    """One fresh DB per arm/task. All business writes use the existing gateway."""
    if task.cohort != "common" and arm != "v3":
        return {"task_id": task.task_id, "arm": arm, "status": "not_applicable"}
    started = time.perf_counter()
    records, error = [], None
    with business_clock():
        initial = build_fixture(root, task)
        immutable = digest(business_rows(root))
        runtime = FormalReturnPoc(root, UID, model=EvaluationModel(tap=None))
        gateway, store = runtime.gateway, runtime.store
        sid = store.create(UID)
        ledger = ledger or EpisodeLedger(root / "fees.jsonl", cap_cny=5, max_requests=48)
        episode = task.task_id + ":" + arm
        ledger.begin(episode, limits)
        tap = ModelTap(gateway, ledger, episode, model, limits)
        runtime.model.tap = tap
        tap.attach_meter()
        old = None
        if arm == "old":
            settings = runtime.settings.model_copy(
                update={
                    "max_tool_calls": 16,
                    "max_retrieval_calls": 16,
                    "context_max_chars": 1000000,
                }
            )
            old = CustomerServiceAgent(
                settings=settings,
                llm=OldModelBridge(tap),
                registry=OldGatewayBridge(tap),
                max_steps=8,
            )
        else:
            runtime.__enter__()
        wait_id = None
        try:
            for index, step in enumerate(task.steps):
                tick = time.perf_counter()
                before = snapshot(gateway, sid)
                attempted_start, receipt_start = len(tap.attempted), len(before["receipts"])
                raw, display, finding = None, None, []
                recovery, recovery_diagnostic = None, None
                if step.action == "chat":
                    tap.queue = list(contracts[index]) if model is None else []
                    if old:
                        tid = store.begin_turn(sid, UID, step.text)
                        with store.transaction() as conn:
                            conn.execute(
                                "INSERT INTO local_threads VALUES(?,?,?,0) "
                                "ON CONFLICT(session_id) DO UPDATE SET turn_id=excluded.turn_id",
                                (sid, "old-evaluation:" + sid, tid),
                            )
                        tap.context = TurnContext(sid, UID, tid)
                        offset = len(old.messages)
                        response = old.chat(step.text)
                        raw = response.answer
                        if response.stop_reason != "answered":
                            store.interrupt_turn(sid, UID, tid, raw)
                            raise RuntimeError("Old Loop stopped: " + response.stop_reason)
                        store.finish_turn(
                            sid,
                            UID,
                            tid,
                            old.messages[offset:],
                            {"answer": raw, "stop_reason": "answered"},
                        )
                    else:
                        for event in runtime.stream(sid, step.text):
                            if event.type == "started":
                                tid = event.run_id
                                tap.context = TurnContext(sid, UID, tid)
                            if event.type == "completed":
                                raw = event.detail["answer"]
                        if raw is None:
                            raise RuntimeError("No final native answer")
                    e = collect_evidence(gateway, root, sid, UID, tid)
                    result = review_answer(raw, e)
                    display, finding = result.text, [f.model_dump() for f in result.findings]
                elif step.action == "expire":
                    with store.transaction() as conn:
                        conn.execute(
                            "UPDATE return_previews SET expires_at=? WHERE session_id=?",
                            ("2026-10-25T03:59:00+00:00", sid),
                        )
                elif step.action == "policy_change":
                    p = root / "docs/return_policy.md"
                    p.write_text(p.read_text() + "\n合成评测政策修订：仍需应用确认。\n")
                elif step.action == "open":
                    result = runtime.submission(sid, "open")
                    wait_id = result["wait_id"]
                elif step.action in {"accept", "reject", "cancel"}:
                    result = runtime.submission(
                        sid, "decide", wait_id=wait_id, decision=step.action
                    )
                elif step.action == "lost_commit_receipt":

                    def fault(point):
                        if point == "after_consume_commit":
                            raise SimulatedLostReceipt("SQL committed; application receipt lost")

                    try:
                        runtime.submission(
                            sid, "decide", wait_id=wait_id, decision="accept", fault=fault
                        )
                        raise AssertionError("Fault injection was not reached")
                    except SimulatedLostReceipt:
                        recovery = "unknown_pending_read_only_reconciliation"
                elif step.action == "restart_lookup":
                    runtime.__exit__(None, None, None)
                    runtime.__enter__()
                    result = runtime.submission(sid, "lookup", wait_id=wait_id)
                    recovery = bool(result and result["request"]["status"] == "pending_review")
                    # Read receipt first; then diagnose the mirror without replaying a write.
                    mirror = runtime.submission(sid, "resume", wait_id=wait_id)
                    recovery_diagnostic = {
                        "authority_verified": bool(result),
                        "graph_mirror": mirror.get("graph_mirror"),
                    }
                else:
                    raise ValueError("Unknown application action " + step.action)
                state = snapshot(gateway, sid)
                state["previous_preview_count"] = before["preview_count"]
                sql_ok = subset(state, step.expect)
                sql_ok &= all(
                    state["counts"][t] == state["requests"]
                    for t in ("return_confirmations", "return_audit")
                )
                sql_ok &= digest(business_rows(root)) == immutable
                if state["requests"]:
                    # Full authoritative receipt, not an answer or the Graph checkpoint.
                    verified = runtime.submission(sid, "lookup", wait_id=wait_id)
                    sql_ok &= bool(verified) and all(
                        state["counts"][t] == state["requests"]
                        for t in ("return_confirmations", "return_audit")
                    )
                sql_ok &= state["counts"]["return_reviews"] == 0
                tool_ok = score_tools(
                    step, tap.attempted[attempted_start:], state["receipts"][receipt_start:]
                )
                records.append(
                    {
                        "step": index,
                        "action": step.action,
                        "sql_correct": bool(sql_ok),
                        "tool_valid": tool_ok,
                        "raw": raw,
                        "raw_goal_covered": raw is None
                        or all(
                            any(part in raw for part in atom.split("|"))
                            for atom in step.answer_requires
                        ),
                        "display_goal_covered": display is None
                        or all(
                            any(part in display for part in atom.split("|"))
                            for atom in step.answer_requires
                        ),
                        "display": display,
                        "findings": finding,
                        "raw_grade": assess_answer(raw, step.answers, state)
                        if raw is not None
                        else None,
                        "display_grade": assess_answer(display, step.answers, state)
                        if display is not None
                        else None,
                        "recovery": recovery,
                        "recovery_diagnostic": recovery_diagnostic,
                        "latency_ms": (time.perf_counter() - tick) * 1000,
                        "state": state,
                        "attempted": tap.attempted[attempted_start:],
                    }
                )
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            if arm == "v3":
                runtime.__exit__(None, None, None)
        final = snapshot(gateway, sid)
        (root / "model-outputs.json").write_text(
            json.dumps(tap.responses, ensure_ascii=False, default=str)
        )
        (root / "model-inputs.json").write_text(
            json.dumps(tap.requests, ensure_ascii=False, default=str)
        )
        sql_ok = len(records) == len(task.steps) and all(r["sql_correct"] for r in records)
        tools_ok = len(records) == len(task.steps) and all(r["tool_valid"] for r in records)
        answers = [r for r in records if r["raw"] is not None]
        faithful = bool(answers) and all(
            r["raw_grade"]["status"] == "faithful" and r["raw_goal_covered"] for r in answers
        )
        result = {
            "task_id": task.task_id,
            "split": task.split,
            "cohort": task.cohort,
            "arm": arm,
            "kind": "sdk_injected_model" if model is not None else "fixed_decision_contract",
            "status": "completed" if error is None else "failed",
            "error": error,
            "initial_fingerprint": initial,
            "steps": records,
            "final": final,
            "sql_correct": sql_ok,
            "tool_valid": tools_ok,
            "task_success": sql_ok and tools_ok and faithful and error is None,
            "model_calls": len(tap.requests),
            "model_http_requests": ledger.summary(episode)["requests"],
            "input_tokens": None
            if model is None
            else ledger.summary(episode)["roles"]["agent"]["prompt_tokens"],
            "output_tokens": None
            if model is None
            else ledger.summary(episode)["roles"]["agent"]["completion_tokens"],
            "latency_ms": (time.perf_counter() - started) * 1000,
            "chat_latency_ms": sum(r["latency_ms"] for r in records if r["action"] == "chat"),
            "cost": ledger.summary(episode)
            if model is not None
            else {"paid_cny": 0, "token_estimate_not_actual": True},
            "request_signatures": [
                hashlib.sha256(normalized(r).encode()).hexdigest() for r in tap.requests
            ],
        }
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def summarise(rows, include_splits=True):
    reports = {}
    for cohort in ("common", "incremental"):
        for arm in ("old", "v3"):
            selected = [r for r in rows if r.get("cohort") == cohort and r["arm"] == arm]
            if not selected:
                continue
            turns = [s for r in selected for s in r["steps"] if s["raw"] is not None]
            changed = [s for s in turns if s["raw"] != s["display"]]
            raw_bad = [s for s in turns if s["raw_grade"]["status"] == "unfaithful"]
            corrected = [s for s in raw_bad if s["display_grade"]["status"] == "faithful"]
            missed = [s for s in raw_bad if s["display_grade"]["status"] == "unfaithful"]
            good_changed = [s for s in changed if s["raw_grade"]["status"] == "faithful"]
            false = [s for s in good_changed if s["display_grade"]["status"] == "unfaithful"]
            unresolved_changes = sum(s["display_grade"]["status"] == "unresolved" for s in changed)
            recoveries = [
                s for r in selected for s in r["steps"] if isinstance(s["recovery"], bool)
            ]
            reports[cohort + ":" + arm] = {
                "episodes": len(selected),
                "task_success": sum(r["task_success"] for r in selected),
                "sql_correct": sum(r["sql_correct"] for r in selected),
                "tool_valid": sum(r["tool_valid"] for r in selected),
                "answer_turns": len(turns),
                "raw_faithful": sum(s["raw_grade"]["status"] == "faithful" for s in turns),
                "display_faithful": sum(s["display_grade"]["status"] == "faithful" for s in turns),
                "raw_errors": len(raw_bad),
                "raw_goal_covered_turns": sum(s.get("raw_goal_covered", False) for s in turns),
                "display_goal_covered_turns": sum(
                    s.get("display_goal_covered", False) for s in turns
                ),
                "corrected_errors": len(corrected),
                "missed_errors": len(missed),
                "changed_correct_answers": len(good_changed),
                "false_corrections": len(false),
                "changed_answers": len(changed),
                "unresolved_changes": unresolved_changes,
                "unresolved_raw": sum(s["raw_grade"]["unresolved"] for s in turns),
                "unresolved_display": sum(s["display_grade"]["unresolved"] for s in turns),
                "correction_rate": len(corrected) / len(raw_bad) if raw_bad else None,
                "false_correction_rate_among_changes": len(false) / len(changed)
                if changed and not unresolved_changes
                else None,
                "miss_rate": len(missed) / len(raw_bad) if raw_bad else None,
                "recovery_success": sum(s["recovery"] for s in recoveries),
                "recovery_attempts": len(recoveries),
                "business_success_answer_wrong": sum(
                    r["sql_correct"]
                    and any(
                        s["raw_grade"] and s["raw_grade"]["status"] == "unfaithful"
                        for s in r["steps"]
                    )
                    for r in selected
                ),
                "model_decisions": sum(r["model_calls"] for r in selected),
                "model_http_requests": sum(r["model_http_requests"] for r in selected),
                "input_tokens": None
                if all(r["input_tokens"] is None for r in selected)
                else sum(r["input_tokens"] or 0 for r in selected),
                "output_tokens": None
                if all(r["output_tokens"] is None for r in selected)
                else sum(r["output_tokens"] or 0 for r in selected),
                "latency_ms": sum(r["latency_ms"] for r in selected),
                "chat_latency_ms": sum(r.get("chat_latency_ms", 0) for r in selected),
                "temporary_stream_faithfulness": "not_measured_by_nonstream_runner",
            }
    pairs = []
    for old in [r for r in rows if r.get("cohort") == "common" and r["arm"] == "old"]:
        new = next((r for r in rows if r["task_id"] == old["task_id"] and r["arm"] == "v3"), None)
        if new is None:
            continue
        pairs.append(
            {
                "task_id": old["task_id"],
                "same_initial_db": bool(old["initial_fingerprint"])
                and old["initial_fingerprint"] == new["initial_fingerprint"],
                "same_normalized_model_inputs": old["request_signatures"]
                == new["request_signatures"],
                "same_sql_checks": old["sql_correct"] == new["sql_correct"],
                "same_final_business_state": bool(old["final"])
                and normalized(old["final"]) == normalized(new["final"]),
                "same_tool_calls": [s["attempted"] for s in old["steps"]]
                == [s["attempted"] for s in new["steps"]],
                "old_success": old["task_success"],
                "v3_success": new["task_success"],
            }
        )
    result = {
        "metrics": reports,
        "pairs": pairs,
        "paid_model_quality_comparison": False,
        "interpretation": "Fixed outputs verify protocol and graders; no LLM quality inference",
    }

    if include_splits:
        result["by_split"] = {
            split: summarise([r for r in rows if r.get("split") == split], False)["metrics"]
            for split in ("development", "test")
        }
    return result
