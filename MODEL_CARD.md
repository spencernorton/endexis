---
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

# Endexis (`endexis-qwen3-0.6b-dpo-merged`)

**Endexis** (`endexis`) is a tenant-agnostic clinical data model interpreter, **Discern Analytics 2.0 (DA2)** `.daquery` / `.dareport` compiler, **Cerner Command Language (CCL)** `.prg` compiler, and **Eclipse BIRT 4.4.2** (`.rptdesign`) report generator fine-tuned via 2-stage Supervised Fine-Tuning (SFT) and RPO-anchored Direct Preference Optimization (DPO) on `Qwen/Qwen3-0.6B`.

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

Endexis is an independent open-source interoperability toolkit and model developed by the Endexis Contributors and is not affiliated with, endorsed by, or sponsored by Oracle Corporation, Cerner Corporation, or the Eclipse Foundation. See `LEGAL.md` for full details.
