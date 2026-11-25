#!/usr/bin/env python3
"""v2.5 candidate: the v2.3 recipe trained once on the full corpus, which now holds the upload-edit rows.

Same base, LoRA, SFT, NLL-anchored RPO-DPO, WiSE-FT anchor and FP32->BF16 merge as
`train_and_eval_v2_3_oof_and_full.py` (imported, not copied). The 5-fold OOF circuit breaker is not rerun: the IR
rows are unchanged, and the merged model is scored on the same prompt-disjoint dev set (`oof_eval_v2_3`, N=132) as
v2.3 to show it didn't regress (compare with v2.3 under the same code: `compare_dev_same_code.py`). The upload-edit holdout is scored separately (`qualify_upload_edits.py`), on the
quantized model as it is served.

The candidate is chosen by the v2.3 gates, before any holdout is scored: the DPO stage's model if its val SFT loss
is no worse than after SFT and its preference accuracy is at least 0.99, else the stage-1 SFT model (which meets both
by definition when its own preference accuracy does). `--resume-stage1` merges the stage-1 adapter of an earlier
run (its receipt and `checkpoints_v2_5/stage1_sft`) without training again.

Neither sealed holdout may be in the training workspace: the script refuses to start if either file is there.
Writes `endexis-qwen3-0.6b-dpo-v2_5-merged/`, `checkpoints_v2_5/` and `phase3_v2_5_training_receipt.json`.
"""
from __future__ import annotations

import argparse
import gc
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_and_eval_v2_3_oof_and_full as v23  # noqa: E402

torch, save_file, load_file = v23.torch, v23.save_file, v23.load_file
MERGED_DIR = v23.WORK_DIR / "endexis-qwen3-0.6b-dpo-v2_5-merged"
CKPT_DIR = v23.WORK_DIR / "checkpoints_v2_5"
SEALED = ("eval_postfreeze_v3.jsonl", "eval_upload_edits_v1.jsonl")


