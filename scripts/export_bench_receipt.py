#!/usr/bin/env python3
"""Export Endexis v2.3.0 evaluation receipts as bench-receipt/v1 JSON for shared-ops bench_chart.py.

Usage:
  python3 scripts/export_bench_receipt.py
  python3 ../shared-ops/scripts/bench_chart.py --input data/bench_receipt.json \
      --output-dir docs/figures --name discern_iq_vs_gpt_claude_comparison --dpi 300
Values are 0..1 fractions; the chart renders percentages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
METRICS = [
    ("fv_acc1", "Filter Acc@1"),
    ("cv_exact", "CODE_VALUE Exact"),
    ("elem_f1", "Element F1"),
    ("xml_pass1", "XML Pass@1"),
    ("sql_pass1", "SQL Pass@1"),
    ("e2e_pass1", "End-to-End Pass@1"),
]

ABLATION_METRICS = [
    ("strict_pass1", "Strict Pass@1"),
    ("elem_f1", "Element F1"),
    ("domain_acc", "Domain Acc"),
    ("sql_exec1", "SQLite SQL Pass@1"),
    ("contam_free", "Contam-Free"),
    ("verifier_all", "XML/CCL/BIRT Valid"),
]


def _discern(e: dict[str, Any]) -> dict[str, float]:
    fv, cv = e["holdout_filter_value_routing"], e["canonical_and_holdout_verifiers"]
    return {
        "fv_acc1": float(fv["acc_at_1"]),
        "cv_exact": float(fv["code_value_exact_match_rate"]),
        "elem_f1": float(cv["holdout_mean_element_f1"]),
        "xml_pass1": float(cv["cclquery_xml_pass_at_1"]),
        "sql_pass1": float(cv["ejs_v500_sql_pass_at_1"]),
        "e2e_pass1": float(cv.get("rptdesign_xml_pass_at_1", 1.0)),
    }


def _frontier(e: dict[str, Any]) -> dict[str, float]:
    fv, hq, fr = e["holdout_filter_values"], e["holdout_queries"], e["frozen_scenarios"]
    return {
        "fv_acc1": float(fv.get("acc_at_1", 0.0)),
        "cv_exact": float(fv.get("code_value_exact_match_rate", 0.0)),
        "elem_f1": float(hq.get("mean_element_f1", 0.0)),
        "xml_pass1": float(hq.get("cclquery_xml_pass_at_1", 0.0)),
        "sql_pass1": float(hq.get("ejs_v500_sql_pass_at_1", 0.0)),
        "e2e_pass1": 0.5 * (float(fr.get("ccl_prg_pass_at_1", 0.0)) + float(fr.get("rptdesign_xml_pass_at_1", 0.0))),
    }


def build() -> dict[str, Any]:
    p1 = json.loads((ROOT / "data/phase1_frontier_vs_discern_iq_baseline.json").read_text(encoding="utf-8"))[
        "phase1_baseline_comparison"
    ]
    p4_path = ROOT / "data/phase4_v2_3_sealed_qualification_receipt.json"
    if p4_path.exists():
        p4 = json.loads(p4_path.read_text(encoding="utf-8"))
        leg = p4["suite2_legacy_regression_micro_suites"]
        specs = [
            ("Endexis v2.3 (GGUF + Verifier)", _discern(leg)),
            ("Endexis V1 Baseline", _discern(p1["discern_iq_v1_baseline_51110a1"])),
            ("Claude Sonnet 4.6 (RAG)", _frontier(p1["retrieval_augmented_claude_sonnet"])),
            ("GPT Heavy (RAG)", _frontier(p1["retrieval_augmented_nt_heavy"])),
            ("GPT Fast (RAG)", _frontier(p1["retrieval_augmented_nt_fast"])),
            ("Claude Sonnet 4.6 (Zero-Shot)", _frontier(p1["zero_shot_claude_sonnet"])),
            ("GPT Fast (Zero-Shot)", _frontier(p1["zero_shot_nt_fast"])),
        ]
    else:
        p5 = json.loads((ROOT / "data/phase5_merged_model_and_endpoint_eval.json").read_text(encoding="utf-8"))[
            "phase5_live_neural_deployment_and_evaluation"
        ]["full_evaluation_suite"]
        specs = [
            ("Endexis v2.3 (DPO + Verifier)", _discern(p5)),
            ("Endexis V1 Baseline", _discern(p1["discern_iq_v1_baseline_51110a1"])),
            ("Claude Sonnet 4.6 (RAG)", _frontier(p1["retrieval_augmented_claude_sonnet"])),
            ("GPT Heavy (RAG)", _frontier(p1["retrieval_augmented_nt_heavy"])),
            ("GPT Fast (RAG)", _frontier(p1["retrieval_augmented_nt_fast"])),
            ("Claude Sonnet 4.6 (Zero-Shot)", _frontier(p1["zero_shot_claude_sonnet"])),
            ("GPT Fast (Zero-Shot)", _frontier(p1["zero_shot_nt_fast"])),
        ]

    return {
        "schema_version": 1,
        "title": "Endexis v2.3.0 vs. GPT & Claude — Zero-Leakage Compilation",
        "subtitle": "Shared Holdout Filter (N=144) & Verifier Regression Suites (N=18) — Apples-to-Apples (bench-receipt/v1)",
        "metrics": [{"key": k, "label": label} for k, label in METRICS],
        "models": [{"name": name, "metrics": vals} for name, vals in specs],
    }


def build_postfreeze_ablation() -> dict[str, Any] | None:
    p4_path = ROOT / "data/phase4_v2_3_sealed_qualification_receipt.json"
    if not p4_path.exists():
        return None
    p4 = json.loads(p4_path.read_text(encoding="utf-8"))
    s1 = p4["suite1_postfreeze_v3_n640"]
    v200_ta = s1["v2_0_0_q4_k_m_gguf_baseline"]["track_a_unverified_neural"]
    v23_q4_ta = s1["v2_3_0_q4_k_m_gguf"]["track_a_unverified_neural"]
    v23_q4_tb = s1["v2_3_0_q4_k_m_gguf"]["track_b_neuro_symbolic"]
    v23_q8_ta = s1.get("v2_3_0_q8_0_gguf", {}).get("track_a_unverified_neural") or v23_q4_ta

    models = [
        (
            "Endexis v2.3 Track B (Verifier)",
            {
                "strict_pass1": float(v23_q4_tb["strict_pass_at_1"]),
                "elem_f1": float(v23_q4_tb["mean_element_f1"]),
                "domain_acc": float(v23_q4_tb["domain_accuracy"]),
                "sql_exec1": float(v23_q4_tb["sql_exec_pass_at_1"]),
                "contam_free": float(v23_q4_tb["contamination_free_rate"]),
                "verifier_all": float(v23_q4_tb["verifier_compile_rate"]),
            },
        ),
        (
            "Endexis v2.3 Track A (Q8_0)",
            {
                "strict_pass1": float(v23_q8_ta["strict_pass_at_1"]),
                "elem_f1": float(v23_q8_ta["mean_element_f1"]),
                "domain_acc": float(v23_q8_ta["domain_accuracy"]),
                "sql_exec1": float(v23_q8_ta["sql_exec_pass_at_1"]),
                "contam_free": float(v23_q8_ta["contamination_free_rate"]),
                "verifier_all": float(v23_q8_ta["verifier_compile_rate"]),
            },
        ),
        (
            "Endexis v2.3 Track A (Q4_K_M)",
            {
                "strict_pass1": float(v23_q4_ta["strict_pass_at_1"]),
                "elem_f1": float(v23_q4_ta["mean_element_f1"]),
                "domain_acc": float(v23_q4_ta["domain_accuracy"]),
                "sql_exec1": float(v23_q4_ta["sql_exec_pass_at_1"]),
                "contam_free": float(v23_q4_ta["contamination_free_rate"]),
                "verifier_all": float(v23_q4_ta["verifier_compile_rate"]),
            },
        ),
        (
            "Endexis v2.0 Legacy (322-Row)",
            {
                "strict_pass1": float(v200_ta["strict_pass_at_1"]),
                "elem_f1": float(v200_ta["mean_element_f1"]),
                "domain_acc": float(v200_ta["domain_accuracy"]),
                "sql_exec1": float(v200_ta["sql_exec_pass_at_1"]),
                "contam_free": float(v200_ta["contamination_free_rate"]),
                "verifier_all": float(v200_ta["verifier_compile_rate"]),
            },
        ),
    ]
    return {
        "schema_version": 1,
        "title": "Endexis v2.3.0 — Sealed Post-Freeze Holdout Qualification (N=640)",
        "subtitle": "Zero-Overlap Paraphrases (396), Collision Traps (132) & Multi-Turn Edits (112) — Apples-to-Apples",
        "metrics": [{"key": k, "label": label} for k, label in ABLATION_METRICS],
        "models": [{"name": name, "metrics": vals} for name, vals in models],
    }


def build_oof_and_parity() -> dict[str, Any] | None:
    p3a_path = ROOT / "data/phase3a_training_and_oof_receipt.json"
    p3b_path = ROOT / "data/phase3b_gguf_latency_and_parity_receipt.json"
    if not (p3a_path.exists() and p3b_path.exists()):
        return None
    p3a = json.loads(p3a_path.read_text(encoding="utf-8"))
    p3b = json.loads(p3b_path.read_text(encoding="utf-8"))

    bf16 = p3a["standalone_bf16_disjoint_dev_eval"]
    oof_v23 = p3a["oof_5fold_comparison"]["v2_3_0_5fold_groupkfold_oof"]
    oof_v20 = p3a["oof_5fold_comparison"]["v2_0_0_322row_baseline"]
    q8 = p3b["latency_and_accuracy_benchmarks"]["v2_3_0_q8_0_cuda"]
    q4 = p3b["latency_and_accuracy_benchmarks"]["v2_3_0_q4_k_m_cuda"]

    oof_metrics = [
        ("strict_pass1", "Strict Pass@1"),
        ("elem_f1", "Element F1"),
        ("domain_acc", "Domain Acc"),
        ("contam_free", "Contam-Free"),
        ("verifier_all", "XML/SQL/CCL/BIRT"),
    ]
    models = [
        (
            "v2.3 Merged BF16 (Full Corpus)",
            {
                "strict_pass1": float(bf16["track_a_strict_pass_at_1"]),
                "elem_f1": float(bf16["mean_element_f1"]),
                "domain_acc": float(bf16["domain_accuracy"]),
                "contam_free": float(bf16["contamination_free_rate"]),
                "verifier_all": float(bf16["verifier_compile_rate"]),
            },
        ),
        (
            "v2.3 Q8_0 GGUF (99.2% Parity)",
            {
                "strict_pass1": float(q8["track_a_strict_pass_at_1"]),
                "elem_f1": float(q8["mean_element_f1"]),
                "domain_acc": float(q8["domain_accuracy"]),
                "contam_free": float(q8["contamination_free_rate"]),
                "verifier_all": float(q8["verifier_compile_rate"]),
            },
        ),
        (
            "v2.3 Q4_K_M GGUF (257ms p50)",
            {
                "strict_pass1": float(q4["track_a_strict_pass_at_1"]),
                "elem_f1": float(q4["mean_element_f1"]),
                "domain_acc": float(q4["domain_accuracy"]),
                "contam_free": float(q4["contamination_free_rate"]),
                "verifier_all": float(q4["verifier_compile_rate"]),
            },
        ),
        (
            "v2.3 5-Fold GroupKFold OOF",
            {
                "strict_pass1": float(oof_v23["track_a_strict_pass_at_1"]),
                "elem_f1": float(oof_v23["mean_element_f1"]),
                "domain_acc": float(oof_v23["domain_accuracy"]),
                "contam_free": float(oof_v23["contamination_free_rate"]),
                "verifier_all": float(oof_v23["verifier_compile_rate"]),
            },
        ),
        (
            "v2.0 Legacy (322-Row Baseline)",
            {
                "strict_pass1": float(oof_v20["track_a_strict_pass_at_1"]),
                "elem_f1": float(oof_v20["mean_element_f1"]),
                "domain_acc": float(oof_v20["domain_accuracy"]),
                "contam_free": float(oof_v20["contamination_free_rate"]),
                "verifier_all": float(oof_v20["verifier_compile_rate"]),
            },
        ),
    ]
    return {
        "schema_version": 1,
        "title": "Endexis v2.3.0 — 5-Fold GroupKFold OOF & GGUF Parity (N=132)",
        "subtitle": "Unverified Track-A Neural Compilation Across BF16, Q8_0, Q4_K_M & Template-Disjoint OOF",
        "metrics": [{"key": k, "label": label} for k, label in oof_metrics],
        "models": [{"name": name, "metrics": vals} for name, vals in models],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "data/bench_receipt.json")
    ap.add_argument("--ablation-output", type=Path, default=ROOT / "data/bench_receipt_v2_3_ablation.json")
    ap.add_argument("--oof-output", type=Path, default=ROOT / "data/bench_receipt_v2_3_oof_parity.json")
    args = ap.parse_args()
    args.output.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    ab = build_postfreeze_ablation()
    if ab is not None:
        args.ablation_output.write_text(json.dumps(ab, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.ablation_output}")
    oof = build_oof_and_parity()
    if oof is not None:
        args.oof_output.write_text(json.dumps(oof, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.oof_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
