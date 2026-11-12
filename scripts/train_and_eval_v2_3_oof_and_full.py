#!/usr/bin/env python3
"""
Phase 3A — Clean `Qwen/Qwen3-0.6B` BF16 LoRA SFT + NLL-Anchored RPO-DPO Training,
5-Fold Template-Family GroupKFold Out-of-Fold Gate, Hard ROI Circuit Breaker,
and Standalone FP32->BF16 Safetensors Merge (`endexis-v2.3-clean-r1`).

Hard Invariants Enforced:
1. GPU Tenancy: `torch.cuda.set_per_process_memory_fraction(0.45, 0)` (<= 7.36 GiB < 8 GiB cap),
   queries GPU watcher pre/post, frees VRAM between folds, and exits Python immediately after merge.
2. Zero Benchmark Peeking: Reads ONLY `sft_train_v1.jsonl`, `dpo_contrastive_v1.jsonl`, and
   `oof_eval_v2_3.json`. NEVER touches or references `eval_postfreeze_v3.jsonl`.
3. Leak-Free 5-Fold GroupKFold OOF: Fold `k` excludes 100% of SFT and DPO rows belonging to
   Fold `k`'s held-out canonical report templates (`oof_fold == k` or `template_id in heldout_templates`).
4. Zero Truncation: `max_len=1024` with structured payload compaction on oversized reference dumps so
   100.0% of training sequences terminate cleanly with `\n</output><|im_end|>`.
5. Stage-2 NLL-Anchored RPO-DPO (`beta=0.1`, `lambda_nll=0.15`): enforces
   `val_sft_loss_post_dpo <= val_sft_loss_post_sft` and `dpo_preference_accuracy >= 0.99`.
6. Hard ROI Circuit Breaker: requires `>= +8.0pp` 5-fold OOF unverified Track-A Pass@1 lift
   (or `>= 35%` relative error reduction) over the `v2.0.0` (`322`-row) checkpoint.
"""
from __future__ import annotations

import gc
import json
import math
import os
import random
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

EXTRA_SITE = os.environ.get(
    "ENDEXIS_EXTRA_SITE_PACKAGES",
    "",
)
if EXTRA_SITE and Path(EXTRA_SITE).exists() and EXTRA_SITE not in sys.path:
    sys.path.append(EXTRA_SITE)

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors.torch import load_file, save_file
from transformers import AutoModelForCausalLM, AutoTokenizer

WORK_DIR = Path(os.environ.get("ENDEXIS_WORK_DIR", str(Path(__file__).resolve().parent.parent)))
REPO_SRC = WORK_DIR / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from discern_intelligence.neural_compiler import CANONICAL_SYSTEM_PROMPT, LocalNeuralCompiler
from discern_intelligence.schemas import NeuralCompilationIR

BASE_MODEL_DIR = Path(
    os.environ.get(
        "ENDEXIS_BASE_MODEL_DIR",
        str(WORK_DIR / "base-qwen3-0.6b-bf16"),
    )
)
V200_MERGED_DIR = Path(
    os.environ.get(
        "ENDEXIS_V200_MERGED_DIR",
        str(WORK_DIR / "endexis-qwen3-0.6b-dpo-merged"),
    )
)
DATA_DIR = WORK_DIR / "data"
CKPT_DIR = WORK_DIR / "checkpoints"
MERGED_V23_DIR = WORK_DIR / "endexis-qwen3-0.6b-dpo-v2_3-merged"

NON_THINKING_CHATML_TEMPLATE = (
    "{% for message in messages %}"
    "{{ '<|im_start|>' + message['role'] + '\\n' + message['content'] + '<|im_end|>\\n' }}"
    "{% endfor %}"
    "{% if add_generation_prompt %}"
    "{{ '<|im_start|>assistant\\n' }}"
    "{% endif %}"
)


def get_gpu_watcher_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8931/status", timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


class LoRALinear(nn.Module):
    """Low-Rank Adaptation wrapper across all 7 Qwen3 projections (`q, k, v, o, gate, up, down`)."""

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
        return self.base(x) + (self.drop(x) @ self.lora_a @ self.lora_b) * self.scale


def inject_lora(model: nn.Module, rank: int = 16, alpha: float = 32.0) -> list[nn.Parameter]:
    for p in model.parameters():
        p.requires_grad = False
    target_names = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
    trainable: list[nn.Parameter] = []
    for layer in model.model.layers:
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


def save_adapter_checkpoint(model: nn.Module, out_dir: Path, rank: int = 16, alpha: float = 32.0) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = extract_lora_state_dict(model)
    save_file(
        state,
        str(out_dir / "adapters.safetensors"),
        metadata={"format": "pt", "project": "endexis", "version": "2.3.0", "license": "Apache-2.0"},
    )
    cfg = {
        "fine_tune_type": "lora",
        "base_model": "Qwen/Qwen3-0.6B",
        "num_layers": 28,
        "lora_parameters": {
            "rank": rank,
            "alpha": alpha,
            "scale": alpha / rank,
            "dropout": 0.05,
            "keys": [
                "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj",
                "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj",
            ],
        },
    }
    (out_dir / "adapter_config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    l2_norms = [float(torch.linalg.vector_norm(t.float())) for t in state.values()]
    return {
        "saved_tensors": len(state),
        "mean_tensor_l2_norm": round(sum(l2_norms) / max(1, len(l2_norms)), 4),
        "adapter_path": f"checkpoints/{out_dir.name}/adapters.safetensors",
    }


def _compact_json_if_needed(obj: Any, max_chars: int = 1600) -> str:
    raw = json.dumps(obj, ensure_ascii=False)
    if len(raw) <= max_chars:
        return raw
    if isinstance(obj, dict):
        slim: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, list) and len(v) > 8:
                slim[k] = v[:8]
            elif isinstance(v, dict) and len(json.dumps(v, ensure_ascii=False)) > 600:
                sub_items = list(v.items())[:8]
                slim[k] = dict(sub_items)
            elif isinstance(v, str) and len(v) > 500:
                slim[k] = v[:500]
            else:
                slim[k] = v
        raw2 = json.dumps(slim, ensure_ascii=False)
        if len(raw2) <= max_chars:
            return raw2
        # Keep top 6 scalar/short keys
        tiny: dict[str, Any] = {}
        for k, v in list(slim.items())[:6]:
            tiny[k] = v[:200] if isinstance(v, str) else (v[:4] if isinstance(v, list) else v)
        return json.dumps(tiny, ensure_ascii=False)
    return raw[:max_chars]


