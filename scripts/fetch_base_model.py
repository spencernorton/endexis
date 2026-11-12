#!/usr/bin/env python3
"""Fetch the Qwen3-0.6B base model into base-qwen3-0.6b-bf16/ (train_and_eval_gpu.py expects it there).

Usage: python3 scripts/fetch_base_model.py [--output base-qwen3-0.6b-bf16]
Requires the [train] extra (huggingface-hub). Public model; no token needed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "base-qwen3-0.6b-bf16")
    ap.add_argument("--repo", default="Qwen/Qwen3-0.6B")
    args = ap.parse_args()
    from huggingface_hub import snapshot_download
    path = snapshot_download(repo_id=args.repo, local_dir=str(args.output))
    print(f"Base model ready: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
