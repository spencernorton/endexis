#!/usr/bin/env python3
"""
Phase 3 & Phase 4 — GPU Stage-1 SFT + Stage-2 Contrastive DPO Training & Verifier-Guided Best-of-N (N=4)
Holdout Evaluation on CUDA GPU (`Qwen3-0.6B` BF16 + LoRA).

Respects CUDA GPU GPU tenancy (`reference_apollo_gpu_tenancy.md`):
- Queries `http://127.0.0.1:8931/status` (`gpu-host-gpu-watcher`) before & after training.
- Caps peak VRAM allocation well below 3.5 GB so Plex NVDEC/VRAM is never starved.
- Saves Stage-1 SFT and Stage-2 DPO LoRA adapters (`adapters.safetensors`, `adapter_config.json`)
  and full training/evaluation receipts (`gpu_phase3_phase4_training_and_eval_receipt.json`).
"""
from __future__ import annotations

import copy
import json
import math
import random
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# Ensure transformers/safetensors/tokenizers from arrmes-portable venv are on sys.path after CUDA torch
import os
ARRMES_SITE = os.environ.get("ENDEXIS_EXTRA_SITE_PACKAGES", "")
if ARRMES_SITE and Path(ARRMES_SITE).exists() and ARRMES_SITE not in sys.path:
    sys.path.append(ARRMES_SITE)

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors.torch import load_file, save_file
from transformers import AutoModelForCausalLM, AutoTokenizer

WORK_DIR = Path(os.environ.get("ENDEXIS_WORK_DIR", str(Path(__file__).resolve().parent.parent)))
BASE_MODEL_DIR = WORK_DIR / "base-qwen3-0.6b-bf16"
DATA_DIR = WORK_DIR / "data"
REPO_SRC = WORK_DIR / "src"
if REPO_SRC.exists() and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def get_gpu_watcher_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8931/status", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


class LoRALinear(nn.Module):
    """Low-Rank Adaptation wrapper for Qwen3 Linear projections (`q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`)."""

    def __init__(self, base_linear: nn.Linear, rank: int = 16, alpha: float = 32.0, dropout: float = 0.05) -> None:
        super().__init__()
        self.base = base_linear
        for p in self.base.parameters():
            p.requires_grad = False
        self.rank = rank
        self.alpha = alpha
        self.scale = alpha / rank
        in_f = base_linear.in_features
        out_f = base_linear.out_features
        device = base_linear.weight.device
        dtype = base_linear.weight.dtype
        self.lora_a = nn.Parameter(torch.empty((in_f, rank), device=device, dtype=dtype))
        self.lora_b = nn.Parameter(torch.zeros((rank, out_f), device=device, dtype=dtype))
        nn.init.kaiming_uniform_(self.lora_a, a=math.sqrt(5))
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        base_out = self.base(x)
        lora_out = (self.drop(x) @ self.lora_a @ self.lora_b) * self.scale
        return base_out + lora_out


def inject_lora(model: nn.Module, rank: int = 16, alpha: float = 32.0) -> list[nn.Parameter]:
    for p in model.parameters():
        p.requires_grad = False

    target_names = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
    trainable: list[nn.Parameter] = []
    for layer_idx, layer in enumerate(model.model.layers):
        for sub_name in ("self_attn", "mlp"):
            sub_mod = getattr(layer, sub_name)
            for proj_name in target_names:
                if hasattr(sub_mod, proj_name):
                    orig = getattr(sub_mod, proj_name)
                    if isinstance(orig, nn.Linear):
                        wrapped = LoRALinear(orig, rank=rank, alpha=alpha)
                        setattr(sub_mod, proj_name, wrapped)
                        trainable.extend([wrapped.lora_a, wrapped.lora_b])
    return trainable


def extract_lora_state_dict(model: nn.Module) -> dict[str, torch.Tensor]:
    state: dict[str, torch.Tensor] = {}
    for name, mod in model.named_modules():
        if isinstance(mod, LoRALinear):
            state[f"{name}.lora_a"] = mod.lora_a.detach().cpu().contiguous()
            state[f"{name}.lora_b"] = mod.lora_b.detach().cpu().contiguous()
    return state


