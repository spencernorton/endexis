#!/usr/bin/env python3
"""Retired: chart drawing moved to shared-ops bench_chart.py (bench-receipt/v1).

Kept as a thin entry point: exports data/bench_receipt.json via
export_bench_receipt.py, then renders with the shared tool when it is found
(sibling shared-ops checkout or $BENCH_CHART). Historical figures are not
re-rendered here.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_bench_receipt import build  # noqa: E402
import json  # noqa: E402


def _find_tool() -> Path | None:
    env = os.environ.get("BENCH_CHART")
    candidates = [Path(env)] if env else []
    candidates.append(ROOT.parent / "shared-ops" / "scripts" / "bench_chart.py")
    return next((c for c in candidates if c.is_file()), None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "figures")
    args = ap.parse_args()
    receipt = ROOT / "data" / "bench_receipt.json"
    receipt.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {receipt}")
    tool = _find_tool()
    if tool is None:
        print("bench_chart.py not found; render with:\n"
              f"  python3 ../shared-ops/scripts/bench_chart.py --input {receipt} "
              f"--output-dir {args.output_dir} --name discern_iq_vs_gpt_claude_comparison")
        return 0
    return subprocess.call([sys.executable, str(tool), "--input", str(receipt),
                            "--output-dir", str(args.output_dir),
                            "--name", "discern_iq_vs_gpt_claude_comparison"])


if __name__ == "__main__":
    raise SystemExit(main())
