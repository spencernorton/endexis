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
  - gguf
  - dpo
language:
  - en
pipeline_tag: text-generation
---

# Endexis (`endexis-qwen3-0.6b-dpo-merged` — v2.3.0)

**Endexis** (`endexis`) is a tenant-agnostic clinical data model interpreter, **Discern Analytics 2.0 (DA2)** `.daquery` / `.dareport` compiler, **Cerner Command Language (CCL)** `.prg` compiler, and **Eclipse BIRT 4.4.2** (`.rptdesign`) report generator fine-tuned via 2-stage Supervised Fine-Tuning (SFT) and NLL-anchored RPO Direct Preference Optimization (DPO) on `Qwen/Qwen3-0.6B`.

## What Changed in `v2.3.0` (`endexis-v2.3-clean-r1`)

- **14x Corpus Sync & Compact Neural Compilation IR (`5,277` SFT / `648` Contrastive DPO Pairs)**:
  - Trained from stock `Qwen/Qwen3-0.6B` (`BF16`, rank-16 LoRA across all 7 projections: `q, k, v, o, gate, up, down`, `196` merged tensors) across all `36` clinical, nursing/MAR, dietary/bathing, census/bed-board, and financial revenue-cycle templates plus `768` compact `NeuralCompilationIR` rows and `648` hard preference pairs.
  - Stage-2 NLL-anchored RPO-DPO (`beta = 0.1`, `lambda_nll = 0.15`) held full-corpus SFT cross-entropy flat (`val_sft_loss_post_sft = 0.1137`, `val_sft_loss_post_dpo = 0.1137`, `dpo_preference_accuracy = 100.0%`, `dpo_mean_logprob_margin = +3.4014`), while 5-fold GroupKFold OOF shifted only `+0.0024` to `+0.0058` across folds vs. the `v2.0.0` `322`-row DPO loss regression (`0.1881 -> 0.2039`, `+0.0158`).
- **Clinical-vs-Billing Concept Isolation & Multi-Turn Negative/Override Refinements**:
  - Eliminates clinical attending/ordering physician vs. billing adjustment (`Adj Post Prsnl`, `Prsnl Position`, `Adj Trans *`) collisions, `"discharged"` vs. `CHARGE` substring leaks, and conversational filler in report naming.
  - Supports structured and natural-language multi-turn refinements (`exclude_columns`, `exclude_filters`, `override_filters`) across `<cclquery>` XML, `.daquery`, EJS V500 SQL, CCL `.prg`, and BIRT `.rptdesign`.
- **High-Precision Quantized GGUF Checkpoints (`Q8_0` & `Q4_K_M`) & Sub-260ms Single-Pass Latency**:
  - `endexis-qwen3-0.6b-dpo-v2_3-q8_0.gguf` (`741.07 MB`, SwiGLU `ffn_down` and `attn_output` preserved in `BF16`): **`99.24%`** CUDA structural agreement and **`99.96%`** mean element Jaccard vs. merged `BF16` on disjoint evaluation (`N = 132`).
  - `endexis-qwen3-0.6b-dpo-v2_3-q4_k_m.gguf` (`378.32 MB`): **`257.44 ms`** single-pass CUDA p50 on `N = 132` OOF and **`191.81 ms`** Track-A p50 (`214.22 ms` mean, `10.98x` faster mean than `2,352.61 ms` legacy PyTorch HTTP) / **`210.12 ms`** Track-B full wall-clock p50 (`232.82 ms` mean) across the `N = 640` sealed post-freeze holdout.

## Zero-Leakage Qualification Summary (`v2.3.0` vs. `v2.0.0` `322`-Row Checkpoint)