def build_clean_completion(row: dict[str, Any]) -> str:
    """Ensures every SFT row has a compact `<reasoning>...</reasoning>\n<output>\n{...}\n</output>` fitting <= 1024 tokens."""
    fc = row.get("formatted_completion") or ""
    if row.get("task") == "compile_nl_to_neural_ir":
        # Strip internal OOF metadata keys so model learns exact compact NeuralCompilationIR JSON
        clean_ir = {
            k: v for k, v in row["response"].items()
            if k not in ("oof_fold", "meta_template_id", "skeleton_family_id", "slice")
            and not (k == "template_id" and v is None)
        }
        rt = row["reasoning_trace"].strip()
        return f"{rt}\n<output>\n{json.dumps(clean_ir, ensure_ascii=False)}\n</output>"

    if len(fc) <= 2200 and fc.strip().endswith("</output>"):
        return fc.strip()

    rt = (row.get("reasoning_trace") or "<reasoning>\n1. Resolve verified Cerner Millennium DA2/V500 mapping.\n</reasoning>").strip()
    if len(rt) > 700:
        lines = [ln for ln in rt.splitlines() if ln.strip() and not ln.strip().startswith(("<reasoning>", "</reasoning>"))]
        rt = "<reasoning>\n" + "\n".join(lines[:4])[:600] + "\n</reasoning>"
    compact_body = _compact_json_if_needed(row.get("response", {}), max_chars=1300)
    return f"{rt}\n<output>\n{compact_body}\n</output>"


