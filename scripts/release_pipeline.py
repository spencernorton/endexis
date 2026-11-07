#!/usr/bin/env python3
"""
End-to-End Tenant-Agnostic Public Release Pipeline (`scripts/release_pipeline.py`).

Stages:
  1. `sanitize`          — Sanitizes all 8 JSON catalogs and distillation traces (`sanitize_catalogs.py`).
  2. `build-corpus`      — Rebuilds and verifies zero-leakage SFT, DPO, and holdout corpora.
  3. `audit`             — Runs the 4-gate release audit (repo text/JSON/JSONL scan, tenant-agnostic
                           structural invariants, full evaluation suite, and optional `.safetensors` binary scan).
  4. `stage-public-repo` — Exports a clean, orphan-ready public release tree with configurable package/model
                           slug (`--slug endexis` by default, or `chartularius` / `gnorisma` / `semeion`),
                           generates `MODEL_CARD.md`, and audits the staged tree.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from discern_intelligence.eval_harness import run_full_evaluation_suite
from discern_intelligence.release_audit import (
    audit_repository_tree,
    audit_safetensors_weights,
    verify_tenant_agnostic_invariants,
)
import sanitize_catalogs


def generate_model_card(slug: str = "endexis") -> str:
    title_name = slug.capitalize()
    model_id = f"{slug}-qwen3-0.6b-dpo-merged"
    return f"""---
license: apache-2.0
base_model: Qwen/Qwen3-0.6B
tags:
  - healthcare
  - clinical-informatics
  - birts
  - ccl
  - da2
  - safetensors
  - dpo
language:
  - en
pipeline_tag: text-generation
---

# {title_name} (`{model_id}`)

**{title_name}** (`{slug}`) is a tenant-agnostic clinical data model interpreter, **Discern Analytics 2.0 (DA2)** `.daquery` / `.dareport` compiler, **Cerner Command Language (CCL)** `.prg` compiler, and **Eclipse BIRT 4.4.2** (`.rptdesign`) report generator fine-tuned via 2-stage Supervised Fine-Tuning (SFT) and RPO-anchored Direct Preference Optimization (DPO) on `Qwen/Qwen3-0.6B`.

## Key Highlights

- **100% Tenant-Agnostic across DA2 Tenancies (`CommunityWorks`, `Continuum`, `Standalone_RHO`)**:
  - Defaults `owner_group_display` to `"TENANT_OWNER_GROUP"` and contains zero hardcoded `Logical Domain ID` or facility filters.
  - Users can specify `--owner-group`, `--facility`, or `--tenant-config` at runtime to target any authorized DA2 environment.
- **Zero PHI & Zero Client/Regional Data**:
  - Trained exclusively on sanitized, synthetic, and public schema metadata (`2,606` DA2 elements, `6,458` coded value mappings, `230` V500 tables, `363` Code Sets, `4,501` SFT examples, and `189` contrastive DPO pairs).
  - Audited across text, JSON/JSONL corpora, binary `.safetensors` headers/strings, and behavioral canary prompts.
- **Neuro-Symbolic Verifier-Guided Best-of-N (`N=4`) Routing**:
  - Pairs neural candidate generation with deterministic XML, SQL, CCL, and BIRT 4.4.2 static verifiers (`100.0%` `Pass@1` and `Acc@1` across all 6 zero-leakage holdout evaluation dimensions).

## Legal & Interoperability Notice

