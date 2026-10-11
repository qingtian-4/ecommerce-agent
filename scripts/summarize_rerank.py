"""Recompute coverage and accounting from saved rerank evidence, without API calls."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "results/rerank"


def main() -> None:
    manifest = json.loads((BASE / "frozen_manifest.json").read_text())
    cases_path = ROOT / "data/eval/rerank_cases.json"
    assert manifest["cases_sha256"] == hashlib.sha256(cases_path.read_bytes()).hexdigest()
    cases = json.loads(cases_path.read_text())["cases"]
    comparison = {"manifest": manifest, "runs": {}, "records_verified": 0}
    previous = None
    for split in ("development", "validation"):
        path = BASE / split / "summary.json"
        report = json.loads(path.read_text())
        raw = (path.parent / "per_case.jsonl").read_bytes()
        assert report["records_sha256"] == hashlib.sha256(raw).hexdigest()
        assert all(report["inputs"][key] == manifest[key] for key in report["inputs"])
        assert report["conditions"] == manifest["conditions"]
        if previous:
            assert all(previous[key] == report[key]
                       for key in ("inputs", "conditions", "source_sha256"))
        previous = report
        rows = [json.loads(line) for line in raw.splitlines()]
        expected = {c["id"]: c for c in cases if c["split"] == split}
        assert len(rows) == len(expected) * len(manifest["variants"])
        assert len({(r["id"], r["mode"]) for r in rows}) == len(rows)
        for r in rows:
            assert all(r[key] == value for key, value in expected[r["id"]].items())
            assert len(r["accepted"]) <= 3 and len(r["verification_pool"]) <= 10
            assert {c["chunk_id"] for c in r["accepted"]} <= {
                c["chunk_id"] for c in r["verification_pool"]
            }
            for stage in ("candidate", "context", "accepted"):
                field = "candidates" if stage == "candidate" else stage
                hits = {c["chunk_id"] for c in r[field]}
                groups = r["evidence_groups"]
                coverage = [any(key in hits for key in group) for group in groups]
                assert r["metrics"][stage]["recall"] == (mean(coverage) if groups else None)
                assert r["metrics"][stage]["all_evidence"] == (all(coverage) if groups else None)
        for variant, m in report["summary"].items():
            part = [r for r in rows if r["mode"] == variant]
            supported = [r for r in part if r["evidence_groups"]]
            for stage in ("candidate", "context", "accepted"):
                assert math.isclose(m[f"{stage}_recall"],
                                    mean(r["metrics"][stage]["recall"] for r in supported))
                assert m[f"{stage}_all_evidence"] == sum(
                    r["metrics"][stage]["all_evidence"] for r in supported
                )
            assert m["errors"] == sum(r["error"] is not None for r in part)
            assert m["unsupported_accepted"] == sum(bool(r["accepted"]) for r in part
                                                    if not r["evidence_groups"])
            assert m["unsupported_rejected"] == sum(not r["accepted"] and r["error"] is None
                                                    for r in part if not r["evidence_groups"])
        # Shared embedding and rerank count once per query; all five verifications count.
        usages = [u for r in rows for u in r["verification_usage"]]
        usages += [u for r in rows if r["mode"] == "hybrid" for u in r["embedding_usage"]]
        usages += [u for r in rows if r["mode"] == "hybrid_rerank" for u in r["rerank_usage"]]
        assert len(usages) == report["actual_accounting"]["calls"]
        assert sum(u["total_tokens"] for u in usages) == report["actual_accounting"]["total_tokens"]
        comparison["runs"][split] = {
            "summary_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "summary": report["summary"], "actual_accounting": report["actual_accounting"],
        }
        comparison["records_verified"] += len(rows)
    decision = json.loads((BASE / "deployment_decision.json").read_text())
    assert decision["development_sha256"] == comparison["runs"]["development"]["summary_sha256"]
    assert (comparison["runs"]["development"]["actual_accounting"]["calls"] == 364
            and comparison["runs"]["validation"]["actual_accounting"]["calls"] == 140)
    comparison["deployment_decision"] = decision
    comparison["label_audit_notes"] = [
        "After first validation, charger-warranty-difference was found to omit charger_a#5 "
        "as an alternative: its 18-month warranty card also supports the duration. "
        "Frozen labels and scores remain unchanged; reference-section coverage is not an "
        "exhaustive judgment of all valid evidence or generated answer accuracy.",
    ]
    (BASE / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
    )
    print(f"Verified {comparison['records_verified']} records and shared accounting; no API calls.")


if __name__ == "__main__":
    main()
