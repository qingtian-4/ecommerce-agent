"""Offline official-protocol acceptance. Never constructs a paid model client."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PIN = "dd8757d4c8791dad7b3cdaf02a105a3d5f9d86bd"
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_upstream(upstream):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(upstream), *args], text=True).strip()

    if git("rev-parse", "HEAD") != PIN or git("status", "--porcelain"):
        raise ValueError("A clean original dd8757d checkout is required")
    dist = importlib.metadata.distribution("tau2")
    if dist.version != "1.0.1":
        raise ValueError("tau2 must be 1.0.1 at the pinned commit")
    source = json.loads(dist.read_text("direct_url.json"))
    if source.get("vcs_info", {}).get("commit_id") != PIN:
        raise ValueError("Installed tau2 was not built from the pinned commit")
    sources = {}
    for original in sorted((upstream / "src/tau2").rglob("*.py")):
        relative = original.relative_to(upstream / "src")
        if digest(original) != digest(Path(dist.locate_file(str(relative)))):
            raise ValueError("Installed official source differs: " + str(relative))
        sources[str(original.relative_to(upstream))] = digest(original)
    data = {
        str(p.relative_to(upstream)): digest(p)
        for p in sorted((upstream / "data/tau2/domains/retail").rglob("*"))
        if p.is_file()
    }
    return {
        "commit": PIN,
        "version": dist.version,
        "source_hashes": sources,
        "retail_data_hashes": data,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    upstream, out = args.upstream.resolve(), args.out.resolve()
    manifest = verify_upstream(upstream)
    manifest.update(
        {
            "task_ids": ["33", "34"],
            "seed": 17,
            "mode": "fixed scripted decisions; protocol only; no quality estimate",
            "evaluation": "official ALL / strict_replay / original DB-only tasks",
            "network": "socket.connect forbidden by mandatory autouse fixture",
            "paid_requests": 0,
            "paid_tokens": 0,
            "estimated_cny": 0,
            "python": sys.version,
            "packages": {
                name: importlib.metadata.version(name)
                for name in [
                    "tau2",
                    "langchain",
                    "langchain-core",
                    "langchain-openai",
                    "langgraph",
                    "langgraph-checkpoint-sqlite",
                    "aiosqlite",
                ]
            },
        }
    )
    out.mkdir(parents=True, exist_ok=False)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    env = os.environ | {
        "TAU2_DATA_DIR": str(upstream / "data"),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "ACS_LLM_API_KEY": "",
        "ACS_EMBEDDING_API_KEY": "",
        "ACS_TAU_CONTRACT_OUT": str(out / "paired"),
    }
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-vv",
        "-rA",
        "tests/test_tau_langgraph_adapter.py",
        "--junitxml=" + str(out / "junit.xml"),
    ]
    with (out / "pytest.log").open("w") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    suite = ET.parse(out / "junit.xml").getroot()
    cases = suite.findall(".//testcase")
    skipped = len(suite.findall(".//skipped"))
    failures = len(suite.findall(".//failure")) + len(suite.findall(".//error"))
    comparisons = []
    for path in sorted((out / "paired").glob("*.json")):
        old, graph = json.loads(path.read_text())
        comparisons.append(
            {
                "case": path.stem,
                "task": graph["task_id"],
                "old_db": old["db"],
                "graph_db": graph["db"],
                "old_reward": old["reward"]["reward"],
                "graph_reward": graph["reward"]["reward"],
                "old_calls": len(old["executed"]),
                "graph_calls": len(graph["executed"]),
                "same_calls": old["executed"] == graph["executed"],
                "same_requests": old["model_requests"] == graph["model_requests"],
                "old_termination": old["termination"],
                "graph_termination": graph["termination"],
            }
        )
    ok = result.returncode == 0 and bool(cases) and not skipped and not failures
    summary = {
        "passed": ok,
        "tests": len(cases),
        "skipped": skipped,
        "failures": failures,
        "comparisons": comparisons,
        "paid_requests": 0,
        "paid_tokens": 0,
        "estimated_cny": 0,
        "benchmark_quality_score": None,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "comparisons"}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
