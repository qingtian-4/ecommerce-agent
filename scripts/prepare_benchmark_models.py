"""Download pinned public ONNX weights once; evaluation itself stays network-free."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acs.commerce_retrieval_comparison import MODELS  # noqa: E402


def main():
    from huggingface_hub import hf_hub_download

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    for kind, (repo, rev) in MODELS.items():
        for name in ("tokenizer.json", "onnx/model_quantized.onnx"):
            hf_hub_download(repo, name, revision=rev, local_dir=a.out / kind)
        print(kind, repo, rev)


if __name__ == "__main__":
    main()
