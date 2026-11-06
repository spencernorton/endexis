#!/usr/bin/env python3
"""
Runs the Discern Intelligence V1 Deterministic Verification & Zero-Leakage Holdout Evaluation Suite.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from discern_intelligence.eval_harness import run_full_evaluation_suite  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Discern Intelligence V1 deterministic evaluation harness")
    ap.add_argument("--data-dir", default=str(REPO_ROOT / "data"), help="Path to corpus data directory")
    ap.add_argument("--verify", action="store_true", help="Enforce strict pass/accuracy gates")
    args = ap.parse_args()

    report = run_full_evaluation_suite(Path(args.data_dir))
    print(json.dumps(report, indent=2))

    if args.verify:
        zl = report["zero_leakage_verification"]
        assert zl["zero_leakage_passed"] is True, f"Zero-leakage check failed: {zl}"

        rt = report["holdout_filter_value_routing"]
        assert rt["acc_at_1"] >= 0.85, f"Expected Holdout Filter Value Routing Acc@1 >= 0.85, got {rt['acc_at_1']}"
        assert rt["acc_at_3"] >= 0.95, f"Expected Holdout Filter Value Routing Acc@3 >= 0.95, got {rt['acc_at_3']}"
        assert rt["code_value_exact_match_rate"] == 1.0, (
            f"Expected CODE_VALUE exact match rate == 1.0, got {rt['code_value_exact_match_rate']}"
        )

        ver = report["canonical_and_holdout_verifiers"]
        assert ver["cclquery_xml_pass_at_1"] == 1.0, f"<cclquery> XML Pass@1 < 1.0: {ver}"
        assert ver["ejs_v500_sql_pass_at_1"] == 1.0, f"EJS V500 SQL Pass@1 < 1.0: {ver}"
        assert ver["rptdesign_xml_pass_at_1"] == 1.0, f"BIRT .rptdesign XML Pass@1 < 1.0: {ver}"
        assert ver["holdout_da2_generator_pass_at_1"] == 1.0, f"Holdout DA2 Generator Pass@1 < 1.0: {ver}"
        assert ver["holdout_mean_element_f1"] >= 0.65, f"Holdout Mean Element F1 < 0.65: {ver}"
        assert ver["dpo_preference_validity_rate"] == 1.0, f"DPO Preference Validity < 1.0: {ver}"

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