def gates_for(after: dict, sft: dict) -> dict:
    return {"val_sft_loss_not_worse_after_dpo": after["val_sft_loss"] <= sft["val_sft_loss"],
            "dpo_preference_accuracy_ge_0_99": after["dpo_preference_accuracy"] >= 0.99}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume-stage1", action="store_true", help="merge an earlier run's stage-1 adapter")
    args = ap.parse_args()
    t0 = time.time()
    random.seed(42)
    torch.manual_seed(42)
    assert torch.cuda.is_available(), "CUDA GPU required"
    for name in SEALED:
        assert not (v23.DATA_DIR / name).exists(), f"{name} must not be in the training workspace"
    torch.cuda.set_per_process_memory_fraction(0.45, 0)  # the v2.3 cap: <= 7.4 GiB of the shared card
    device = torch.device("cuda:0")
    pre_gpu = v23.get_gpu_watcher_status()
    print("GPU watcher before:", json.dumps(pre_gpu.get("gpu", pre_gpu)), flush=True)

    read = lambda name: [json.loads(ln) for ln in (v23.DATA_DIR / name).read_text().splitlines() if ln.strip()]  # noqa: E731
    sft_rows, dpo_rows = read("sft_train_v1.jsonl"), read("dpo_contrastive_v1.jsonl")
    oof = json.loads((v23.DATA_DIR / "oof_eval_v2_3.json").read_text())
    # build_training_corpus.py rebuilds the legacy corpus without them; rerun build_upload_edit_cases.py after it
    assert any(r.get("task") == "rewrite_upload_edit" for r in sft_rows), "no upload-edit rows in sft_train_v1.jsonl"
    assert any(r.get("provenance") == "endexis:synthetic_upload_edits" for r in dpo_rows), "no upload-edit DPO pairs"
    tokenizer = v23.AutoTokenizer.from_pretrained(str(v23.BASE_MODEL_DIR))

    receipt_path = v23.WORK_DIR / "phase3_v2_5_training_receipt.json"
    stage1 = CKPT_DIR / "stage1_sft" / "adapters.safetensors"
    if args.resume_stage1:
        earlier = json.loads(receipt_path.read_text())
        info, adapter = earlier["training"], earlier["adapter"]
        dpo_gates, dpo_dev = earlier.get("dpo_stage_gates", earlier["gates"]), earlier.get(
            "dpo_stage_oof_eval_v2_3", earlier["merged_bf16_oof_eval_v2_3"])
        dpo_lora = None
    else:
        tr_sft, vl_sft, tr_dpo, vl_dpo = v23.build_training_splits(sft_rows, dpo_rows, oof["template_to_fold"],
                                                                   heldout_fold=None, rng_seed=42)
        model, info = v23.train_sft_and_rpo_dpo_model(tokenizer, tr_sft, vl_sft, tr_dpo, vl_dpo, device,
                                                      sft_epochs=2, dpo_epochs=2, tag="full_v2_5")
        stage1.parent.mkdir(parents=True, exist_ok=True)
        save_file(info.pop("sft_state_backup"), str(stage1),
                  metadata={"format": "pt", "project": "endexis", "stage": "stage1_sft", "version": "2.5.0"})
        adapter = v23.save_adapter_checkpoint(model, CKPT_DIR / "stage2_dpo")
        dpo_lora = v23.extract_lora_state_dict(model)
        dpo_gates, dpo_dev = gates_for(info["post_stage2_rpo_dpo"], info["post_stage1_sft"]), None
        del model
        gc.collect()
        torch.cuda.empty_cache()
    use_dpo = dpo_lora is not None and all(dpo_gates.values())
    candidate = "stage2_rpo_dpo" if use_dpo else "stage1_sft"
    lora = dpo_lora if use_dpo else load_file(str(stage1))
    gates = dpo_gates if use_dpo else gates_for(info["post_stage1_sft"], info["post_stage1_sft"])
    print(f"candidate: {candidate} (DPO stage gates {dpo_gates})", flush=True)

    merge = v23.merge_fp32_to_bf16_standalone(v23.BASE_MODEL_DIR, lora, MERGED_DIR)
    merged = v23.AutoModelForCausalLM.from_pretrained(str(MERGED_DIR), dtype=torch.bfloat16).to(device)
    dev = v23.score_oof_items_on_gpu(merged, v23.AutoTokenizer.from_pretrained(str(MERGED_DIR)), oof["items"],
                                     v23.CANONICAL_SYSTEM_PROMPT, device, max_new_tokens=384, legacy_mode=False,
                                     batch_size_gen=1)
    print(f"  [merged BF16 on oof_eval_v2_3, N={len(oof['items'])}] Pass@1={dev['track_a_strict_pass_at_1']:.4f} "
          f"F1={dev['mean_element_f1']:.4f}", flush=True)
    peak = round(torch.cuda.max_memory_allocated() / 2**20, 1)
    del merged
    gc.collect()
    torch.cuda.empty_cache()

    receipt = {
        "schema": "endexis-phase3-training-receipt/v2.5",
        "recipe": "train_and_eval_v2_3_oof_and_full.py (full-corpus stage only)",
        "corpus": {"sft_rows": len(sft_rows), "dpo_pairs": len(dpo_rows),
                   "upload_edit_sft_rows": sum(r.get("task") == "rewrite_upload_edit" for r in sft_rows)},
        "training": info, "candidate": candidate, "gates": gates, "dpo_stage_gates": dpo_gates,
        "adapter": adapter, "merge_manifest": merge,
        "merged_bf16_oof_eval_v2_3": {k: v for k, v in dev.items() if k != "items"},
        **({"dpo_stage_oof_eval_v2_3": dpo_dev} if dpo_dev and not use_dpo else {}),
        "gpu": {"memory_fraction_cap": 0.45, "peak_pytorch_vram_mb": peak,
                "watcher_before": pre_gpu.get("gpu"), "watcher_after": v23.get_gpu_watcher_status().get("gpu")},
        "wall_time_seconds": round(time.time() - t0, 1),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print("gates:", gates, flush=True)
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
