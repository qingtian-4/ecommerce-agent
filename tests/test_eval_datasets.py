"""Dataset distribution and synthetic business facts, independent of model answers."""

import re
from collections import Counter

import yaml

from acs.database import BusinessDB
from acs.eval_runner import CASES_PATH
from acs.rag import KnowledgeBase


def load(name):
    return yaml.safe_load((CASES_PATH.parent / name).read_text())["cases"]


def test_new_verification_queries_and_orders_did_not_reuse_the_original_set():
    original, heldout = load("cases.yaml"), load("holdout.yaml")
    assert len(heldout) == 15
    assert not {c["id"] for c in original} & {c["id"] for c in heldout}
    assert not set(re.findall(r"O\d+", str(original))) & set(re.findall(r"O\d+", str(heldout)))


def test_development_distribution_preserves_frozen_cases_and_adds_missing_coverage(settings):
    cases = load("development.yaml")
    assert cases[:35] == load("cases.yaml")
    assert len(cases) == len({c["id"] for c in cases}) == 70
    assert sum("turns" in c for c in cases) == 10
    assert sum(c["user"] == "USER003" for c in cases) >= 8
    assert sum(bool(c.get("forbidden_tools")) for c in cases) >= 30
    products = Counter(p for c in cases for p in c.get("product_ids", []))
    assert all(products[p] >= 3 for p in ("P001", "P002", "P003", "P004"))
    db = BusinessDB(settings)
    states = Counter()
    chunks = {c.chunk_id for c in KnowledgeBase(settings).load_documents()}
    for case in cases:
        for turn in case.get("turns") or [case]:
            assert not set(turn.get("required_tools", [])) & set(turn.get("forbidden_tools", []))
            facts = dict(turn.get("order_facts") or {})
            if facts:
                order = db.get_order(facts.pop("order_id"), case["user"])
                assert order["found"]
                assert all(order[k] == v for k, v in facts.items()), (case["id"], facts)
                states[order["status"]] += 1
            for group in turn.get("required_chunk_groups", []):
                assert group and set(group) <= chunks
    assert states["pending"] >= 2  # plus the preserved original pending case
    assert states["cancelled"] >= 3