- **5-Fold Template-Family GroupKFold Out-of-Fold (`N = 132`, Unverified Track-A Neural)**:
  - `v2.3.0` 5-Fold OOF Strict Pass@1: **`50.76%`** vs. `0.00%` (`v2.0.0` `322`-row baseline, **`+50.76pp`** lift), with **`94.70%`** 4-surface verifier compile rate, **`93.18%`** domain routing accuracy, and **`94.70%`** contamination-free rate.
  - Standalone full-corpus merged `BF16` (`96.97%` Strict Pass@1, `96.82%` Element F1), `Q8_0` GGUF (`97.73%` Strict Pass@1, `96.84%` Element F1), and `Q4_K_M` GGUF (`89.39%` Strict Pass@1, `95.81%` Element F1).
- **Sealed Post-Freeze Holdout (`data/eval_postfreeze_v3.jsonl`, `N = 640`, `0 / 0 / 0` Exact / Token-Bag / Normalized 5-Gram Skeleton Overlap; `19` Generic Boilerplate Raw 5-Grams)**:
  - **Track A (Strict Raw Unverified Neural IR, `strict_neural_ir=True` & `compile_from_neural_ir()`, Zero Symbolic Rescue)**:
    - `v2.0.0 Q4_K_M` (`322`-row baseline): `0.00%` parsed valid IR, `0.00%` SQLite/XML/CCL/BIRT pass rate, `0.00%` Strict Pass@1 (`464.25 ms` p50, `534.86 ms` mean).
    - `v2.3.0 Q4_K_M`: **`95.16%`** parsed valid IR (`609 / 640`), **`95.00%`** SQLite V500 SQL execution & `<cclquery>` XML pass rate (`608 / 640`), **`75.47%`** strict 4-surface (`XML + SQLite + CCL + BIRT`) zero-rescue compile rate (`483 / 640`), **`89.69%`** contamination-free rate (`93.18%` on `132` concept-collision traps), **`80.47%`** domain accuracy, and **`14.22%`** Strict Pass@1 (`+14.22pp` lift; `191.81 ms` p50, `214.22 ms` mean).
    - `v2.3.0 Q8_0`: **`97.50%`** parsed valid IR (`624 / 640`), **`97.19%`** SQLite V500 SQL & `<cclquery>` XML pass rate (`622 / 640`), **`78.28%`** strict 4-surface zero-rescue compile rate (`501 / 640`), **`90.78%`** contamination-free rate (`96.97%` on `132` concept-collision traps with **`0.00%`** clinical-to-billing leak rate), **`81.56%`** domain accuracy, and **`14.06%`** Strict Pass@1 (`209.24 ms` p50, `234.08 ms` mean).
  - **Track B (Full Neuro-Symbolic System: Single-Pass `Q4_K_M` + Deterministic Verifiers)**:
    - **`100.00%`** (`640 / 640`) SQLite V500 SQL execution, `<cclquery>` XML, and clinical/refinement compliance, and **`99.69%`** (`638 / 640`) 4-surface end-to-end verified Pass@1 (**`75.47%`** raw unedited neural IR accepted + **`19.38%`** symbolically repaired neural IR accepted = **`94.84%`** neural-led acceptance, **`5.16%`** full symbolic fallback, `84.38%` domain accuracy, `20.62%` Strict Pass@1, **`210.12 ms`** full wall-clock p50 / `232.82 ms` mean).
- **Legacy Regression & Privacy Gates**:
  - **`100.00%`** domain-conditioned `Acc@1` (**`89.58%`** unconditioned `Acc@1`) and **`100.00%`** `CODE_VALUE` exact match across `144` holdout filter values, plus **`100.00%`** across `20` V2 adversarial traps (`+ 5` SQLite edge cases), `14` frozen scenarios, and `4` holdout queries.
  - **`0`** violations across repository tree scan (including decoded Base64 literal inspection), tenant-agnostic invariants (`default_owner_group_display = "TENANT_OWNER_GROUP"`), binary `.safetensors` / `.gguf` string audits, and `17` live behavioral canary prompts.

## Legal & Interoperability Notice

Endexis is an independent open-source interoperability toolkit and model developed by the Endexis Contributors and is not affiliated with, endorsed by, or sponsored by Oracle Corporation, Cerner Corporation, or the Eclipse Foundation. See `LEGAL.md` for full details.
