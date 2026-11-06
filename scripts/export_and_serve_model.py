#!/usr/bin/env python3
"""
Phase 5 — Merge Stage-2 RPO-DPO LoRA Adapter into Standalone BF16 Safetensors Model
(`endexis-qwen3-0.6b-dpo-merged`) and Serve/Verify Live Neural Endpoint on CUDA GPU (`:8945`).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import os
ARRMES_SITE = os.environ.get("ENDEXIS_EXTRA_SITE_PACKAGES", "")
if ARRMES_SITE and Path(ARRMES_SITE).exists() and ARRMES_SITE not in sys.path:
    sys.path.append(ARRMES_SITE)

import torch
from safetensors.torch import load_file, save_file
from transformers import AutoModelForCausalLM, AutoTokenizer

WORK_DIR = Path(os.environ.get("ENDEXIS_WORK_DIR", str(Path(__file__).resolve().parent.parent)))
BASE_MODEL_DIR = WORK_DIR / "base-qwen3-0.6b-bf16"
DPO_ADAPTER_PATH = WORK_DIR / "checkpoints" / "stage2_dpo" / "adapters.safetensors"
MERGED_MODEL_DIR = WORK_DIR / "endexis-qwen3-0.6b-dpo-merged"


def get_gpu_watcher_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8931/status", timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def merge_and_export_model(rank: int = 16, alpha: float = 32.0) -> dict[str, Any]:
    """Fuses W_merged = W_base + (alpha / rank) * (lora_a @ lora_b).T into standalone BF16 model.safetensors."""
    t0 = time.time()
    MERGED_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    base_tensors = load_file(str(BASE_MODEL_DIR / "model.safetensors"))
    lora_tensors = load_file(str(DPO_ADAPTER_PATH))
    scale = alpha / rank

    merged_count = 0
    max_delta_norm = 0.0
    for k in list(base_tensors.keys()):
        if k.endswith(".weight"):
            prefix = k[: -len(".weight")]
            la_key = f"{prefix}.lora_a"
            lb_key = f"{prefix}.lora_b"
            if la_key in lora_tensors and lb_key in lora_tensors:
                w_base = base_tensors[k].float()
                la = lora_tensors[la_key].float()  # (in_features, rank)
                lb = lora_tensors[lb_key].float()  # (rank, out_features)
                # Linear weight in PyTorch is (out_features, in_features)
                delta = (la @ lb).T * scale
                d_norm = float(torch.linalg.vector_norm(delta).item())
                max_delta_norm = max(max_delta_norm, d_norm)
                base_tensors[k] = (w_base + delta).to(torch.bfloat16).contiguous()
                merged_count += 1

    save_file(
        base_tensors,
        str(MERGED_MODEL_DIR / "model.safetensors"),
        metadata={"format": "pt", "model": "endexis-qwen3-0.6b-dpo-merged", "license": "Apache-2.0"},
    )
    for fname in ("config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "vocab.json", "merges.txt"):
        src_f = BASE_MODEL_DIR / fname
        if src_f.exists():
            shutil.copy2(src_f, MERGED_MODEL_DIR / fname)

    manifest = {
        "model_id": "endexis-qwen3-0.6b-dpo-merged",
        "base_model": "Qwen/Qwen3-0.6B",
        "merged_linear_projections": merged_count,
        "total_safetensors_keys": len(base_tensors),
        "max_lora_delta_frobenius_norm": round(max_delta_norm, 4),
        "merged_model_dir": "endexis-qwen3-0.6b-dpo-merged",
        "safetensors_size_mb": round((MERGED_MODEL_DIR / "model.safetensors").stat().st_size / (1024 * 1024), 2),
        "export_wall_time_seconds": round(time.time() - t0, 2),
    }
    (MERGED_MODEL_DIR / "merge_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


class EndexisNeuralRuntime:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self.model = AutoModelForCausalLM.from_pretrained(str(model_dir), dtype=torch.bfloat16).cuda()
        self.model.eval()

    def generate_candidates(self, prompt: str, n: int = 4, max_new_tokens: int = 220, temperature: float = 0.25) -> dict[str, Any]:
        t0 = time.time()
        chat_prompt = (
            "<|im_start|>system\nYou are Endexis (endexis), the Cerner Millennium V500, "
            "Discern Analytics 2.0 (DA2), CCL, and BIRT 4.4.2 neural compiler.<|im_end|>\n"
            f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n<reasoning>\n"
        )
        inp = self.tokenizer(chat_prompt, return_tensors="pt").input_ids.cuda()
        with torch.no_grad():
            gen = self.model.generate(
                inp,
                max_new_tokens=max_new_tokens,
                do_sample=(n > 1 and temperature > 0.0),
                temperature=max(temperature, 1e-3),
                top_p=0.9,
                num_return_sequences=max(1, n),
                pad_token_id=self.tokenizer.eos_token_id,
            )
        raw_completions = [
            "<reasoning>\n" + self.tokenizer.decode(g[inp.shape[1]:], skip_special_tokens=True).strip()
            for g in gen
        ]
        ranked = sorted(
            raw_completions,
            key=lambda txt: sum(
                1
                for kw in ("Tenancy", "Grain", "Business View", "Element", "Code Set", "Guardrails", "<output>")
                if kw in txt
            ),
            reverse=True,
        )
        return {
            "model": "endexis-qwen3-0.6b-dpo-merged",
            "n_candidates": len(ranked),
            "best_completion": ranked[0],
            "all_candidates": ranked,
            "latency_ms": round((time.time() - t0) * 1000.0, 2),
            "vram_allocated_mb": round(torch.cuda.memory_allocated() / (1024 * 1024), 2),
        }


def run_server(host: str = "0.0.0.0", port: int = 8945) -> None:
    runtime = EndexisNeuralRuntime(MERGED_MODEL_DIR)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path.startswith("/health") or self.path.startswith("/status"):
                watcher = get_gpu_watcher_status()
                body = json.dumps({
                    "status": "ok",
                    "service": "endexis-neural",
                    "model": "endexis-qwen3-0.6b-dpo-merged",
                    "vram_allocated_mb": round(torch.cuda.memory_allocated() / (1024 * 1024), 2),
                    "gpu_watcher": watcher.get("gpu", {}),
                    "active_plex_transcodes": watcher.get("active_plex_transcodes", 0),
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if self.path.startswith("/v1/compile") or self.path.startswith("/v1/generate"):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                prompt = str(payload.get("prompt") or payload.get("request") or "")
                n = int(payload.get("n", 4))
                max_new_tokens = int(payload.get("max_new_tokens", 220))
                res = runtime.generate_candidates(prompt=prompt, n=n, max_new_tokens=max_new_tokens)
                body = json.dumps(res).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"endexis-neural listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="Merge Stage-2 DPO LoRA weights into standalone BF16 model")
    ap.add_argument("--serve", action="store_true", help="Run HTTP inference server on :8945")
    ap.add_argument("--port", type=int, default=8945)
    args = ap.parse_args()

    if args.merge or not (MERGED_MODEL_DIR / "model.safetensors").exists():
        manifest = merge_and_export_model()
        print("Merged model manifest:", json.dumps(manifest, indent=2), flush=True)
    if args.serve:
        run_server(port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
