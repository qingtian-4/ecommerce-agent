"""Offline v2.2 data audit and replay of the already observed EC03 tool choice."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acs import commerce_benchmark as prior  # noqa: E402
from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.commerce_benchmark_v2 import load, run_case  # noqa: E402
from acs.commerce_benchmark_v2_data import ROOT, build  # noqa: E402
from acs.config import PROJECT_ROOT, Settings  # noqa: E402
from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.live_evaluation import LIMITS  # noqa: E402
from acs.rag import KnowledgeBase  # noqa: E402


def audit_data(suite, out):
    with TemporaryDirectory(prefix="commerce-v22-audit-") as tmp, prior.no_network():
        root = Path(tmp)
        rebuilds = {}
        for revision, folder in [("local-commerce-v2", "commerce_benchmark_v2"),
                                 ("local-commerce-v2.1", "commerce_benchmark_v2_1"),
                                 ("local-commerce-v2.2", "commerce_benchmark_v2_2")]:
            source = ROOT.parent / folder
            target = root / folder
            assert build(target, revision=revision) == load(source)["manifest"]
            assert all(p.read_bytes() == (target / p.relative_to(source)).read_bytes()
                       for p in source.rglob("*") if p.is_file())
            rebuilds[revision] = file_hash(source / "manifest.json")
        db = root / "business.sqlite"
        prior.seed_database(db, suite["business"])
        with sqlite3.connect(db) as conn:
            orders = {r[0]: r[1] for r in conn.execute("SELECT order_id,user_id FROM orders")}
            items = {r[0]: r[1:] for r in conn.execute(
                "SELECT id,order_id,product_id,quantity FROM order_items")}
            products = {r[0] for r in conn.execute("SELECT product_id FROM products")}
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert not conn.execute(
                "SELECT o.order_id FROM orders o JOIN order_items i USING(order_id) "
                "GROUP BY o.order_id HAVING total_amount!=SUM(quantity*purchase_price)"
            ).fetchall()
        rows = []
        for entry, task in zip(suite["entries"], suite["tasks"], strict=True):
            refs, claims = 0, 0
            for step, gold, replay in zip(task.steps, entry["tool_gold"],
                                          suite["contracts"][task.task_id], strict=True):
                calls = [dict(name=c["name"], arguments=c["args"])
                         for d in replay for c in d.get("calls", [])]
                assert calls == [dict(name=g["name"], arguments=g["arguments"]) for g in gold]
                if step.action == "chat":
                    assert replay[-1]["text"] in {a.text for a in step.answers}
                draft = step.expect.get("draft") or {}
                if draft.get("item_id") is not None:
                    oid, _, quantity = items[draft["item_id"]]
                    assert oid == draft["order_id"] and orders[oid] == "USER001"
                    assert 0 < draft["quantity"] <= quantity
                texts = [step.text] + [a.text for a in step.answers]
                for text in texts:
                    assert set(re.findall(r"O\d{5}", text)) <= orders.keys()
                    assert set(re.findall(r"B\d{3}", text)) <= products
                    assert {int(i) for i in re.findall(r"明细\s*(\d+)", text)} <= items.keys()
                for answer in step.answers:
                    refs += 1
                    for c in answer.claims:
                        claims += 1
                        assert c.span in answer.text
                        if c.fact.startswith(("product.", "price.", "order.")):
                            assert c.fact.split(".")[1] in re.findall(r"[A-Z]+\d+", c.span)
                        if c.fact.startswith("draft."):
                            key = c.fact.split(".", 1)[1]
                            assert key in draft and draft[key] == c.value
                            if key in {"item_id", "order_id"} and c.value is not None:
                                assert re.search(r"(?<![A-Za-z0-9])" + re.escape(str(c.value))
                                                 + r"(?![A-Za-z0-9])", c.span)
                # All structured tool references must resolve to actual SQL entities.
                for call in calls:
                    args = call["arguments"]
                    if "order_id" in args:
                        assert args["order_id"] in orders
                    if "product_id" in args:
                        assert args["product_id"] in products
                    if args.get("item_id") is not None:
                        assert items[args["item_id"]][0] == args["order_id"]
            rows.append(dict(task_id=task.task_id, family=entry["family"], steps=len(task.steps),
                             references=refs, claims=claims, structural_consistency=True))
        chunks = KnowledgeBase(Settings(_env_file=None, docs_dir=ROOT / "docs",
                                        chunk_size=700, chunk_overlap=80)).load_documents()
        rag = []
        for case in suite["rag"]["cases"]:
            groups = []
            for alternatives in case["source_alternatives"]:
                for source in alternatives:
                    assert any(c.document_id == source["document_id"]
                               and source["text"] in c.content for c in chunks)
                groups.append([c.chunk_id for c in chunks if any(
                    c.document_id == s["document_id"] and s["text"] in c.content
                    for s in alternatives)])
            assert groups == case["evidence_groups"]
            assert bool(groups) == (case["answerability"] == "supported")
            assert "首要规格" not in case["query"]
            if case["category"] in {"product", "multifact"}:
                pids = re.findall(r"B\d{3}", case["query"])
                assert len(groups) == len(pids)
                for pid, alternatives in zip(pids, case["source_alternatives"], strict=True):
                    assert all(s["text"].startswith(pid + "：") for s in alternatives)
            rag.append(dict(id=case["id"], query=case["query"],
                            answer_facts=case["answer_facts"], evidence_groups=groups,
                            source_alternatives=case["source_alternatives"], mapping_valid=True))
    report = dict(rebuilt_manifests=rebuilds, tasks=rows, rag=rag,
                  scope="60 seen instances / 12 families; 120 evidence mappings",
                  semantic_review="author-assisted audit, not independent human review",
                  model_http_requests=0)
    dump(out / "data_audit.json", report)
    return report


def replay_catalog(suite, out):
    from eval_live_readiness import model_for

    source = (PROJECT_ROOT
              / "results/commerce_comparison_20261027/deepseek_pilot/failure_evidence.json")
    evidence = json.loads(source.read_text())
    entry = next(e for e in suite["entries"] if e["task"]["task_id"] == "EC03-1")
    reports = []
    for arm in ("old", "v3"):
        root = out / ".private-evidence" / ("ec03-sdk-" + arm)
        (root / ".private-evidence").mkdir(parents=True, exist_ok=False)
        ledger = EpisodeLedger(root / "fees.jsonl", cap_cny=5, max_requests=40)
        episode = "EC03-1:" + arm
        ledger.begin(episode, LIMITS)
        queue = [dict(calls=[dict(id=f"observed-{n}", name=c["name"], args=c["arguments"])
                             for n, c in enumerate(evidence["known_tool_receipts"])])]
        queue += [copy.deepcopy(d) for turn in suite["contracts"]["EC03-1"] for d in turn]
        with model_for(root, ledger, episode, queue=queue) as model:
            result = run_case(suite, entry, root / "fixture", arm, model=model,
                              ledger=ledger, limits=LIMITS)
        bodies = [e["body_bytes"] for e in ledger.rows() if e["type"] == "mock_http_observed"]
        assert not queue and not result["global_stop"]
        assert result["sql_correct"] and result["tool_valid"] and result["raw_outcome"] == "pass"
        assert max(bodies) <= 32768
        reports.append(dict(arm=arm, body_bytes=bodies, max_body_bytes=max(bodies),
                            sql_correct=True, tool_valid=True, authored_answer="pass",
                            final_draft=result["final"]["draft"],
                            formal_requests=result["final"]["requests"],
                            mock_requests=len(bodies), real_http_requests=0))
    report = dict(source=str(source.relative_to(PROJECT_ROOT)), source_sha256=file_hash(source),
                  historical_body_bytes=40104, unchanged_body_limit=32768, arms=reports,
                  limitation="Observed initial tool choice; later decisions scripted, not quality")
    dump(out / "ec03_replay.json", report)
    return report


def scan_catalog(suite, out, arms=("old", "v3")):
    """Exercise accumulated context, including the response AFTER the last page."""
    from eval_live_readiness import model_for

    reports = []
    for arm in arms:
        entry = copy.deepcopy(suite["entries"][0])
        task = entry["task"]
        task["task_id"] = "catalog-scan-control"
        task["steps"] = task["steps"][:1]
        task["steps"][0].update(text="查询所有页，查完才能算完整目录。", answers=[])
        entry["tool_gold"] = [[]]
        root = out / ".private-evidence" / arm
        (root / ".private-evidence").mkdir(parents=True, exist_ok=False)
        ledger = EpisodeLedger(root / "fees.jsonl", max_requests=40)
        episode = task["task_id"] + ":" + arm
        ledger.begin(episode, LIMITS)
        queue = [dict(calls=[dict(id=f"page-{n}", name="list_products",
                                  args={"after_product_id": after} if after else {})])
                 for n, after in enumerate(["", "B108", "B208", "B308", "B408"])]
        queue.append(dict(text="目录五页已查询。"))
        with model_for(root, ledger, episode, queue=queue) as model:
            result = run_case(suite, entry, root / "fixture", arm, model=model,
                              ledger=ledger, limits=LIMITS)
        bodies = [e["body_bytes"] for e in ledger.rows() if e["type"] == "mock_http_observed"]
        candidates = [p["product_id"] for receipt in result["final"]["receipts"]
                      for p in json.loads(receipt["result"])["products"]]
        assert candidates == sorted(p["id"] for p in suite["business"]["products"])
        assert not queue and not result["global_stop"] and max(bodies) <= 32768
        assert result["sql_correct"] and result["tool_valid"]
        # No synthetic language annotation: success here concerns enumeration only.
        assert result["raw_outcome"] == "pending_review"
        reports.append(dict(arm=arm, candidate_count=len(candidates), candidates=candidates,
                            body_bytes=bodies, max_body_bytes=max(bodies),
                            mock_requests=len(bodies), real_http_requests=0,
                            answer_status="pending_review"))
    report = dict(arms=reports, unchanged_body_limit=32768)
    dump(out / "catalog_scan.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--mode", choices=["data", "replay", "scan"], required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {"data": audit_data, "replay": replay_catalog, "scan": scan_catalog}[args.mode](
        load(ROOT), args.out)
    print(json.dumps({"mode": args.mode, "artifacts": str(args.out),
                      "sections": list(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
