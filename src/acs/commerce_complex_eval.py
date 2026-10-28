"""Evidence and authored answer coverage, separate from SQL and model quality."""

from __future__ import annotations

import copy
import json
import unicodedata
from collections import Counter
from pathlib import Path

from . import multiturn_evaluation as m8
from .commerce_benchmark import no_network, public_agent, seed_database
from .commerce_benchmark_data import dump, file_hash
from .commerce_benchmark_v2 import run_case
from .commerce_complex_data import VERSION
from .config import Settings
from .cs_tools import ToolRegistry
from .live_evaluation import LIMITS, protocol_pairs


def evidence_present(goal, record, receipts, previous_state):
    """Check facts in successful receipts, not the chosen plan or query spelling."""
    evidence = goal["evidence"]
    kind = evidence["kind"]
    values = [(r["name"], json.loads(r["result"])) for r in receipts if not r["error"]]
    if kind == "state":
        return m8.subset(record["state"], evidence["expected"])
    if kind == "write":
        return (any(name == "update_return_task" for name, _ in values)
                and m8.subset(record["state"]["draft"], evidence["expected"]))
    if kind == "no_write":
        return (not any(r["name"] in {"update_return_task", "cancel_return_task", "preview_return"}
                        for r in receipts)
                and record["state"]["draft"] == previous_state.get("draft")
                and not record["state"]["requests"])
    if kind == "refusal":
        matches = [v for n, v in values if n in {"get_order", "get_logistics"}
                   and v.get("order_id") == evidence["order_id"]]
        return all(v.get("found") is False for v in matches)
    if kind == "order":
        names = {"get_logistics"} if evidence["logistics"] else {"get_order", "get_logistics"}
        for name, value in values:
            if name in names and value.get("order_id") == evidence["order_id"]:
                if value.get("error_type") == "forbidden":
                    continue
                return True
            if (name == "list_orders" and not evidence["logistics"]
                    and any(o["order_id"] == evidence["order_id"] for o in value["orders"])):
                return True
        return False
    if kind == "policy":
        return (any(d.get("document_id") == evidence["document_id"]
                   and evidence["contains"] in d.get("content", "")
                   for name, value in values if name == "search_knowledge"
                   for d in value.get("documents", []))
                or any(evidence["document_id"] in json.dumps(v.get("policy", {}))
                       and evidence["contains"] in json.dumps(
                           v.get("policy", {}), ensure_ascii=False)
                       for _, v in values))
    if kind == "no_match":
        def literal(value):
            return "".join(unicodedata.normalize("NFKC", value).casefold().split())

        query = literal(evidence["query"])
        for r in receipts:
            if r["name"] != "list_products" or r["error"]:
                continue
            args, value = json.loads(r["arguments"]), json.loads(r["result"])
            if (literal(args.get("name_contains", "")) == query
                    and not args.get("category") and args.get("max_price") is None
                    and value.get("total_count") == 0 and value.get("query_complete") is True):
                return True
        # A full catalog scan is a legitimate, less efficient alternative proof.
        observed = {p["product_id"]: p for name, value in values if name == "list_products"
                    for p in value.get("products", [])}
        catalog = set(record["state"].get("products", {}))
        return (bool(catalog) and set(observed) == catalog
                and all("name" in p and query not in literal(p["name"])
                        for p in observed.values()))
    if kind == "product" and "current_price" not in evidence:
        # Immutable catalog facts can be reused from earlier successful reads.
        values = [(r["name"], json.loads(r["result"])) for r in record["state"]["receipts"]
                  if not r["error"]]
    candidates = [p for name, value in values
                  for p in (value.get("products", []) if name == "list_products"
                            else [value] if value.get("found") and (
                                name == "get_product" or name == "get_price_history"
                                and kind == "product" and "current_price" in evidence) else [])]
    if kind == "product" and "current_price" in evidence:
        for name, value in values:
            order = value if name == "get_order" else value.get("order", {})
            if name in {"get_order", "get_return_task"} and order and order.get("found"):
                candidates.extend(order.get("items", []))
    if kind == "candidates":
        return set(evidence["ids"]) <= {p["product_id"] for p in candidates}
    if kind == "product":
        for product in candidates:
            if product["product_id"] != evidence["product_id"]:
                continue
            if ("attribute" in evidence
                    and evidence["attribute"] not in product.get("attributes", [])):
                continue
            if ("current_price" in evidence
                    and product["current_price"] != evidence["current_price"]):
                continue
            return True
        # Knowledge is also legitimate for static specifications; never for current price.
        if "attribute" in evidence and "current_price" not in evidence:
            span = evidence["product_id"] + "：" + evidence["attribute"]
            return any(span in d.get("content", "") for name, value in values
                       if name == "search_knowledge" for d in value.get("documents", []))
        return False
    raise ValueError("Unknown evidence kind " + kind)


