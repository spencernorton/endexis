#!/usr/bin/env python3
"""
Phase 5 — End-to-End Live Evaluation of `ApolloNeuralCompiler` (`endexis-qwen3-0.6b-dpo-merged`
served on `gpu-host:8945` + deterministic verifier fallback) across all zero-leakage holdout sets.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from discern_intelligence.eval_harness import run_full_evaluation_suite
from discern_intelligence.neural_compiler import ApolloNeuralCompiler


def main() -> int:
    t0 = time.time()
    data_dir = REPO_ROOT / "data"
    nc = ApolloNeuralCompiler()
    health = nc.health()

    holdout_qs = [
        json.loads(line)
        for line in (data_dir / "eval_holdout_queries_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    frozen_scs = [
        json.loads(line)
        for line in (data_dir / "eval_frozen_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    hq_results = []
    for hq in holdout_qs:
        res = nc.compile_with_verifier_fallback(hq["prompt"], n=4, tenancy="communityworks", report_name=f"Phase5_{hq['query_id']}")
        hq_results.append({
            "query_id": hq["query_id"],
            "neural_endpoint_active": res["neural_endpoint_active"],
            "transport": res["transport"],
            "latency_ms": res["latency_ms"],
            "vram_allocated_mb": res["vram_allocated_mb"],
            "all_candidates_verified_count": res["all_candidates_verified_count"],
            "bundle_valid": res["bundle"].verification.is_valid,
            "ccl_valid": res["ccl_program"].verification.is_valid,
        })

    frozen_valid = 0
    for sc in frozen_scs:
        res = nc.compile_with_verifier_fallback(sc["request"], n=4, tenancy=sc["tenancy"])
        if res["bundle"].verification.is_valid and res["ccl_program"].verification.is_valid:
            frozen_valid += 1

    full_suite = run_full_evaluation_suite(data_dir)
    merge_manifest = json.loads((data_dir / "merge_manifest.json").read_text(encoding="utf-8"))

    report = {
        "phase5_live_neural_deployment_and_evaluation": {
            "merged_model_manifest": merge_manifest,
            "apollo_endpoint_health": health,
            "holdout_production_queries_neural_best_of_4": {
                "evaluated_count": len(hq_results),
                "pass_at_1": round(sum(1 for r in hq_results if r["bundle_valid"] and r["ccl_valid"]) / max(1, len(hq_results)), 4),
                "mean_latency_ms": round(sum(r["latency_ms"] for r in hq_results) / max(1, len(hq_results)), 2),
                "queries": hq_results,
            },
            "frozen_scenarios_best_of_4_pass_at_1": round(frozen_valid / max(1, len(frozen_scs)), 4),
            "full_evaluation_suite": full_suite,
            "wall_time_seconds": round(time.time() - t0, 2),
        }
    }
    out_path = data_dir / "phase5_merged_model_and_endpoint_eval.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
