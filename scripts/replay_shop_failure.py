"""Replay the observed decisions without network; one blocked continuation is synthetic."""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from eval_live_readiness import model_for  # noqa: E402

from acs.commerce_benchmark import no_network  # noqa: E402
from acs.commerce_benchmark_data import dump, file_hash  # noqa: E402
from acs.episode_budget import EpisodeLedger  # noqa: E402
from acs.live_evaluation import LIMITS  # noqa: E402
from acs.shop_mainline_data import load  # noqa: E402
from acs.shop_mainline_eval import run  # noqa: E402


def replay(source, out):
    out.mkdir(parents=True, exist_ok=False)
    suite, reports = load(), []
    for tid in ("SX02", "SX04"):
        entry = next(e for e in suite["entries"] if e["task"]["task_id"] == tid)
        for arm in ("old", "v3"):
            original = source / ".private-evidence" / f"scoped-{tid}-{arm}"
            outputs = json.loads((original / "model-outputs.json").read_text())
            queue = [dict(text=r["content"], calls=copy.deepcopy(r["tool_calls"])) for r in outputs]
            if tid == "SX04":
                gold = entry["intent_gold"][1]
                queue.append(dict(text=next(text for text, ids in gold["answer_coverage"].items()
                                            if len(ids) == len(gold["goals"]))))
            root = out / ".private-evidence" / f"{tid}-{arm}"
            root.mkdir(parents=True)
            episode = tid+":"+arm
            ledger = EpisodeLedger(root / "fees.jsonl")
            ledger.begin(episode, LIMITS)
            with no_network(), model_for(root, ledger, episode, queue=queue) as model:
                result = run(suite, entry, root / "fixture", arm, model=model, ledger=ledger,
                             model_context=True)
            bodies = [r["body_bytes"] for r in ledger.rows() if r["type"] == "mock_http_observed"]
            report = dict(task_id=tid, arm=arm, status=result["status"],
                outcome=result["raw_outcome"], sql_correct=result["sql_correct"],
                all_evidence=all(g["evidence"] for g in result["objectives"]),
                objectives=result["objectives"], body_bytes=bodies, error=result["error"],
                original_outputs_sha256=file_hash(original / "model-outputs.json"),
                synthetic_last_answer=tid == "SX04", paid_requests=0,
                note="Observed choices unchanged; synthetic continuation proves execution only.")
            assert result["status"] == "completed" and not queue and max(bodies) <= 32768
            assert report["all_evidence"] == (tid == "SX04")
            reports.append(report)
    dump(out / "report.json", dict(kind="real_trace_replay_not_new_model_quality", reports=reports))
    print(json.dumps(reports, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    replay(args.source, args.out)