def grade_objectives(entry, result):
    previous, state, rows = 0, {"draft": None}, []
    for index, gold in enumerate(entry["intent_gold"]):
        if index >= len(result["steps"]):
            rows.extend(dict(step=index, goal=g["id"], evidence=False,
                             raw="not_executed", display="not_executed") for g in gold["goals"])
            continue
        record = result["steps"][index]
        receipts = record["state"]["receipts"][previous:]
        previous = len(record["state"]["receipts"])
        for goal in gold["goals"]:
            evidence = evidence_present(goal, record, receipts, state)
            row = dict(step=record["step"], goal=goal["id"], evidence=evidence)
            for kind in ("raw", "display"):
                covered = gold["answer_coverage"].get(record[kind])
                row[kind] = ("pending_review" if covered is None else
                             "pass" if goal["id"] in covered else "fail")
            rows.append(row)
        state = record["state"]

    def outcome(kind):
        original = result[kind + "_outcome"]
        if original in {"execution_error", "fail"}:
            return original
        if len(result["steps"]) != len(entry["intent_gold"]) or any(
            not r["evidence"] or r[kind] == "fail" for r in rows
        ):
            return "fail"
        if original == "pending_review" or any(r[kind] == "pending_review" for r in rows):
            return "pending_review"
        return "pass"

    raw_outcome = outcome("raw")
    return dict(objectives=rows, raw_outcome=raw_outcome, display_outcome=outcome("display"),
                task_success={"pass": True, "fail": False}.get(raw_outcome),
                semantic_scope="exact authored references only; novel language pending review")


def probe_entities(suite, out):
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    with no_network():
        db = out / ".private-evidence" / "business.sqlite"
        db.parent.mkdir(mode=0o700)
        seed_database(db, suite["business"])
        registry = ToolRegistry(Settings(_env_file=None, db_path=db))
        for case in suite["entity_probes"]:
            args, ids, receipts = {"name_contains": case["query"]}, [], []
            for _ in range(40):
                result, _, error = registry.run("list_products", json.dumps(args),
                                                current_user_id="USER001")
                if error or result.get("error_type"):
                    raise RuntimeError((case["id"], error, result))
                ids.extend(p["product_id"] for p in result["products"])
                receipts.append(dict(arguments=args, result=result))
                if not result["has_more"]:
                    break
                args = result["next_arguments"]
            else:
                raise RuntimeError("Catalog did not terminate")
            rows.append(dict(id=case["id"], query=case["query"], actual_ids=ids,
                             expected_ids=case["expected_ids"], passed=ids == case["expected_ids"],
                             ambiguity=len(ids) > 1, calls=len(receipts), receipts=receipts))
    report = dict(kind="deterministic_tool_entity_recall_not_NLU", cases=rows,
                  passed=sum(r["passed"] for r in rows), count=len(rows), paid_calls=0,
                  dataset=suite["manifest"], tool_source_sha256=file_hash(Path(__file__).with_name(
                      "cs_tools.py")))
    dump(out / "entity_scorecard.json", report)
    return report


def run_offline(suite, out):
    out.mkdir(parents=True, exist_ok=False)
    private = out / ".private-evidence"
    private.mkdir(mode=0o700)
    rows, controls = [], []
    for entry in suite["entries"]:
        for arm in ("old", "v3"):
            tid = entry["task"]["task_id"]
            print(tid, arm, flush=True)
            result = run_case(suite, entry, private / (tid + "-" + arm), arm, limits=LIMITS)
            result["legacy_raw_outcome"] = result["raw_outcome"]
            result.update(grade_objectives(entry, result))
            result["contract"] = VERSION
            rows.append(result)
            # Same real SQL/receipts; perturb only known authored response coverage.
            if arm == "v3":
                for i, gold in enumerate(entry["intent_gold"]):
                    for text, covered in gold["answer_coverage"].items():
                        if len(covered) == len(gold["goals"]):
                            continue
                        changed = copy.deepcopy(result)
                        changed["raw_outcome"] = result["legacy_raw_outcome"]
                        changed["steps"][i]["raw"] = text
                        grade = grade_objectives(entry, changed)
                        controls.append(dict(task_id=tid, step=i, omitted=[
                            g["id"] for g in gold["goals"] if g["id"] not in covered],
                            sql_unchanged=True, original_sql_correct=result["sql_correct"],
                            outcome=grade["raw_outcome"]))
            dump(private / "episodes.json", rows)
    report = dict(kind="scripted_decisions_actual_runtimes_not_model_baseline_quality",
                  dataset=suite["manifest"], paid_calls=0,
                  per_arm={arm: dict(Counter(r["raw_outcome"] for r in rows if r["arm"] == arm))
                           for arm in ("old", "v3")}, protocol_pairs=protocol_pairs(rows),
                  objective_evidence=sum(g["evidence"] for r in rows for g in r["objectives"]),
                  objective_count=sum(len(r["objectives"]) for r in rows),
                  source={n: file_hash(Path(__file__).with_name(n)) for n in [
                      "commerce_complex_eval.py", "cs_tools.py", "multiturn_evaluation.py"]})
    dump(out / "scorecard.json", report)
    dump(out / "episodes.json", [public_agent(r) | dict(arm=r["arm"], objectives=r["objectives"])
                                  for r in rows])
    dump(out / "badcases.json", [dict(task_id=r["task_id"], arm=r["arm"], error=r["error"],
                                      sql_correct=r["sql_correct"], tool_valid=r["tool_valid"],
                                      raw_outcome=r["raw_outcome"], objectives=r["objectives"])
                                  for r in rows if r["raw_outcome"] != "pass"])
    dump(out / "omission_controls.json", controls)
    return report
