#!/usr/bin/env python3
"""
Phase 3B — GGUF Conversion, Quantization (`Q8_0` & `Q4_K_M`), `Q8_0 <-> BF16` Structural Parity Gate (`>= 98%`),
and Isolated `llama-server` Latency Benchmark (`<= 350 ms` single-pass CUDA `Q4_K_M` p50 vs `2,352.61 ms` legacy PyTorch HTTP).

Hard Invariants Enforced:
1. PyTorch is NOT loaded in this script — zero dual-framework VRAM contention.
2. Uses `try / finally` around every temporary `llama-server` process (`127.0.0.1:18945`) so zero stray processes remain.
3. Never touches `eval_postfreeze_v3.jsonl` (scores ONLY `oof_eval_v2_3.json`).
4. Does NOT overwrite live `production GGUF model directory` weights — stages cleanly inside
   `WORK_DIR / "gguf" / "endexis-qwen3-0.6b-dpo-v2_3-{q8_0,q4_k_m}.gguf"`.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

WORK_DIR = Path(os.environ.get("ENDEXIS_WORK_DIR", str(Path(__file__).resolve().parent.parent)))
REPO_SRC = WORK_DIR / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from discern_intelligence.neural_compiler import CANONICAL_SYSTEM_PROMPT, LocalNeuralCompiler

MERGED_V23_DIR = WORK_DIR / "endexis-qwen3-0.6b-dpo-v2_3-merged"
GGUF_OUT_DIR = WORK_DIR / "gguf"
DATA_DIR = WORK_DIR / "data"

CONVERT_SCRIPT = Path(
    os.environ.get(
        "LLAMA_CONVERT_SCRIPT",
        str(WORK_DIR / "llama.cpp/convert_hf_to_gguf.py"),
    )
)
QUANTIZE_BIN = Path(
    os.environ.get(
        "LLAMA_QUANTIZE_BIN",
        str(WORK_DIR / "llama.cpp/build/bin/llama-quantize"),
    )
)
SERVER_CUDA_BIN = Path(
    os.environ.get(
        "LLAMA_SERVER_CUDA_BIN",
        str(WORK_DIR / "gpu-bin/llama-server"),
    )
)
SERVER_CPU_BIN = Path(
    os.environ.get(
        "LLAMA_SERVER_CPU_BIN",
        str(WORK_DIR / "llama.cpp/build/bin/llama-server"),
    )
)
V200_Q4_GGUF = Path(
    os.environ.get(
        "ENDEXIS_V200_Q4_GGUF",
        str(WORK_DIR / "gguf/endexis-qwen3-0.6b-dpo-q4_k_m.gguf"),
    )
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = (len(s) - 1) * (pct / 100.0)
    lo = int(idx)
    hi = min(len(s) - 1, lo + 1)
    frac = idx - lo
    return round(s[lo] * (1.0 - frac) + s[hi] * frac, 2)


def wait_until_gpu_uncontended(max_wait_s: float = 900.0) -> None:
    deadline = time.time() + max_wait_s
    quiet_streak = 0
    while time.time() < deadline:
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                text=True,
                timeout=4.0,
            ).strip()
            util = int(out.splitlines()[0].strip())
            if util <= 5:
                quiet_streak += 1
                if quiet_streak >= 3:
                    return
            else:
                quiet_streak = 0
        except Exception:
            return
        time.sleep(1.5)


def wait_server_ready(port: int, timeout_s: float = 35.0) -> None:
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"llama-server on port {port} did not become healthy within {timeout_s}s")


def run_server_benchmark(
    server_bin: Path,
    gguf_path: Path,
    ngl: int,
    oof_items: list[dict[str, Any]],
    port: int = 18945,
    eval_n4_subset: int = 32,
) -> dict[str, Any]:
    docker_img = os.environ.get("LLAMA_CUDA_DOCKER_IMAGE", "")
    cname = f"endexis-gguf-bench-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    docker_ep = os.environ.get("LLAMA_DOCKER_ENTRYPOINT", "")
    if ngl > 0:
        wait_until_gpu_uncontended()
    if ngl > 0 and docker_img:
        ep_args = ["--entrypoint", docker_ep] if docker_ep else []
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            cname,
            "--network",
            "host",
            "--gpus",
            "device=0",
            "--cpus",
            "8",
            "--memory",
            "4g",
            *ep_args,
            "-v",
            f"{gguf_path.resolve()}:/models/model.gguf:ro",
            docker_img,
            "--model",
            "/models/model.gguf",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ngl",
            str(ngl),
            "-c",
            "2048",
            "-np",
            "1",
            "-fa",
            "on",
            "-bs",
            "--samplers",
            "temperature",
            "--spec-type",
            "ngram-mod",
            "--spec-ngram-mod-n-max",
            "16",
            "-t",
            "2",
            "--no-webui",
            *os.environ.get("LLAMA_EXTRA_SERVER_ARGS", "").split(),
        ]
    else:
        cmd = [
            str(server_bin),
            "--model",
            str(gguf_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ngl",
            str(ngl),
            "-c",
            "2048",
            "-np",
            "1",
            "-t",
            "4" if ngl > 0 else "8",
        ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_server_ready(port)
        compiler = LocalNeuralCompiler(endpoint_url=f"http://127.0.0.1:{port}")
        # 4 warmup requests to bring GPU clocks from P8 idle (210 MHz) to P0 boost
        for w_item in oof_items[:4]:
            compiler.query_neural_endpoint(w_item["prompt"], n=1, max_new_tokens=256)

        # Query neural completions back-to-back first so offline SQLite harness rebuilds do not force GPU P8 sleep between requests
        raw_generations: list[tuple[dict[str, Any], str, float]] = []
        for item in oof_items:
            q_res = compiler.query_neural_endpoint(item["prompt"], n=1, max_new_tokens=384) or {}
            raw_generations.append(
                (item, q_res.get("best_completion") or "", float(q_res.get("latency_ms") or 0.0))
            )

        latencies_n1: list[float] = []
        per_item_n1: list[dict[str, Any]] = []
        for item, raw_comp, lat_ms in raw_generations:
            prompt = item["prompt"]
            gt = item["ground_truth_ir"]
            res = compiler.compile_track_a_raw_ir(prompt, completion_text=raw_comp, max_new_tokens=384)
            res["latency_ms"] = lat_ms
            latencies_n1.append(lat_ms)
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
            strict_pass = bool(res["parsed_valid_ir"] and res["all_verifiers_passed"] and dom_match and f1 >= 0.55)
            per_item_n1.append({
                "oof_id": item["oof_id"],
                "parsed_valid_ir": res["parsed_valid_ir"],
                "all_verifiers_passed": res["all_verifiers_passed"],
                "domain_match": dom_match,
                "pred_domain": pred_dom,
                "element_f1": round(f1, 4),
                "contamination_free": res["contamination_free"],
                "track_a_strict_pass": strict_pass,
                "pred_selected_elements": sorted(pred_elems),
                "latency_ms": res["latency_ms"],
            })

        latencies_n4: list[float] = []
        n4_verified_hits = 0
        if eval_n4_subset > 0:
            for item in oof_items[:eval_n4_subset]:
                t0 = time.perf_counter()
                b_res = compiler.compile_with_verifier_fallback(item["prompt"], n=4)
                dt4 = round((time.perf_counter() - t0) * 1000.0, 2)
                latencies_n4.append(dt4)
                if b_res["bundle"].verification.is_valid and b_res["ccl_program"].verification.is_valid:
                    n4_verified_hits += 1

        n_tot = max(1, len(per_item_n1))
        return {
            "gguf_file": gguf_path.name,
            "ngl": ngl,
            "count": len(per_item_n1),
            "track_a_strict_pass_at_1": round(sum(1 for x in per_item_n1 if x["track_a_strict_pass"]) / n_tot, 4),
            "verifier_compile_rate": round(sum(1 for x in per_item_n1 if x["all_verifiers_passed"]) / n_tot, 4),
            "domain_accuracy": round(sum(1 for x in per_item_n1 if x["domain_match"]) / n_tot, 4),
            "mean_element_f1": round(sum(x["element_f1"] for x in per_item_n1) / n_tot, 4),
            "contamination_free_rate": round(sum(1 for x in per_item_n1 if x["contamination_free"]) / n_tot, 4),
            "latency_n1_greedy_ms": {
                "p50": percentile(latencies_n1, 50),
                "p95": percentile(latencies_n1, 95),
                "mean": round(statistics.mean(latencies_n1), 2),
                "min": round(min(latencies_n1), 2),
                "max": round(max(latencies_n1), 2),
            },
            "latency_n4_verifier_ranked_ms": (
                {
                    "subset_count": len(latencies_n4),
                    "track_b_verified_pass_at_4": round(n4_verified_hits / max(1, len(latencies_n4)), 4),
                    "p50": percentile(latencies_n4, 50),
                    "p95": percentile(latencies_n4, 95),
                    "mean": round(statistics.mean(latencies_n4), 2),
                }
                if latencies_n4
                else {}
            ),
            "items": per_item_n1,
        }
    finally:
        proc.terminate()
        subprocess.run(["docker", "rm", "-f", cname, f"arrmes-cuda-test-{proc.pid}"], capture_output=True, check=False)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3.0)
        time.sleep(0.5)


def main() -> int:
    t0 = time.time()
    assert not (DATA_DIR / "eval_postfreeze_v3.jsonl").exists(), "eval_postfreeze_v3.jsonl must NOT exist in training workspace!"
    GGUF_OUT_DIR.mkdir(parents=True, exist_ok=True)

    bf16_gguf = GGUF_OUT_DIR / "endexis-qwen3-0.6b-dpo-v2_3-bf16.gguf"
    q8_gguf = GGUF_OUT_DIR / "endexis-qwen3-0.6b-dpo-v2_3-q8_0.gguf"
    q4_gguf = GGUF_OUT_DIR / "endexis-qwen3-0.6b-dpo-v2_3-q4_k_m.gguf"

    if os.environ.get("ENDEXIS_SKIP_QUANTIZE") == "1" and q8_gguf.exists() and q4_gguf.exists():
        print("1-2. Reusing freshly quantized Q8_0 and Q4_K_M GGUF artifacts...", flush=True)
    else:
        conv_py = os.environ.get("LLAMA_CONVERT_PYTHON", sys.executable)
        print("1. Converting standalone BF16 safetensors -> BF16 GGUF (zero F16 RMSNorm rounding)...", flush=True)
        subprocess.run(
            [conv_py, str(CONVERT_SCRIPT), str(MERGED_V23_DIR), "--outfile", str(bf16_gguf), "--outtype", "bf16"],
            check=True,
        )
        print("2. Quantizing BF16 GGUF -> SwiGLU-guarded Q8_0 and compact Q4_K_M...", flush=True)
        subprocess.run(
            [
                str(QUANTIZE_BIN),
                "--tensor-type",
                "ffn_down=bf16",
                "--tensor-type",
                "attn_output=bf16",
                str(bf16_gguf),
                str(q8_gguf),
                "Q8_0",
            ],
            check=True,
        )
        subprocess.run([str(QUANTIZE_BIN), str(bf16_gguf), str(q4_gguf), "Q4_K_M"], check=True)
        bf16_gguf.unlink(missing_ok=True)

    oof_items = json.loads((DATA_DIR / "oof_eval_v2_3.json").read_text(encoding="utf-8"))["items"]
    bf16_preds = {
        x["oof_id"]: x
        for x in json.loads((WORK_DIR / "bf16_disjoint_dev_predictions.json").read_text(encoding="utf-8"))
    }

    cuda_bin = SERVER_CUDA_BIN if SERVER_CUDA_BIN.exists() else SERVER_CPU_BIN
    cpu_bin = SERVER_CPU_BIN if SERVER_CPU_BIN.exists() else cuda_bin

    print("3. Benchmarking Baseline v2.0.0 Q4_K_M GGUF on CUDA...", flush=True)
    v200_q4_cuda = run_server_benchmark(cuda_bin, V200_Q4_GGUF, ngl=99, oof_items=oof_items, eval_n4_subset=0)

    print("4. Benchmarking v2.3.0 Q8_0 GGUF on CUDA + Checking Q8_0 <-> BF16 Parity...", flush=True)
    v23_q8_cuda = run_server_benchmark(cuda_bin, q8_gguf, ngl=99, oof_items=oof_items, eval_n4_subset=16)

    print("5. Benchmarking v2.3.0 Q4_K_M GGUF on CUDA...", flush=True)
    v23_q4_cuda = run_server_benchmark(cuda_bin, q4_gguf, ngl=99, oof_items=oof_items, eval_n4_subset=16)

    print("6. Benchmarking v2.3.0 Q4_K_M GGUF on CPU (32-item representative subset)...", flush=True)
    v23_q4_cpu = run_server_benchmark(cpu_bin, q4_gguf, ngl=0, oof_items=oof_items[:32], eval_n4_subset=0)

    # Compute Q8_0 <-> BF16 Structural Parity across all 132 disjoint items
    structural_matches = 0
    exact_element_jaccards: list[float] = []
    for q8_item in v23_q8_cuda["items"]:
        bf_item = bf16_preds[q8_item["oof_id"]]
        s_q8 = set(q8_item["pred_selected_elements"])
        s_bf = set(bf_item["pred_selected_elements"])
        jac = len(s_q8 & s_bf) / max(1, len(s_q8 | s_bf)) if (s_q8 or s_bf) else 1.0
        exact_element_jaccards.append(jac)
        if (
            q8_item["track_a_strict_pass"] == bf_item["track_a_strict_pass"]
            and q8_item["pred_domain"] == bf_item["pred_domain"]
            and jac >= 0.80
        ):
            structural_matches += 1

    q8_bf16_parity = round(structural_matches / len(oof_items), 4)
    mean_jaccard = round(statistics.mean(exact_element_jaccards), 4)
    q4_cuda_p50 = v23_q4_cuda["latency_n1_greedy_ms"]["p50"]

    print(
        f"\n=== PHASE 3B GGUF & LATENCY GATE SUMMARY ===\n"
        f"  Q8_0 <-> BF16 Structural Agreement: {q8_bf16_parity*100:.2f}% (Mean Element Jaccard: {mean_jaccard*100:.2f}%)\n"
        f"  v2.3.0 Q4_K_M CUDA Single-Pass p50: {q4_cuda_p50:.2f} ms (p95: {v23_q4_cuda['latency_n1_greedy_ms']['p95']:.2f} ms) vs 2352.61 ms Legacy PyTorch HTTP\n"
        f"  v2.3.0 Q8_0 CUDA Single-Pass p50:   {v23_q8_cuda['latency_n1_greedy_ms']['p50']:.2f} ms\n"
        f"  v2.3.0 Q4_K_M CPU Single-Pass p50:  {v23_q4_cpu['latency_n1_greedy_ms']['p50']:.2f} ms",
        flush=True,
    )
    receipt = {
        "schema": "endexis-phase3b-gguf-latency-parity-receipt/v2.3",
        "pipeline_version": "endexis-v2.3-clean-r1",
        "gguf_artifacts": {
            "q8_0": {
                "filename": q8_gguf.name,
                "size_mb": round(q8_gguf.stat().st_size / (1024 * 1024), 2),
                "sha256": sha256_file(q8_gguf),
            },
            "q4_k_m": {
                "filename": q4_gguf.name,
                "size_mb": round(q4_gguf.stat().st_size / (1024 * 1024), 2),
                "sha256": sha256_file(q4_gguf),
            },
        },
        "q8_0_vs_bf16_parity": {
            "disjoint_eval_count": len(oof_items),
            "structural_agreement_rate": q8_bf16_parity,
            "mean_element_set_jaccard": mean_jaccard,
            "gate_threshold": 0.98,
            "passed": bool(q8_bf16_parity >= 0.98),
        },
        "latency_and_accuracy_benchmarks": {
            "legacy_v2_0_0_pytorch_http_reference_mean_ms": 2352.61,
            "v2_0_0_q4_k_m_cuda": {k: v for k, v in v200_q4_cuda.items() if k != "items"},
            "v2_3_0_q8_0_cuda": {k: v for k, v in v23_q8_cuda.items() if k != "items"},
            "v2_3_0_q4_k_m_cuda": {k: v for k, v in v23_q4_cuda.items() if k != "items"},
            "v2_3_0_q4_k_m_cpu": {k: v for k, v in v23_q4_cpu.items() if k != "items"},
            "cuda_q4_p50_speedup_vs_legacy_pytorch_http": round(2352.61 / max(1.0, q4_cuda_p50), 2),
            "latency_gate_p50_le_350ms_passed": bool(q4_cuda_p50 <= 350.0),
        },
        "wall_time_seconds": round(time.time() - t0, 2),
    }
    (WORK_DIR / "phase3b_gguf_latency_and_parity_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print("\nSaved phase3b_gguf_latency_and_parity_receipt.json successfully.", flush=True)
    assert q8_bf16_parity >= 0.98, f"Q8_0 <-> BF16 structural parity {q8_bf16_parity} < 0.98!"
    assert q4_cuda_p50 <= 350.0, f"Q4_K_M CUDA p50 {q4_cuda_p50} ms > 350.0 ms target!"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