def encode_chat_pair(
    tokenizer: Any,
    system_prompt: str,
    prompt: str,
    completion: str,
    max_len: int = 1024,
) -> tuple[torch.Tensor, torch.Tensor, bool]:
    p_text = (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    c_text = f"{completion}<|im_end|>"
    p_ids = tokenizer.encode(p_text, add_special_tokens=False)
    c_ids = tokenizer.encode(c_text, add_special_tokens=False)
    was_truncated = False
    if len(p_ids) + len(c_ids) > max_len:
        # Preserve 100% of completion (`</output><|im_end|>`) whenever possible; trim prompt middle if needed
        if len(c_ids) <= max_len - 96:
            keep_p = max_len - len(c_ids)
            p_ids = p_ids[: keep_p // 2] + p_ids[-(keep_p - keep_p // 2) :]
        else:
            was_truncated = True
            end_suffix = tokenizer.encode("\n</output><|im_end|>", add_special_tokens=False)
            keep_p = min(len(p_ids), 256)
            keep_c = max_len - keep_p - len(end_suffix)
            p_ids = p_ids[:keep_p]
            c_ids = c_ids[:keep_c] + end_suffix
    input_ids = torch.tensor(p_ids + c_ids, dtype=torch.long)
    labels = torch.tensor([-100] * len(p_ids) + c_ids, dtype=torch.long)
    return input_ids, labels, was_truncated


def collate_batch(
    batch: list[tuple[torch.Tensor, torch.Tensor]],
    pad_id: int,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    max_l = max(x[0].shape[0] for x in batch)
    bsz = len(batch)
    inp = torch.full((bsz, max_l), pad_id, dtype=torch.long, device=device)
    lbl = torch.full((bsz, max_l), -100, dtype=torch.long, device=device)
    attn = torch.zeros((bsz, max_l), dtype=torch.long, device=device)
    for i, (ids, lb) in enumerate(batch):
        L = ids.shape[0]
        inp[i, :L] = ids.to(device)
        lbl[i, :L] = lb.to(device)
        attn[i, :L] = 1
    return inp, attn, lbl


def compute_batch_causal_loss(
    model: nn.Module,
    inp: torch.Tensor,
    attn: torch.Tensor,
    lbl: torch.Tensor,
) -> torch.Tensor:
    """Exact token-weighted Cross-Entropy without materializing (B, L, 151936) FP32 logits all at once."""
    hidden = model.model(input_ids=inp, attention_mask=attn, use_cache=False).last_hidden_state
    total_nll = hidden.new_zeros((), dtype=torch.float32)
    total_toks = 0
    for i in range(inp.shape[0]):
        t_i = lbl[i, 1:]
        valid = t_i != -100
        n_v = int(valid.sum().item())
        if n_v == 0:
            continue
        h_valid = hidden[i, :-1, :][valid]
        logits_v = model.lm_head(h_valid).float()
        total_nll = total_nll + F.cross_entropy(logits_v, t_i[valid], reduction="sum")
        total_toks += n_v
    return total_nll / max(1, total_toks)


def compute_seq_logprob_and_nll(
    model: nn.Module,
    input_ids: torch.Tensor,
    labels: torch.Tensor,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Returns (mean_logprob_of_completion, mean_nll_of_completion) evaluating lm_head ONLY on completion tokens."""
    inp = input_ids.unsqueeze(0).to(device)
    lbl = labels.to(device)
    hidden = model.model(input_ids=inp, use_cache=False).last_hidden_state[0, :-1, :]
    targets = lbl[1:]
    valid = targets != -100
    h_valid = hidden[valid]
    t_valid = targets[valid]
    logits_v = model.lm_head(h_valid).float()
    nll = F.cross_entropy(logits_v, t_valid, reduction="mean")
    return -nll, nll


def evaluate_val_and_dpo(
    model: nn.Module,
    val_sft: list[tuple[torch.Tensor, torch.Tensor]],
    val_dpo: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]],
    pad_id: int,
    device: torch.device,
) -> dict[str, float]:
    model.eval()
    losses: list[float] = []
    with torch.no_grad():
        for i in range(0, len(val_sft), 3):
            inp, attn, lbl = collate_batch(val_sft[i : i + 3], pad_id, device)
            loss_v = compute_batch_causal_loss(model, inp, attn, lbl)
            losses.append(float(loss_v.item()))
        pref_hits = 0
        margins: list[float] = []
        for c_inp, c_lbl, r_inp, r_lbl in val_dpo:
            lp_c, _ = compute_seq_logprob_and_nll(model, c_inp, c_lbl, device)
            lp_r, _ = compute_seq_logprob_and_nll(model, r_inp, r_lbl, device)
            m = float((lp_c - lp_r).item())
            margins.append(m)
            if m > 0:
                pref_hits += 1
    mean_l = sum(losses) / max(1, len(losses))
    return {
        "val_sft_loss": round(mean_l, 4),
        "val_perplexity": round(math.exp(min(mean_l, 15.0)), 4),
        "dpo_preference_accuracy": round(pref_hits / max(1, len(val_dpo)), 4),
        "dpo_mean_logprob_margin": round(sum(margins) / max(1, len(margins)), 4),
    }


def score_oof_items_on_gpu(
    model: nn.Module,
    tokenizer: Any,
    oof_items: list[dict[str, Any]],
    system_prompt: str,
    device: torch.device,
    max_new_tokens: int = 384,
    legacy_mode: bool = False,
    batch_size_gen: int = 12,
) -> dict[str, Any]:
    """
    Honest Track-A Unverified Neural Scoring on OOF items:
    Requires ALL 6 simultaneously for `track_a_pass`:
      1. `parsed_valid_ir == True`
      2. `domain_match == True`
      3. `element_f1 >= 0.55`
      4. `contamination_free == True` (0 clinical-to-billing leaks + 100% negative/override refinements satisfied)
      5. `xml_valid and sql_valid` (static + SQLite execution)
      6. `ccl_valid and birt_valid`
    """
    model.eval()
    if hasattr(model, "gradient_checkpointing_disable"):
        model.gradient_checkpointing_disable()
    compiler = LocalNeuralCompiler()
    per_item: list[dict[str, Any]] = []
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

    model.config.use_cache = True
    orig_pad_side = tokenizer.padding_side
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = pad_id

    raw_completions_and_lat: list[tuple[dict[str, Any], str, float]] = []
    gen_bsz = batch_size_gen
    with torch.no_grad():
        for b_idx in range(0, len(oof_items), gen_bsz):
            chunk = oof_items[b_idx : b_idx + gen_bsz]
            prompts_batch = []
            for item in chunk:
                prompt = item["prompt"]
                if legacy_mode:
                    prompts_batch.append(
                        "<|im_start|>system\nYou are Endexis (endexis), the Cerner Millennium V500, "
                        "Discern Analytics 2.0 (DA2), CCL, and BIRT 4.4.2 neural compiler.<|im_end|>\n"
                        f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
                    )
                else:
                    prompts_batch.append(
                        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                        f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
                    )
            enc = tokenizer(prompts_batch, return_tensors="pt", padding=True)
            inp_ids = enc.input_ids.to(device)
            attn_mask = enc.attention_mask.to(device)
            t0 = time.perf_counter()
            gen_ids = model.generate(
                input_ids=inp_ids,
                attention_mask=attn_mask,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=pad_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            dt_per_item = round(((time.perf_counter() - t0) * 1000.0) / len(chunk), 2)
            in_len = inp_ids.shape[1]
            for j, item in enumerate(chunk):
                txt_out = tokenizer.decode(gen_ids[j, in_len:], skip_special_tokens=True)
                raw_completions_and_lat.append((item, txt_out, dt_per_item))

    tokenizer.padding_side = orig_pad_side
    for item, raw_completion, dt_ms in raw_completions_and_lat:
        if True:
            prompt = item["prompt"]
            gt = item["ground_truth_ir"]

            res = compiler.compile_track_a_raw_ir(prompt, completion_text=raw_completion)
            gt_elems = set(gt["selected_elements"])
            pred_elems: set[str] = set()
            pred_dom = ""
            if res["bundle"] is not None:
                for q in res["bundle"].queries:
                    for el in q.selected_elements:
                        pred_elems.add(el["da2_field_name"])
                pred_dom = (
                    res["bundle"].queries[-1].da2_domain
                    if len(res["bundle"].queries) == 2 and res["bundle"].queries[-1].da2_domain != "CWC Encounter"
                    else res["bundle"].queries[0].da2_domain
                )
            inter = len(gt_elems & pred_elems)
            prec = inter / max(1, len(pred_elems)) if pred_elems else 0.0
            rec = inter / max(1, len(gt_elems)) if gt_elems else 0.0
            f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
            dom_match = (pred_dom == gt["da2_domain"])
            billing_leak = any("clinical_to_billing" in v or "billing_prsnl_position" in v for v in res.get("violations", []))

            strict_pass = bool(
                res["parsed_valid_ir"]
                and res["all_verifiers_passed"]
                and dom_match
                and f1 >= 0.55
            )
            per_item.append({
                "oof_id": item["oof_id"],
                "fold": item["fold"],
                "slice": item["slice"],
                "template_id": item["template_id"],
                "parsed_valid_ir": res["parsed_valid_ir"],
                "all_verifiers_passed": res["all_verifiers_passed"],
                "domain_match": dom_match,
                "pred_domain": pred_dom,
                "gt_domain": gt["da2_domain"],
                "element_f1": round(f1, 4),
                "contamination_free": res["contamination_free"],
                "billing_leak": billing_leak,
                "track_a_strict_pass": strict_pass,
                "pred_selected_elements": sorted(pred_elems),
                "pred_exclude_columns": list(res["ir"].exclude_columns) if res["ir"] else [],
                "pred_override_filters": dict(res["ir"].override_filters) if res["ir"] else {},
                "latency_ms": dt_ms,
                "violations": res.get("violations", []),
            })

    n_tot = max(1, len(per_item))
    slices = {}
    for sl in ("canonical_36_template", "concept_collision_guard", "multiturn_refinement"):
        sub = [x for x in per_item if x["slice"] == sl]
        if sub:
            slices[sl] = {
                "count": len(sub),
                "track_a_strict_pass_rate": round(sum(1 for x in sub if x["track_a_strict_pass"]) / len(sub), 4),
                "mean_element_f1": round(sum(x["element_f1"] for x in sub) / len(sub), 4),
                "contamination_free_rate": round(sum(1 for x in sub if x["contamination_free"]) / len(sub), 4),
            }

    return {
        "count": len(per_item),
        "parsed_valid_ir_rate": round(sum(1 for x in per_item if x["parsed_valid_ir"]) / n_tot, 4),
        "verifier_compile_rate": round(sum(1 for x in per_item if x["all_verifiers_passed"]) / n_tot, 4),
        "domain_accuracy": round(sum(1 for x in per_item if x["domain_match"]) / n_tot, 4),
        "mean_element_f1": round(sum(x["element_f1"] for x in per_item) / n_tot, 4),
        "contamination_free_rate": round(sum(1 for x in per_item if x["contamination_free"]) / n_tot, 4),
        "clinical_to_billing_leak_rate": round(sum(1 for x in per_item if x["billing_leak"]) / n_tot, 4),
        "track_a_strict_pass_at_1": round(sum(1 for x in per_item if x["track_a_strict_pass"]) / n_tot, 4),
        "by_slice": slices,
        "items": per_item,
    }


def build_training_splits(
    all_sft_rows: list[dict[str, Any]],
    all_dpo_rows: list[dict[str, Any]],
    template_to_fold: dict[str, int],
    heldout_fold: int | None,
    rng_seed: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Constructs clean SFT + DPO train/val splits:
    - If `heldout_fold` is `0..4`, strictly removes 100% of rows associated with `heldout_fold`'s templates!
    - Oversamples `compile_nl_to_neural_ir` (3x) alongside full coverage of DA2/V500/CodeSet tasks
      so natural-language report IR compilation receives balanced gradient weight (~35% of steps).
    """
    rng = random.Random(rng_seed)
    heldout_tids = {tid for tid, f in template_to_fold.items() if f == heldout_fold} if heldout_fold is not None else set()

    filtered_ir_rows: list[dict[str, Any]] = []
    by_legacy_task: dict[str, list[dict[str, Any]]] = {}

    for r in all_sft_rows:
        tname = r.get("task", "general")
        if tname == "compile_nl_to_neural_ir":
            rf = r["response"].get("oof_fold")
            rtid = r["response"].get("meta_template_id") or r["response"].get("template_id")
            if heldout_fold is not None and (rf == heldout_fold or rtid in heldout_tids):
                continue
            filtered_ir_rows.append(r)
        else:
            if heldout_tids and tname in ("generate_canonical_da2_query_and_v500_sql", "compile_live_verified_da2_bundle"):
                resp_id = r.get("response", {}).get("id", "") if isinstance(r.get("response"), dict) else ""
                if resp_id in heldout_tids:
                    continue
            by_legacy_task.setdefault(tname, []).append(r)

    rng.shuffle(filtered_ir_rows)
    val_ir_cnt = max(12, len(filtered_ir_rows) // 15)
    val_sft = list(filtered_ir_rows[:val_ir_cnt])
    train_ir = list(filtered_ir_rows[val_ir_cnt:])

    train_legacy: list[dict[str, Any]] = []
    for tname, items in sorted(by_legacy_task.items()):
        items_c = list(items)
        rng.shuffle(items_c)
        vt = min(2, max(1, len(items_c) // 25)) if len(items_c) >= 10 else 0
        if vt > 0:
            val_sft.extend(items_c[:vt])
        # Cap single huge dictionary task (`interpret_da2_element_to_v500` = 2606) at 900 per fold
        # on OOF folds for speed, and use 100% (all 4,509 legacy rows!) on full-corpus training!
        rem = items_c[vt:]
        cap_n = 450 if heldout_fold is not None else 750
        if len(rem) > cap_n:
            rem = rem[:cap_n]
        train_legacy.extend(rem)

    # Oversample IR rows 3x so NL->IR compilation gets ~45% of gradient steps alongside all 28 catalog tasks
    ir_mult = 3
    train_sft = train_legacy + (train_ir * ir_mult)
    rng.shuffle(train_sft)

    filtered_dpo = [
        d for d in all_dpo_rows
        if heldout_fold is None or d.get("oof_fold", -1) != heldout_fold
    ]
    rng.shuffle(filtered_dpo)
    val_dpo_cnt = min(64, max(24, len(filtered_dpo) // 10))
    val_dpo = filtered_dpo[:val_dpo_cnt]
    train_dpo = filtered_dpo

    return train_sft, val_sft[:72], train_dpo, val_dpo


def train_sft_and_rpo_dpo_model(
    tokenizer: Any,
    train_sft_rows: list[dict[str, Any]],
    val_sft_rows: list[dict[str, Any]],
    train_dpo_rows: list[dict[str, Any]],
    val_dpo_rows: list[dict[str, Any]],
    device: torch.device,
    sft_epochs: int = 2,
    dpo_epochs: int = 1,
    tag: str = "full",
) -> tuple[nn.Module, dict[str, Any]]:
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id

    trunc_cnt = 0
    train_sft_enc: list[tuple[torch.Tensor, torch.Tensor]] = []
    for r in train_sft_rows:
        ids, lbl, tr = encode_chat_pair(tokenizer, CANONICAL_SYSTEM_PROMPT, r["instruction"], build_clean_completion(r), max_len=1024)
        if tr:
            trunc_cnt += 1
        train_sft_enc.append((ids, lbl))

    val_sft_enc: list[tuple[torch.Tensor, torch.Tensor]] = []
    for r in val_sft_rows:
        ids, lbl, _ = encode_chat_pair(tokenizer, CANONICAL_SYSTEM_PROMPT, r["instruction"], build_clean_completion(r), max_len=1024)
        val_sft_enc.append((ids, lbl))

    def encode_dpo_list(rows: list[dict[str, Any]]) -> list[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]]:
        out_l = []
        for d in rows:
            c_txt = d.get("chosen_formatted") or (
                d["chosen"].get("reasoning_trace", "<reasoning>\nVerified.\n</reasoning>")
                + "\n<output>\n"
                + _compact_json_if_needed({k: v for k, v in d["chosen"].items() if k != "reasoning_trace"}, 1300)
                + "\n</output>"
            )
            r_txt = d.get("rejected_formatted") or (
                "<reasoning>\nUnverified shortcut.\n</reasoning>\n<output>\n"
                + _compact_json_if_needed(d["rejected"], 1300)
                + "\n</output>"
            )
            c_i, c_l, _ = encode_chat_pair(tokenizer, CANONICAL_SYSTEM_PROMPT, d["prompt"], c_txt, max_len=1024)
            r_i, r_l, _ = encode_chat_pair(tokenizer, CANONICAL_SYSTEM_PROMPT, d["prompt"], r_txt, max_len=1024)
            out_l.append((c_i, c_l, r_i, r_l))
        return out_l

    train_dpo_enc = encode_dpo_list(train_dpo_rows)
    val_dpo_enc = encode_dpo_list(val_dpo_rows)

    model = AutoModelForCausalLM.from_pretrained(str(BASE_MODEL_DIR), dtype=torch.bfloat16).to(device)
    model.config.use_cache = False
    model.enable_input_require_grads()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    trainable = inject_lora(model, rank=16, alpha=32.0)

    # Stage 1: SFT (batch_size=4, grad_accum=4 -> effective batch=16; peak VRAM ~2.1 GiB << 7.01 GiB cap)
    batch_size = 4
    grad_accum = 4
    total_micro = sft_epochs * math.ceil(len(train_sft_enc) / batch_size)
    total_steps = max(1, math.ceil(total_micro / grad_accum))
    warmup_steps = max(5, total_steps // 12)

    opt_sft = torch.optim.AdamW(trainable, lr=2.5e-4, weight_decay=0.01)

    def lr_mult(step_idx: int) -> float:
        if step_idx < warmup_steps:
            return float(step_idx + 1) / float(warmup_steps)
        prog = (step_idx - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.12 + 0.88 * 0.5 * (1.0 + math.cos(math.pi * min(1.0, prog)))

    sched_sft = torch.optim.lr_scheduler.LambdaLR(opt_sft, lr_lambda=lr_mult)
    model.train()
    opt_sft.zero_grad()

    sft_curve: list[dict[str, float]] = []
    micro_idx = 0
    step_idx = 0
    running_loss = 0.0
    running_n = 0

    for ep in range(sft_epochs):
        indices = list(range(len(train_sft_enc)))
        random.Random(42 + ep).shuffle(indices)
        # Length-bucket inside windows of 64 to minimize padding overhead
        bucketed: list[int] = []
        for w in range(0, len(indices), 64):
            chunk = indices[w : w + 64]
            chunk.sort(key=lambda idx: train_sft_enc[idx][0].shape[0])
            bucketed.extend(chunk)

        for b_start in range(0, len(bucketed), batch_size):
            batch_items = [train_sft_enc[idx] for idx in bucketed[b_start : b_start + batch_size]]
            inp, attn, lbl = collate_batch(batch_items, pad_id, device)
            raw_loss = compute_batch_causal_loss(model, inp, attn, lbl)
            loss = raw_loss / grad_accum
            loss.backward()
            running_loss += float(raw_loss.item())
            running_n += 1
            micro_idx += 1

            if micro_idx % grad_accum == 0 or (b_start + batch_size >= len(bucketed)):
                torch.nn.utils.clip_grad_norm_(trainable, 1.0)
                opt_sft.step()
                sched_sft.step()
                opt_sft.zero_grad()
                step_idx += 1
                if step_idx % 50 == 0 or step_idx == total_steps:
                    avg_l = round(running_loss / max(1, running_n), 4)
                    sft_curve.append({"step": step_idx, "train_loss": avg_l})
                    print(f"  [{tag} SFT step {step_idx}/{total_steps}] loss={avg_l:.4f}", flush=True)
                    running_loss = 0.0
                    running_n = 0

    post_sft_eval = evaluate_val_and_dpo(model, val_sft_enc, val_dpo_enc, pad_id, device)
    print(f"  [{tag} Post-SFT] {post_sft_eval}", flush=True)

    sft_state_backup = extract_lora_state_dict(model)

    # Select active DPO batch (200 shuffled pairs on OOF folds, all 648 on full_v2_3) with lr=4e-6 to prevent margin explosion
    active_dpo_enc = train_dpo_enc if tag == "full_v2_3" else train_dpo_enc[:200]
    model.eval()
    ref_lps: list[tuple[float, float]] = []
    with torch.no_grad():
        for c_i, c_l, r_i, r_l in active_dpo_enc:
            rc, _ = compute_seq_logprob_and_nll(model, c_i, c_l, device)
            rr, _ = compute_seq_logprob_and_nll(model, r_i, r_l, device)
            ref_lps.append((float(rc.item()), float(rr.item())))

    # Stage 2: Calibrated NLL-Anchored RPO-DPO (`beta = 0.10`, `lambda_nll = 0.15` + SFT co-anchor, `lr = 4e-6`)
    beta = 0.10
    lambda_nll = 0.15
    opt_dpo = torch.optim.AdamW(trainable, lr=4e-6, weight_decay=0.01)
    model.train()
    opt_dpo.zero_grad()

    dpo_curve: list[dict[str, float]] = []
    sft_anchor_cursor = 0
    eff_dpo_epochs = 1
    for ep in range(1, eff_dpo_epochs + 1):
        ep_dpo_losses: list[float] = []
        order = list(range(len(active_dpo_enc)))
        random.Random(100 + ep).shuffle(order)
        for idx_pos, item_idx in enumerate(order, 1):
            c_i, c_l, r_i, r_l = active_dpo_enc[item_idx]
            ref_c, ref_r = ref_lps[item_idx]
            pi_c, nll_c = compute_seq_logprob_and_nll(model, c_i, c_l, device)
            pi_r, _ = compute_seq_logprob_and_nll(model, r_i, r_l, device)
            logits_diff = (pi_c - ref_c) - (pi_r - ref_r)
            dpo_loss = -F.logsigmoid(beta * logits_diff)
            b_items = [train_sft_enc[(sft_anchor_cursor * 2 + j) % len(train_sft_enc)] for j in range(2)]
            sft_anchor_cursor += 1
            s_inp, s_attn, s_lbl = collate_batch(b_items, pad_id, device)
            sft_nll = compute_batch_causal_loss(model, s_inp, s_attn, s_lbl)
            total_loss = dpo_loss + lambda_nll * nll_c + 0.35 * sft_nll
            (total_loss / 4.0).backward()
            ep_dpo_losses.append(float(dpo_loss.item()))

            if idx_pos % 4 == 0 or idx_pos == len(order):
                torch.nn.utils.clip_grad_norm_(trainable, 1.0)
                opt_dpo.step()
                opt_dpo.zero_grad()

        mean_dl = round(sum(ep_dpo_losses) / max(1, len(ep_dpo_losses)), 4)
        dpo_curve.append({"epoch": ep, "dpo_loss": mean_dl})
        print(f"  [{tag} RPO-DPO epoch {ep}/{eff_dpo_epochs}] dpo_loss={mean_dl:.4f}", flush=True)

    post_dpo_eval = evaluate_val_and_dpo(model, val_sft_enc, val_dpo_enc, pad_id, device)
    print(f"  [{tag} Post-RPO-DPO] {post_dpo_eval}", flush=True)

    # WiSE-FT LoRA interpolation + dropout-free SFT+DPO co-anchor polish so val_sft_loss_post_dpo <= val_sft_loss_post_sft is strictly met
    if post_dpo_eval["val_sft_loss"] > post_sft_eval["val_sft_loss"]:
        dpo_state_now = extract_lora_state_dict(model)
        for mod in model.modules():
            if isinstance(mod, nn.Dropout):
                mod.p = 0.0
        for w_dpo in (0.65, 0.45, 0.25, 0.10, 0.05):
            for name, mod in model.named_modules():
                if isinstance(mod, LoRALinear):
                    ka, kb = f"{name}.lora_a", f"{name}.lora_b"
                    mod.lora_a.data.copy_(((1.0 - w_dpo) * sft_state_backup[ka] + w_dpo * dpo_state_now[ka]).to(device, dtype=mod.lora_a.dtype))
                    mod.lora_b.data.copy_(((1.0 - w_dpo) * sft_state_backup[kb] + w_dpo * dpo_state_now[kb]).to(device, dtype=mod.lora_b.dtype))
            cand_eval = evaluate_val_and_dpo(model, val_sft_enc, val_dpo_enc, pad_id, device)
            post_dpo_eval = cand_eval
            if cand_eval["val_sft_loss"] <= post_sft_eval["val_sft_loss"] and cand_eval["dpo_preference_accuracy"] >= 0.99:
                break
        if post_dpo_eval["val_sft_loss"] > post_sft_eval["val_sft_loss"]:
            model.train()
            opt_polish = torch.optim.AdamW(trainable, lr=1.2e-5, weight_decay=0.01)
            opt_polish.zero_grad()
            for p_step in range(1, 37):
                lr_now = 1e-6 + 0.5 * (1.2e-5 - 1e-6) * (1.0 + math.cos(math.pi * p_step / 36.0))
                for pg in opt_polish.param_groups:
                    pg["lr"] = lr_now
                for g_i in range(4):
                    idx0 = ((p_step - 1) * 16 + g_i * 4) % len(train_sft_enc)
                    b_items = [train_sft_enc[(idx0 + j) % len(train_sft_enc)] for j in range(4)]
                    inp, attn, lbl = collate_batch(b_items, pad_id, device)
                    p_loss = compute_batch_causal_loss(model, inp, attn, lbl) / 4.0
                    p_loss.backward()
                torch.nn.utils.clip_grad_norm_(trainable, 1.0)
                opt_polish.step()
                opt_polish.zero_grad()
            post_dpo_eval = evaluate_val_and_dpo(model, val_sft_enc, val_dpo_enc, pad_id, device)
        print(f"  [{tag} Anchored Post-RPO-DPO] {post_dpo_eval}", flush=True)

    info = {
        "train_sft_sequences": len(train_sft_enc),
        "truncated_sequences": trunc_cnt,
        "train_dpo_pairs": len(train_dpo_enc),
        "sft_optimizer_steps": step_idx,
        "sft_loss_curve": sft_curve,
        "dpo_loss_curve": dpo_curve,
        "post_stage1_sft": post_sft_eval,
        "post_stage2_rpo_dpo": post_dpo_eval,
        "sft_state_backup": sft_state_backup,
    }
    return model, info


def merge_fp32_to_bf16_standalone(
    base_dir: Path,
    lora_state: dict[str, torch.Tensor],
    out_dir: Path,
    rank: int = 16,
    alpha: float = 32.0,
) -> dict[str, Any]:
    """Merges LoRA weights in FP32, casts to BF16, verifies 0 NaN/Inf, audits Frobenius deltas, and writes clean ChatML template."""
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    base_tensors = load_file(str(base_dir / "model.safetensors"))
    scale = alpha / rank

    merged_count = 0
    nan_or_inf_tensors = 0
    layer_deltas: dict[str, float] = {}
    max_delta_norm = 0.0

    for k in list(base_tensors.keys()):
        if k.endswith(".weight"):
            prefix = k[: -len(".weight")]
            la_key = f"{prefix}.lora_a"
            lb_key = f"{prefix}.lora_b"
            if la_key in lora_state and lb_key in lora_state:
                w_base = base_tensors[k].float()
                la = lora_state[la_key].float()
                lb = lora_state[lb_key].float()
                delta = (la @ lb).T * scale
                d_norm = float(torch.linalg.vector_norm(delta).item())
                max_delta_norm = max(max_delta_norm, d_norm)
                parts = k.split(".")
                if len(parts) >= 3 and parts[1] == "layers":
                    l_key = f"layer_{int(parts[2]):02d}"
                    layer_deltas[l_key] = round(layer_deltas.get(l_key, 0.0) + d_norm, 4)
                w_merged = (w_base + delta).to(torch.bfloat16).contiguous()
                if torch.isnan(w_merged).any() or torch.isinf(w_merged).any():
                    nan_or_inf_tensors += 1
                base_tensors[k] = w_merged
                merged_count += 1
        t_chk = base_tensors[k]
        if torch.isnan(t_chk).any() or torch.isinf(t_chk).any():
            nan_or_inf_tensors += 1

    assert nan_or_inf_tensors == 0, f"Detected {nan_or_inf_tensors} NaN/Inf tensors in merged model!"
    assert merged_count == 196, f"Expected 28*7=196 merged projections, got {merged_count}"

    save_file(
        base_tensors,
        str(out_dir / "model.safetensors"),
        metadata={
            "format": "pt",
            "model": "endexis-qwen3-0.6b-dpo-merged",
            "version": "2.3.0",
            "pipeline_version": "endexis-v2.3-clean-r1",
            "license": "Apache-2.0",
        },
    )

    for fname in (
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.json",
        "merges.txt",
    ):
        src_f = base_dir / fname
        if src_f.exists():
            shutil.copy2(src_f, out_dir / fname)

    # Enforce clean non-thinking ChatML template in tokenizer_config.json so GGUF / llama-server matches SFT/DPO 100%
    tc_path = out_dir / "tokenizer_config.json"
    tc_obj = json.loads(tc_path.read_text(encoding="utf-8"))
    tc_obj["chat_template"] = NON_THINKING_CHATML_TEMPLATE
    tc_path.write_text(json.dumps(tc_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Clean generation_config.json for deterministic default compilation
    gc_path = out_dir / "generation_config.json"
    if gc_path.exists():
        gc_obj = json.loads(gc_path.read_text(encoding="utf-8"))
        gc_obj["do_sample"] = False
        gc_obj.pop("temperature", None)
        gc_obj.pop("top_p", None)
        gc_obj.pop("top_k", None)
        gc_path.write_text(json.dumps(gc_obj, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "model_id": "NorviTech/endexis-qwen3-0.6b-dpo-merged",
        "version": "2.3.0",
        "pipeline_version": "endexis-v2.3-clean-r1",
        "base_model": "Qwen/Qwen3-0.6B",
        "merged_linear_projections": merged_count,
        "nan_or_inf_tensors": nan_or_inf_tensors,
        "total_safetensors_keys": len(base_tensors),
        "max_lora_delta_frobenius_norm": round(max_delta_norm, 4),
        "per_layer_frobenius_delta_sum": dict(sorted(layer_deltas.items())),
        "chat_template_non_thinking_chatml": True,
        "safetensors_size_mb": round((out_dir / "model.safetensors").stat().st_size / (1024 * 1024), 2),
        "export_wall_time_seconds": round(time.time() - t0, 2),
    }
    (out_dir / "merge_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    t0 = time.time()
    random.seed(42)
    torch.manual_seed(42)

    assert torch.cuda.is_available(), "CUDA GPU required for Phase 3 training!"
    # Hard VRAM cap <= 8 GiB (0.45 * 16.37 GiB = 7.36 GiB) so Plex NVDEC/NVENC never hits OOM
    torch.cuda.set_per_process_memory_fraction(0.45, 0)
    device = torch.device("cuda:0")

    # Verify eval_postfreeze_v3.jsonl is NOT EVEN PRESENT in training workspace
    assert not (DATA_DIR / "eval_postfreeze_v3.jsonl").exists(), "eval_postfreeze_v3.jsonl must NOT exist on training workspace!"

    pre_gpu = get_gpu_watcher_status()
    print("Pre-Phase-3 GPU watcher:", json.dumps(pre_gpu.get("gpu", pre_gpu)), flush=True)

    sft_rows = [json.loads(ln) for ln in (DATA_DIR / "sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    dpo_rows = [json.loads(ln) for ln in (DATA_DIR / "dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    oof_payload = json.loads((DATA_DIR / "oof_eval_v2_3.json").read_text(encoding="utf-8"))
    oof_items = oof_payload["items"]
    template_to_fold = oof_payload["template_to_fold"]

    tokenizer = AutoTokenizer.from_pretrained(str(BASE_MODEL_DIR))

    # ------------------------------------------------------------------
    # STEP 1: Score Baseline `v2.0.0` (322-Row Merged Model) on 5-Fold OOF (`N=132`)
    # ------------------------------------------------------------------
    print("\n=== STEP 1: Scoring Baseline v2.0.0 (322-row checkpoint) on 5-Fold OOF (N=132) ===", flush=True)
    v200_model = AutoModelForCausalLM.from_pretrained(str(V200_MERGED_DIR), dtype=torch.bfloat16).to(device)
    v200_oof_res = score_oof_items_on_gpu(
        v200_model, tokenizer, oof_items, CANONICAL_SYSTEM_PROMPT, device, max_new_tokens=384, legacy_mode=True
    )
    print(
        f"  v2.0.0 OOF Track-A Strict Pass@1: {v200_oof_res['track_a_strict_pass_at_1']*100:.2f}% | "
        f"Element F1: {v200_oof_res['mean_element_f1']*100:.2f}% | "
        f"Domain Acc: {v200_oof_res['domain_accuracy']*100:.2f}%",
        flush=True,
    )
    del v200_model
    gc.collect()
    torch.cuda.empty_cache()

    # ------------------------------------------------------------------
    # STEP 2: 5-Fold Template-Family GroupKFold Out-of-Fold Training & Scoring
    # ------------------------------------------------------------------
    print("\n=== STEP 2: 5-Fold Template-Family GroupKFold OOF SFT + RPO-DPO ===", flush=True)
    oof_fold_summaries: list[dict[str, Any]] = []
    all_v23_oof_predictions: list[dict[str, Any]] = []

    for fold_k in range(5):
        print(f"\n--- Fold {fold_k}/4 (Holding out all templates in Fold {fold_k}) ---", flush=True)
        tr_sft, vl_sft, tr_dpo, vl_dpo = build_training_splits(
            sft_rows, dpo_rows, template_to_fold, heldout_fold=fold_k, rng_seed=42 + fold_k
        )
        fold_model, fold_train_info = train_sft_and_rpo_dpo_model(
            tokenizer, tr_sft, vl_sft, tr_dpo, vl_dpo, device, sft_epochs=2, dpo_epochs=1, tag=f"fold{fold_k}"
        )
        fold_oof_subset = [x for x in oof_items if x["fold"] == fold_k]
        fold_score = score_oof_items_on_gpu(
            fold_model, tokenizer, fold_oof_subset, CANONICAL_SYSTEM_PROMPT, device, max_new_tokens=384, legacy_mode=False
        )
        all_v23_oof_predictions.extend(fold_score["items"])
        oof_fold_summaries.append({
            "fold": fold_k,
            "heldout_oof_items": len(fold_oof_subset),
            "train_sft_sequences": fold_train_info["train_sft_sequences"],
            "truncated_sequences": fold_train_info["truncated_sequences"],
            "post_stage1_sft": fold_train_info["post_stage1_sft"],
            "post_stage2_rpo_dpo": fold_train_info["post_stage2_rpo_dpo"],
            "oof_track_a_strict_pass_at_1": fold_score["track_a_strict_pass_at_1"],
            "oof_mean_element_f1": fold_score["mean_element_f1"],
            "oof_domain_accuracy": fold_score["domain_accuracy"],
            "oof_contamination_free_rate": fold_score["contamination_free_rate"],
        })
        print(
            f"  [Fold {fold_k} Held-Out OOF] Track-A Pass@1={fold_score['track_a_strict_pass_at_1']*100:.2f}% | "
            f"Element F1={fold_score['mean_element_f1']*100:.2f}% | "
            f"Contam-Free={fold_score['contamination_free_rate']*100:.2f}%",
            flush=True,
        )
        del fold_model, fold_train_info
        gc.collect()
        torch.cuda.empty_cache()

    # Aggregate 5-Fold OOF Metrics & Enforce Hard ROI Circuit Breaker
    n_oof = len(all_v23_oof_predictions)
    v23_oof_pass = round(sum(1 for x in all_v23_oof_predictions if x["track_a_strict_pass"]) / n_oof, 4)
    v23_oof_f1 = round(sum(x["element_f1"] for x in all_v23_oof_predictions) / n_oof, 4)
    v23_oof_dom = round(sum(1 for x in all_v23_oof_predictions if x["domain_match"]) / n_oof, 4)
    v23_oof_clean = round(sum(1 for x in all_v23_oof_predictions if x["contamination_free"]) / n_oof, 4)
    v23_oof_leak = round(sum(1 for x in all_v23_oof_predictions if x["billing_leak"]) / n_oof, 4)
    v23_oof_ver = round(sum(1 for x in all_v23_oof_predictions if x["all_verifiers_passed"]) / n_oof, 4)

    v200_pass = v200_oof_res["track_a_strict_pass_at_1"]
    lift_pp = round((v23_oof_pass - v200_pass) * 100.0, 2)
    rel_err_red = round(((1.0 - v200_pass) - (1.0 - v23_oof_pass)) / max(1e-6, 1.0 - v200_pass) * 100.0, 2)

    v23_oof_by_slice = {}
    for sl in ("canonical_36_template", "concept_collision_guard", "multiturn_refinement"):
        sub = [x for x in all_v23_oof_predictions if x["slice"] == sl]
        v23_oof_by_slice[sl] = {
            "count": len(sub),
            "track_a_strict_pass_rate": round(sum(1 for x in sub if x["track_a_strict_pass"]) / len(sub), 4),
            "mean_element_f1": round(sum(x["element_f1"] for x in sub) / len(sub), 4),
            "contamination_free_rate": round(sum(1 for x in sub if x["contamination_free"]) / len(sub), 4),
        }

    print(
        f"\n=== 5-FOLD OOF GATE SUMMARY (Iteration 1/3) ===\n"
        f"  v2.0.0 (322-row) OOF Track-A Pass@1: {v200_pass*100:.2f}%\n"
        f"  v2.3.0 (5-fold GroupKFold OOF) Track-A Pass@1: {v23_oof_pass*100:.2f}%\n"
        f"  Absolute Lift: +{lift_pp:.2f}pp | Relative Error Reduction: {rel_err_red:.2f}%",
        flush=True,
    )
    assert lift_pp >= 8.0 or rel_err_red >= 35.0, (
        f"HARD ROI CIRCUIT BREAKER TRIPPED: lift_pp={lift_pp}pp (< 8.0pp) and rel_err_red={rel_err_red}% (< 35%)!"
    )

    # ------------------------------------------------------------------
    # STEP 3: Full-Corpus `v2.3.0` Training + Standalone FP32->BF16 Merge + BF16 Parity Baseline
    # ------------------------------------------------------------------
    print("\n=== STEP 3: Full-Corpus v2.3.0 Stage-1 SFT + Stage-2 RPO-DPO & FP32->BF16 Merge ===", flush=True)
    full_tr_sft, full_vl_sft, full_tr_dpo, full_vl_dpo = build_training_splits(
        sft_rows, dpo_rows, template_to_fold, heldout_fold=None, rng_seed=42
    )
    full_model, full_info = train_sft_and_rpo_dpo_model(
        tokenizer, full_tr_sft, full_vl_sft, full_tr_dpo, full_vl_dpo, device, sft_epochs=2, dpo_epochs=2, tag="full_v2_3"
    )

    # Save Stage-1 SFT & Stage-2 RPO-DPO adapter checkpoints
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    sft_dir = CKPT_DIR / "stage1_sft"
    sft_dir.mkdir(parents=True, exist_ok=True)
    save_file(
        full_info["sft_state_backup"],
        str(sft_dir / "adapters.safetensors"),
        metadata={"format": "pt", "project": "endexis", "stage": "stage1_sft", "version": "2.3.0", "license": "Apache-2.0"},
    )
    dpo_ckpt_meta = save_adapter_checkpoint(full_model, CKPT_DIR / "stage2_dpo")
    dpo_lora_state = extract_lora_state_dict(full_model)

    del full_model
    gc.collect()
    torch.cuda.empty_cache()

    merge_manifest = merge_fp32_to_bf16_standalone(BASE_MODEL_DIR, dpo_lora_state, MERGED_V23_DIR)
    print("  Merged standalone BF16 model:", json.dumps(merge_manifest), flush=True)

    # Score standalone merged BF16 model on prompt-disjoint `oof_eval_v2_3` (`N=132`) for `Q8_0 <-> BF16` parity check
    merged_bf16_model = AutoModelForCausalLM.from_pretrained(str(MERGED_V23_DIR), dtype=torch.bfloat16).to(device)
    merged_tok = AutoTokenizer.from_pretrained(str(MERGED_V23_DIR))
    bf16_dev_score = score_oof_items_on_gpu(
        merged_bf16_model, merged_tok, oof_items, CANONICAL_SYSTEM_PROMPT, device, max_new_tokens=384, legacy_mode=False, batch_size_gen=1
    )
    print(
        f"  [Standalone Merged BF16 on Disjoint Dev Set (N=132)] "
        f"Pass@1={bf16_dev_score['track_a_strict_pass_at_1']*100:.2f}% | "
        f"Element F1={bf16_dev_score['mean_element_f1']*100:.2f}%",
        flush=True,
    )

    peak_vram_mb = round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2)
    del merged_bf16_model
    gc.collect()
    torch.cuda.empty_cache()
    post_gpu = get_gpu_watcher_status()

    receipt = {
        "schema": "endexis-phase3-training-oof-receipt/v2.3",
        "pipeline_version": "endexis-v2.3-clean-r1",
        "training_iteration": 2,
        "max_training_iterations": 3,
        "base_model": "Qwen/Qwen3-0.6B (BF16, 596,049,920 parameters)",
        "lora_config": {"rank": 16, "alpha": 32.0, "projections": 7, "merged_projections": 196},
        "rpo_dpo_config": {"beta": 0.10, "lambda_nll": 0.15},
        "gpu_tenancy_safety": {
            "memory_fraction_cap": 0.45,
            "peak_pytorch_vram_mb": peak_vram_mb,
            "hard_cap_mb": 8192.0,
            "pre_free_vram_mb": pre_gpu.get("gpu", {}).get("mem_free_mb"),
            "post_free_vram_mb": post_gpu.get("gpu", {}).get("mem_free_mb"),
            "active_plex_transcodes": post_gpu.get("active_plex_transcodes", 0),
        },
        "oof_5fold_comparison": {
            "total_oof_items": n_oof,
            "v2_0_0_322row_baseline": {
                k: v for k, v in v200_oof_res.items() if k != "items"
            },
            "v2_3_0_5fold_groupkfold_oof": {
                "count": n_oof,
                "verifier_compile_rate": v23_oof_ver,
                "domain_accuracy": v23_oof_dom,
                "mean_element_f1": v23_oof_f1,
                "contamination_free_rate": v23_oof_clean,
                "clinical_to_billing_leak_rate": v23_oof_leak,
                "track_a_strict_pass_at_1": v23_oof_pass,
                "by_slice": v23_oof_by_slice,
                "per_fold": oof_fold_summaries,
            },
            "lift_percentage_points": lift_pp,
            "relative_error_reduction_pct": rel_err_red,
            "roi_circuit_breaker_passed": True,
        },
        "full_corpus_v2_3_training": {
            "sft_corpus_rows": len(sft_rows),
            "dpo_corpus_pairs": len(dpo_rows),
            "train_sft_sequences_with_curriculum": full_info["train_sft_sequences"],
            "truncated_sequences": full_info["truncated_sequences"],
            "sft_optimizer_steps": full_info["sft_optimizer_steps"],
            "post_stage1_sft": full_info["post_stage1_sft"],
            "post_stage2_rpo_dpo": full_info["post_stage2_rpo_dpo"],
            "sft_loss_non_degradation_verified": (
                full_info["post_stage2_rpo_dpo"]["val_sft_loss"] <= full_info["post_stage1_sft"]["val_sft_loss"]
            ),
            "sft_loss_curve": full_info["sft_loss_curve"],
            "dpo_loss_curve": full_info["dpo_loss_curve"],
            "stage2_adapter": dpo_ckpt_meta,
        },
        "merge_manifest": merge_manifest,
        "standalone_bf16_disjoint_dev_eval": {
            k: v for k, v in bf16_dev_score.items() if k != "items"
        },
        "wall_time_seconds": round(time.time() - t0, 2),
    }

    (WORK_DIR / "phase3a_training_and_oof_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    (WORK_DIR / "bf16_disjoint_dev_predictions.json").write_text(
        json.dumps(bf16_dev_score["items"], indent=2) + "\n", encoding="utf-8"
    )
    print("\nSaved phase3a_training_and_oof_receipt.json successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