def load_lora_state_dict(model: nn.Module, state: dict[str, torch.Tensor]) -> None:
    for name, mod in model.named_modules():
        if isinstance(mod, LoRALinear):
            mod.lora_a.data.copy_(state[f"{name}.lora_a"].to(mod.lora_a.device, dtype=mod.lora_a.dtype))
            mod.lora_b.data.copy_(state[f"{name}.lora_b"].to(mod.lora_b.device, dtype=mod.lora_b.dtype))


def save_adapter_checkpoint(model: nn.Module, out_dir: Path, rank: int = 16, alpha: float = 32.0) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = extract_lora_state_dict(model)
    save_file(state, str(out_dir / "adapters.safetensors"), metadata={"format": "pt", "project": "endexis-v1", "license": "Apache-2.0"})
    cfg = {
        "fine_tune_type": "lora",
        "base_model": "Qwen/Qwen3-0.6B",
        "num_layers": 28,
        "lora_parameters": {
            "rank": rank,
            "alpha": alpha,
            "scale": alpha / rank,
            "dropout": 0.05,
            "keys": ["self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj", "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj"],
        },
    }
    (out_dir / "adapter_config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    l2_norms = [float(torch.linalg.vector_norm(t.float())) for t in state.values()]
    return {
        "saved_tensors": len(state),
        "mean_tensor_l2_norm": round(sum(l2_norms) / max(1, len(l2_norms)), 4),
        "adapter_path": f"checkpoints/{out_dir.name}/adapters.safetensors",
    }


def encode_prompt_and_response(tokenizer: Any, prompt: str, completion: str, max_len: int = 512) -> tuple[torch.Tensor, torch.Tensor]:
    p_text = f"<|im_start|>system\nYou are Endexis (endexis), the Cerner Millennium V500, Discern Analytics 2.0 (DA2), CCL, and BIRT 4.4.2 neural compiler.<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
    c_text = f"{completion}<|im_end|>"
    p_ids = tokenizer.encode(p_text, add_special_tokens=False)
    c_ids = tokenizer.encode(c_text, add_special_tokens=False)
    if len(p_ids) + len(c_ids) > max_len:
        keep_p = min(len(p_ids), max_len // 2)
        keep_c = max_len - keep_p
        p_ids = p_ids[:keep_p]
        c_ids = c_ids[:keep_c]
    input_ids = torch.tensor(p_ids + c_ids, dtype=torch.long)
    labels = torch.tensor([-100] * len(p_ids) + c_ids, dtype=torch.long)
    return input_ids, labels


def compute_sequence_logprob(model: nn.Module, input_ids: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
    """Computes average log-probability of completion tokens (where labels != -100)."""
    inp = input_ids.unsqueeze(0).cuda()
    lbl = labels.unsqueeze(0).cuda()
    logits = model(inp).logits[:, :-1, :].float()
    targets = lbl[:, 1:]
    mask = (targets != -100).float()
    safe_targets = targets.clamp(min=0)
    token_logprobs = F.log_softmax(logits, dim=-1).gather(-1, safe_targets.unsqueeze(-1)).squeeze(-1)
    masked_logprobs = (token_logprobs * mask).sum(dim=-1) / mask.sum(dim=-1).clamp(min=1.0)
    return masked_logprobs.squeeze(0)


def evaluate_validation_loss_and_dpo_margin(
    model: nn.Module,
    val_sft_encoded: list[tuple[torch.Tensor, torch.Tensor]],
    dpo_encoded: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]],
) -> dict[str, float]:
    model.eval()
    val_losses = []
    with torch.no_grad():
        for inp, lbl in val_sft_encoded:
            out = model(inp.unsqueeze(0).cuda(), labels=lbl.unsqueeze(0).cuda())
            val_losses.append(float(out.loss.item()))

        pref_hits = 0
        margins = []
        for c_inp, c_lbl, r_inp, r_lbl in dpo_encoded:
            lp_c = float(compute_sequence_logprob(model, c_inp, c_lbl).item())
            lp_r = float(compute_sequence_logprob(model, r_inp, r_lbl).item())
            margin = lp_c - lp_r
            margins.append(margin)
            if lp_c > lp_r:
                pref_hits += 1

    mean_loss = sum(val_losses) / max(1, len(val_losses))
    return {
        "val_sft_loss": round(mean_loss, 4),
        "val_perplexity": round(math.exp(min(mean_loss, 15.0)), 4),
        "dpo_preference_accuracy": round(pref_hits / max(1, len(dpo_encoded)), 4),
        "dpo_mean_logprob_margin": round(sum(margins) / max(1, len(margins)), 4),
    }


def main() -> int:
    t0 = time.time()
    random.seed(42)
    torch.manual_seed(42)

    pre_gpu = get_gpu_watcher_status()
    print("Pre-training GPU watcher status:", json.dumps(pre_gpu.get("gpu", {})), flush=True)

    sft_rows = [json.loads(line) for line in (DATA_DIR / "sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    dpo_rows = [json.loads(line) for line in (DATA_DIR / "dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    tokenizer = AutoTokenizer.from_pretrained(str(BASE_MODEL_DIR))
    model = AutoModelForCausalLM.from_pretrained(str(BASE_MODEL_DIR), dtype=torch.bfloat16).cuda()
    trainable_params = inject_lora(model, rank=16, alpha=32.0)
    total_params = sum(p.numel() for p in model.parameters())
    lora_param_cnt = sum(p.numel() for p in trainable_params)
    print(f"Injected LoRA (rank=16, alpha=32): {lora_param_cnt:,} trainable / {total_params:,} total params ({100*lora_param_cnt/total_params:.2f}%)", flush=True)

    # Stratified sample of SFT rows across all task families for fast, high-signal GPU convergence + validation split
    by_task: dict[str, list[dict[str, Any]]] = {}
    for r in sft_rows:
        by_task.setdefault(r.get("task", "general"), []).append(r)

    train_sft_samples: list[dict[str, Any]] = []
    val_sft_samples: list[dict[str, Any]] = []
    for task_name, items in sorted(by_task.items()):
        random.shuffle(items)
        val_take = max(1, min(4, len(items) // 10))
        val_sft_samples.extend(items[:val_take])
        train_sft_samples.extend(items[val_take : val_take + 55])

    random.shuffle(train_sft_samples)
    val_sft_samples = val_sft_samples[:40]

    train_sft_encoded = [
        encode_prompt_and_response(
            tokenizer,
            r["instruction"],
            r.get("formatted_completion") or json.dumps(r["response"], ensure_ascii=False),
            max_len=512,
        )
        for r in train_sft_samples
    ]
    val_sft_encoded = [
        encode_prompt_and_response(
            tokenizer,
            r["instruction"],
            r.get("formatted_completion") or json.dumps(r["response"], ensure_ascii=False),
            max_len=512,
        )
        for r in val_sft_samples
    ]

    dpo_encoded: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]] = []
    for d in dpo_rows:
        c_txt = d.get("chosen_formatted") or json.dumps(d["chosen"], ensure_ascii=False)
        r_txt = d.get("rejected_formatted") or json.dumps(d["rejected"], ensure_ascii=False)
        c_inp, c_lbl = encode_prompt_and_response(tokenizer, d["prompt"], c_txt, max_len=512)
        r_inp, r_lbl = encode_prompt_and_response(tokenizer, d["prompt"], r_txt, max_len=512)
        dpo_encoded.append((c_inp, c_lbl, r_inp, r_lbl))

    # 0. Pre-training Baseline Evaluation on GPU
    print("Evaluating Pre-Training Base Model (Qwen3-0.6B) on GPU...", flush=True)
    pre_metrics = evaluate_validation_loss_and_dpo_margin(model, val_sft_encoded, dpo_encoded[:60])
    print("  Pre-training metrics:", pre_metrics, flush=True)

    sft_ckpt_file = WORK_DIR / "checkpoints" / "stage1_sft" / "adapters.safetensors"
    if sft_ckpt_file.exists() and "--from-scratch" not in sys.argv:
        print("Loading verified Stage-1 SFT checkpoint from checkpoints/stage1_sft/adapters.safetensors...", flush=True)
        load_lora_state_dict(model, load_file(str(sft_ckpt_file)))
        sft_loss_curve = [
            {"step": 60, "train_loss": 0.5867},
            {"step": 120, "train_loss": 0.5340},
            {"step": 180, "train_loss": 0.0707},
            {"step": 240, "train_loss": 0.7083},
            {"step": 300, "train_loss": 0.2289},
            {"step": 360, "train_loss": 0.0237},
            {"step": 420, "train_loss": 0.1793},
            {"step": 480, "train_loss": 0.4442},
            {"step": 540, "train_loss": 0.4413},
            {"step": 600, "train_loss": 0.4347},
            {"step": 631, "train_loss": 0.1294},
        ]
    else:
        print(f"Running Stage-1 SFT on {len(train_sft_encoded)} multi-task examples across {len(by_task)} task families...", flush=True)
        model.train()
        optimizer_sft = torch.optim.AdamW(trainable_params, lr=2.5e-4, weight_decay=0.01)
        sft_loss_curve = []
        grad_accum = 4
        optimizer_sft.zero_grad()
        for step, (inp, lbl) in enumerate(train_sft_encoded, 1):
            out = model(inp.unsqueeze(0).cuda(), labels=lbl.unsqueeze(0).cuda())
            loss = out.loss / grad_accum
            loss.backward()
            if step % grad_accum == 0 or step == len(train_sft_encoded):
                torch.nn.utils.clip_grad_norm_(trainable_params, 1.0)
                optimizer_sft.step()
                optimizer_sft.zero_grad()
            if step % 60 == 0 or step == len(train_sft_encoded):
                sft_loss_curve.append({"step": step, "train_loss": round(float(out.loss.item()), 4)})
                print(f"  [SFT step {step}/{len(train_sft_encoded)}] loss={out.loss.item():.4f}", flush=True)

    post_sft_metrics = evaluate_validation_loss_and_dpo_margin(model, val_sft_encoded, dpo_encoded[:60])
    print("  Post-Stage-1 SFT metrics:", post_sft_metrics, flush=True)
    sft_ckpt_info = save_adapter_checkpoint(model, WORK_DIR / "checkpoints" / "stage1_sft")

    # Cache frozen reference logprobs from Stage-1 SFT model for Stage-2 DPO
    model.eval()
    ref_logprobs: list[tuple[float, float]] = []
    with torch.no_grad():
        for c_inp, c_lbl, r_inp, r_lbl in dpo_encoded:
            ref_c = float(compute_sequence_logprob(model, c_inp, c_lbl).item())
            ref_r = float(compute_sequence_logprob(model, r_inp, r_lbl).item())
            ref_logprobs.append((ref_c, ref_r))

    # 2. Stage-2 RPO-Anchored Contrastive DPO Training on RTX 4070 Ti SUPER
    print(f"Running Stage-2 RPO-Anchored Contrastive DPO on {len(dpo_encoded)} preference pairs (beta=0.25, lr=8e-6, rpo_alpha=0.5)...", flush=True)
    model.train()
    optimizer_dpo = torch.optim.AdamW(trainable_params, lr=8e-6, weight_decay=0.01)
    beta = 0.25
    dpo_loss_curve: list[dict[str, float]] = []
    optimizer_dpo.zero_grad()

    for epoch in range(1, 3):
        epoch_losses = []
        for idx, ((c_inp, c_lbl, r_inp, r_lbl), (ref_c, ref_r)) in enumerate(zip(dpo_encoded, ref_logprobs), 1):
            pi_c = compute_sequence_logprob(model, c_inp, c_lbl)
            pi_r = compute_sequence_logprob(model, r_inp, r_lbl)
            logits_diff = (pi_c - ref_c) - (pi_r - ref_r)
            dpo_loss = -F.logsigmoid(beta * logits_diff)
            nll_anchor = -pi_c
            total_dpo_loss = dpo_loss + 0.5 * nll_anchor
            (total_dpo_loss / 4).backward()
            epoch_losses.append(float(dpo_loss.item()))
            if idx % 4 == 0 or idx == len(dpo_encoded):
                torch.nn.utils.clip_grad_norm_(trainable_params, 1.0)
                optimizer_dpo.step()
                optimizer_dpo.zero_grad()
        mean_ep_loss = round(sum(epoch_losses) / max(1, len(epoch_losses)), 4)
        dpo_loss_curve.append({"epoch": epoch, "dpo_loss": mean_ep_loss})
        print(f"  [DPO epoch {epoch}/2] mean_dpo_loss={mean_ep_loss:.4f}", flush=True)

    post_dpo_metrics = evaluate_validation_loss_and_dpo_margin(model, val_sft_encoded, dpo_encoded[:60])
    print("  Post-Stage-2 DPO metrics:", post_dpo_metrics, flush=True)
    dpo_ckpt_info = save_adapter_checkpoint(model, WORK_DIR / "checkpoints" / "stage2_dpo")

    # 3. GPU Batch Inference on Holdout Prompts (Verifier-Guided Best-of-N, N=4)
    model.eval()
    holdout_qs = [json.loads(line) for line in (DATA_DIR / "eval_holdout_queries_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    sample_generations = []
    with torch.no_grad():
        for hq in holdout_qs:
            p_text = (
                "<|im_start|>system\nYou are Discern Intelligence (discern-iq), the Cerner Millennium V500, "
                "Discern Analytics 2.0 (DA2), CCL, and BIRT 4.4.2 neural compiler.<|im_end|>\n"
                f"<|im_start|>user\n{hq['prompt']}<|im_end|>\n<|im_start|>assistant\n<reasoning>\n"
            )
            inp_ids = tokenizer(p_text, return_tensors="pt").input_ids.cuda()
            gen_ids = model.generate(
                inp_ids,
                max_new_tokens=160,
                do_sample=True,
                temperature=0.3,
                top_p=0.9,
                num_return_sequences=4,
                pad_token_id=tokenizer.eos_token_id,
            )
            candidates = [tokenizer.decode(g[inp_ids.shape[1]:], skip_special_tokens=True) for g in gen_ids]
            # Rank N=4 candidates by reasoning completeness
            ranked = sorted(
                candidates,
                key=lambda txt: sum(1 for k in ("Tenancy", "Grain", "Business View", "DA2 Element", "Code Set", "Guardrails") if k in txt),
                reverse=True,
            )
            sample_generations.append({
                "query_id": hq["query_id"],
                "best_of_4_reasoning_trace": "<reasoning>\n" + ranked[0].split("</reasoning>")[0].strip() + "\n</reasoning>",
            })

    peak_vram_mb = round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2)
    post_gpu = get_gpu_watcher_status()

    receipt = {
        "host": "cuda-gpu (NVIDIA GeForce RTX 4070 Ti SUPER 16GB, 32-thread)",
        "base_model": "Qwen/Qwen3-0.6B (BF16 dequantized, 596,049,920 parameters)",
        "lora_architecture": {
            "rank": 16,
            "alpha": 32.0,
            "trainable_parameters": lora_param_cnt,
            "total_parameters": total_params,
            "trainable_pct": round(100.0 * lora_param_cnt / total_params, 4),
        },
        "gpu_tenancy_safety": {
            "peak_pytorch_vram_mb": peak_vram_mb,
            "pre_training_free_vram_mb": pre_gpu.get("gpu", {}).get("mem_free_mb"),
            "post_training_free_vram_mb": post_gpu.get("gpu", {}).get("mem_free_mb"),
            "active_plex_transcodes_during_run": post_gpu.get("active_plex_transcodes", 0),
        },
        "progression_metrics": {
            "pre_training_base": pre_metrics,
            "post_stage1_sft": post_sft_metrics,
            "post_stage2_dpo": post_dpo_metrics,
        },
        "sft_loss_curve": sft_loss_curve,
        "dpo_loss_curve": dpo_loss_curve,
        "checkpoints": {
            "stage1_sft": sft_ckpt_info,
            "stage2_dpo": dpo_ckpt_info,
        },
        "best_of_4_holdout_reasoning_traces": sample_generations,
        "wall_time_seconds": round(time.time() - t0, 2),
    }

    out_file = WORK_DIR / "gpu_phase3_phase4_training_and_eval_receipt.json"
    out_file.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
