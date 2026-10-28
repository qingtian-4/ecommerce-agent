"""Reuse native runtimes and strict coverage; no keyword judge for new prose."""
from __future__ import annotations

import json
from unittest.mock import patch

from . import commerce_benchmark as prior
from . import commerce_complex_eval as complex_eval
from . import multiturn_evaluation as m8
from .commerce_benchmark_v2 import run_case
from .cs_tools import CatalogArgs, catalog_progress
from .live_evaluation import LIMITS
from .return_rules import consultation_policy
from .shop_demo_data import materialize

BASE_SNAPSHOT = prior.authoritative_snapshot
BASE_CLAIM = prior.claim_value
BASE_EVIDENCE = complex_eval.evidence_present


def snapshot(gateway, sid):
    state = BASE_SNAPSHOT(gateway, sid)
    state["consultation"] = consultation_policy(gateway.settings) or {}
    with gateway.store._connect() as conn:
        state["sku_anc"] = {}
        for row in conn.execute("SELECT product_id,anc FROM product_skus"):
            state["sku_anc"].setdefault(row[0], []).append(row[1])
        state["cheap_gas"] = sorted(r[0] for r in conn.execute(
            "SELECT product_id FROM products WHERE category='燃气热水器'")
            if state["prices"][r[0]] <= 1000)
        state["owned_in_ear_count"] = conn.execute(
            "SELECT COUNT(DISTINCT o.order_id) FROM orders o JOIN order_items i USING(order_id) "
            "JOIN products p USING(product_id) WHERE o.user_id=? AND p.name LIKE '%入耳%'",
            (m8.UID,)).fetchone()[0]
    return state


def claim_value(claim, state):
    if claim.fact.startswith("consultation."):
        return state["consultation"].get(claim.fact.split(".", 1)[1]) == claim.value
    if claim.fact.startswith("sku."):
        values = state["sku_anc"].get(claim.fact.split(".")[1], [])
        return bool(values) and all(v == claim.value for v in values)
    if claim.fact == "candidate_ids":
        return state["cheap_gas"] == claim.value
    if claim.fact == "catalog_count":
        return len(state["products"]) == claim.value
    if claim.fact == "owned_in_ear_count":
        return state[claim.fact] == claim.value
    return BASE_CLAIM(claim, state)


def evidence_present(goal, record, receipts, previous_state):
    e = goal["evidence"]
    values = [(r["name"], json.loads(r["result"]), json.loads(r["arguments"]))
              for r in receipts if not r["error"]]
    if e["kind"] == "sku":
        all_values = [(r["name"], json.loads(r["result"]))
                      for r in record["state"]["receipts"] if not r["error"]]
        products = [p for n, v in all_values for p in
                    (v.get("products", []) if n == "list_products" else [v]
                     if n == "get_product" and v.get("found") else [])]
        return any(p.get("product_id") == e["product_id"] and p.get("skus")
                   and all(e["field"] in s and s[e["field"]] == e["value"] for s in p["skus"])
                   for p in products)
    if e["kind"] == "complete_catalog":
        return any(q["complete"] and q["seen_count"] == e["count"]
                   for q in catalog_progress(receipts))
    if e["kind"] == "exact_candidates":
        for progress in catalog_progress(receipts):
            query = progress["query"]
            # A broader complete catalog is legitimate; a narrower price slice is not proof.
            if (not progress["complete"] or query.get("max_price", float("inf"))
                    < e["arguments"]["max_price"]):
                continue
            products = {}
            for n, value, args in values:
                if n != "list_products":
                    continue
                normalized = CatalogArgs.model_validate(args).model_dump(
                    exclude={"after_product_id", "page_size", "view"}, exclude_defaults=True)
                if normalized == query:
                    products.update({p["product_id"]: p for p in value["products"]})
            if not all("category" in p and "current_price" in p for p in products.values()):
                continue
            selected = {pid for pid, p in products.items()
                        if p["category"] == e["arguments"]["category"]
                        and p["current_price"] <= e["arguments"]["max_price"]}
            if selected == set(e["ids"]):
                return True
        return False
    if e["kind"] == "owned_orders":
        return any(n == "list_orders" and a.get("name_contains", "") in {"", e["query"]}
                   and len({o["order_id"] for o in v["orders"] if any(
                       e["query"] in i["product_name"] for i in o["items"])}) == e["count"]
                   for n, v, a in values)
    if e["kind"] == "refusal" and not any(n in {"get_order", "get_logistics"}
            and v.get("order_id") == e["order_id"] for n, v, _ in values):
        return False  # all([]) must not certify an unobserved access check.
    found = BASE_EVIDENCE(goal, record, receipts, previous_state)
    if not found and e["kind"] == "product" and "attribute" in e:
        return any(d.get("document_id") == e["product_id"]
                   and e["attribute"] in d.get("content", "")
                   for r in record["state"]["receipts"] if r["name"] == "search_knowledge"
                   and not r["error"] for d in json.loads(r["result"]).get("documents", []))
    return found


def grade(entry, result):
    with patch.object(complex_eval, "evidence_present", evidence_present):
        return complex_eval.grade_objectives(entry, result)


def run(suite, entry, root, arm, *, model=None, ledger=None, offline=True):
    def fixture(path, ignored):
        materialize(path, suite["environment_root"])
        return m8.initial_fingerprint(path)

    with (patch.object(prior, "materialize", fixture),
          patch.object(prior, "authoritative_snapshot", snapshot),
          patch.object(prior, "claim_value", claim_value)):
        result = run_case(suite, entry, root, arm, model=model, ledger=ledger,
                          offline=offline, limits=LIMITS)
    result["legacy_raw_outcome"] = result["raw_outcome"]
    result.update(grade(entry, result))
    return result
