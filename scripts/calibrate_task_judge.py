"""Test the semantic judge on paired authored examples before any task results are read."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acs.config import get_settings  # noqa: E402
from acs.llm import LLMClient  # noqa: E402
from acs.scenarios import sha256  # noqa: E402
from acs.task_eval import JUDGE_SYSTEM, judge_record  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("data/eval/task_judge_pairs.json"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    pairs = json.loads(args.cases.read_text())
    frozen = {
        "cases_sha256": sha256(args.cases),
        "judge_system": JUDGE_SYSTEM,
        "model": get_settings().llm_model,
        "threshold": "0 false accepts and >=90% agreement",
    }
    (args.out / "frozen_manifest.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2))

    def one(pair):
        client = LLMClient()
        result = judge_record(client, {k: v for k, v in pair.items() if k != "expected_pass"})
        return pair | {"review": result, "agreement": result["passed"] == pair["expected_pass"]}

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(one, pairs))
    (args.out / "raw.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    false_accepts = sum(not r["expected_pass"] and r["review"]["passed"] for r in results)
    summary = {
        "examples": len(results),
        "agreements": sum(r["agreement"] for r in results),
        "false_accepts": false_accepts,
        "false_rejects": sum(r["expected_pass"] and not r["review"]["passed"] for r in results),
        "gate_passed": false_accepts == 0
        and sum(r["agreement"] for r in results) >= 0.9 * len(results),
        "raw_sha256": sha256(args.out / "raw.json"),
        "limits": "Authored labels, same provider; not independent human validation.",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