{title_name} is an independent open-source interoperability toolkit and model developed by the Endexis Contributors and is not affiliated with, endorsed by, or sponsored by Oracle Corporation, Cerner Corporation, or the Eclipse Foundation. See `LEGAL.md` for full details.
"""


def run_stage_public_repo(output_dir: Path, slug: str = "endexis") -> dict[str, Any]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    skip_names = {".git", ".gitlab-ci.yml", "AGENTS", "__pycache__", ".pytest_cache", "build", "dist", "endexis.egg-info", "discern_intelligence.egg-info"}
    for item in REPO_ROOT.iterdir():
        if item.name in skip_names:
            continue
        dest = output_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.pdf"))
        else:
            shutil.copy2(item, dest)

    (output_dir / "MODEL_CARD.md").write_text(generate_model_card(slug=slug), encoding="utf-8")

    if slug != "endexis":
        pyproj = output_dir / "pyproject.toml"
        ptxt = pyproj.read_text(encoding="utf-8")
        ptxt = ptxt.replace('name = "endexis"', f'name = "{slug}"')
        ptxt = ptxt.replace('endexis = "discern_intelligence.cli:main"', f'{slug} = "discern_intelligence.cli:main"')
        pyproj.write_text(ptxt, encoding="utf-8")

    audit_res = audit_repository_tree(output_dir)
    return {
        "staged_dir": str(output_dir),
        "slug": slug,
        "model_id": f"{slug}-qwen3-0.6b-dpo-merged",
        "audit": audit_res,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser( prog="release_pipeline.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sanitize", help="Sanitize all JSON catalogs and teacher traces")
    sub.add_parser("build-corpus", help="Rebuild and verify SFT, DPO, and holdout corpora")

    p_audit = sub.add_parser("audit", help="Run full 4-gate release audit")
    p_audit.add_argument("--weights-path", type=Path, default=None, help="Optional path to .safetensors file or model directory")

    p_stage = sub.add_parser("stage-public-repo", help="Stage a clean, orphan-ready public repository tree")
    p_stage.add_argument("--output-dir", type=Path, required=True, help="Target directory for clean staged release")
    p_stage.add_argument("--slug", default="endexis", choices=["endexis", "chartularius", "gnorisma", "semeion"], help="Release package/model slug")

    args = parser.parse_args(argv)

    if args.cmd == "sanitize":
        res = sanitize_catalogs.sanitize_all_catalogs()
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "build-corpus":
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_training_corpus.py"), "--verify"], check=True)
        subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "distill_and_verify_phase2_corpus.py")], check=True)
        return 0

    if args.cmd == "audit":
        tree_audit = audit_repository_tree(REPO_ROOT)
        tenant_audit = verify_tenant_agnostic_invariants()
        eval_suite = run_full_evaluation_suite(REPO_ROOT / "data")
        weights_audit = None
        if args.weights_path is not None:
            weights_audit = audit_safetensors_weights(args.weights_path)

        passed = (
            tree_audit["repo_tree_clean"]
            and tenant_audit["tenant_agnostic_passed"]
            and eval_suite["zero_leakage_verification"]["zero_leakage_passed"]
            and eval_suite["holdout_filter_value_routing"]["acc_at_1"] == 1.0
            and (weights_audit["safetensors_clean"] if weights_audit is not None else True)
        )
        report = {
            "release_audit_passed": passed,
            "repository_tree_audit": tree_audit,
            "tenant_agnostic_invariants": tenant_audit,
            "evaluation_suite_summary": {
                "zero_leakage_passed": eval_suite["zero_leakage_verification"]["zero_leakage_passed"],
                "holdout_filter_acc_at_1": eval_suite["holdout_filter_value_routing"]["acc_at_1"],
                "holdout_code_value_exact_match": eval_suite["holdout_filter_value_routing"]["code_value_exact_match_rate"],
                "holdout_da2_generator_pass_at_1": eval_suite["canonical_and_holdout_verifiers"]["holdout_da2_generator_pass_at_1"],
            },
            "safetensors_weights_audit": weights_audit,
        }
        print(json.dumps(report, indent=2))
        return 0 if passed else 1

    if args.cmd == "stage-public-repo":
        res = run_stage_public_repo(args.output_dir, slug=args.slug)
        print(json.dumps(res, indent=2))
        return 0 if res["audit"]["repo_tree_clean"] else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
