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
  - sft
language:
  - en
pipeline_tag: text-generation
---

# Endexis (`endexis-qwen3-0.6b-dpo-merged` — v2.5.0)

**Endexis** (`endexis`) is a tenant-agnostic clinical data model interpreter, **Discern Analytics 2.0 (DA2)** `.daquery` / `.dareport` compiler, **Cerner Command Language (CCL)** `.prg` compiler, and **Eclipse BIRT 4.4.2** (`.rptdesign`) report generator fine-tuned on `Qwen/Qwen3-0.6B`. The library that compiles and checks its output is [`spencernorton/endexis`](https://github.com/spencernorton/endexis) (v2.5.2).

The repository name keeps `dpo` from earlier versions. v2.5.0 is one supervised fine-tuning stage: its preference (RPO-DPO) stage made the validation loss worse, so it was not used.

## What Changed in `v2.5.0`

- **Rewording change requests on an uploaded DA2 file (`rewrite_upload_edit`)**: given a summary of an uploaded `.dareport` or `.daquery`, up to three earlier messages and the latest one, the model writes the plain change the latest message meant (*remove Enc Loc Room* for *lose the room column*), or `null` when it asks for no change (a question, *don't add the MRN*, an unsure ask). Deterministic rules apply and check every change; the model only rewords a message the rules could not read, and a rewording is shown only if the rules accept it.
- **Training**: stock `Qwen/Qwen3-0.6B`, rank-16 LoRA, merged to `BF16`. The corpus is v2.3.0's plus `2,600` upload-edit rows (`7,877` SFT rows in all, `922` optimizer steps). Validation SFT loss `0.1162` (perplexity `1.123`). The RPO-DPO stage on `1,298` pairs raised it to `0.118`, failing the gate that it must not get worse, so the stage-1 model ships.
- Same config, tokenizer and chat template as v2.3.0. The GGUF files keep their names, so the `Modelfile` and existing download links still work.

## Results

- **Upload edits, sealed holdout** (`data/eval_upload_edits_v1.jsonl`, `724` cases on other reports, phrasings and histories than training; `Q8_0`, temperature 0, scored by the rules that use it):
  - Of `460` messages the rules held that meant a change, **`77.8%`** get the right rewording and **`3.3%`** a wrong one (bag F1 `0.88`, precision `0.99`). `Q4_K_M`: `76.1%` right, `3.5%` wrong (bag F1 `0.86`).
  - Of `186` that meant no change, none gets a rewording. Without the rules' no-change check `10.2%` did, mostly *don't …* messages; that check was written after seeing them, so the `0` is optimistic.
  - v2.3.0 never rewords (`0%`). CPU p50 `730 ms` (`Q8_0`) and `522 ms` (`Q4_K_M`) on a shared 8-thread CPU.
- **Text to DA2, dev set** (`oof_eval_v2_3`, `N = 132`, merged `BF16`, both models scored with the v2.5.2 library): Strict Pass@1 **`0.9924`** (multi-turn refinement `35/36`) against v2.3.0's `0.9697` (`32/36`); the canonical and concept-collision slices are `1.0` for both.
- **Not rescored**: the v2.3.0 sealed post-freeze holdout (`eval_postfreeze_v3`, `N = 640`). The new training rows have no exact, token-bag or 5-gram skeleton overlap with its hashes.

## Files

| File | Size | SHA-256 |
| --- | --- | --- |
| `model.safetensors` (merged `BF16`) | 1,192,135,216 B | `3af74aa637dc054f4e3ee85003e8358e65a5e742a130c8cc071c4e60e7807d72` |
| `endexis-qwen3-0.6b-dpo-q8_0.gguf` | 777,068,448 B | `036880b68fe180189688aec967e2ed51fa78124a4754eaa3db8230342c4d3f4c` |
| `endexis-qwen3-0.6b-dpo-q4_k_m.gguf` | 396,700,576 B | `fc3b57d79a0f10c5c7b19d10519d502c59375bfd27c179e841e6ebe3732120c6` |

`SHA256SUMS.txt` lists every file. Earlier weights are at the tags `v2.3.0`, `v2.0.0` and `v1.0.0`.

## Known Gaps

- Training and the upload-edit holdout share the plain words for each column, so the holdout tests new phrasings, files and histories, not new words for a column.
- The preference pairs are weak negatives: the SFT model already prefers every chosen reply on the validation pairs.
- A message reaches the model as typed, names included; the rules still refuse any filter that picks out a patient.

## Legal & Interoperability Notice

Endexis is an independent open-source interoperability toolkit and model developed by the Endexis Contributors and is not affiliated with, endorsed by, or sponsored by Oracle Corporation, Cerner Corporation, or the Eclipse Foundation. See `LEGAL.md` for full details.
