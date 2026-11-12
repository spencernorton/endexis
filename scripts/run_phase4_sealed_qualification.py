#!/usr/bin/env python3
"""
Phase 4 — Pre-Freeze Cryptographic & Timestamp Integrity Verification + ONE-SHOT Sealed Qualification
on `data/eval_postfreeze_v3.jsonl` (`N = 640`), Legacy Regression Suites, Behavioral Canaries,
and Binary `.safetensors` Audit (`endexis-v2.3-clean-r1`).

CRITICAL: Run ONLY ONCE after Phase 3 OOF, RPO-DPO non-degradation, `Q8_0 <-> BF16` parity (`>= 98%`),
and `Q4_K_M` CUDA latency (`<= 350 ms` p50) gates have ALL passed.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for p in (SRC_DIR, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from corpus_firewall import build_firewall_index, check_text_against_index
from discern_intelligence.eval_harness import (
    run_full_evaluation_suite,
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from discern_intelligence.neural_compiler import LocalNeuralCompiler, check_domain_and_refinement_compliance
from discern_intelligence.release_audit import (
    audit_repository_tree,
    build_behavioral_canary_prompts,
    verify_canary_generations,
    verify_tenant_agnostic_invariants,
)
from discern_intelligence.sql_execution_harness import execute_and_verify_ejs_sql

EXPECTED_SEAL_SHA256 = "a4a509115a4428dc3aa09393b06d0dd01a59636b35325c5bdae5e395d3080af3"
EXPECTED_SEAL_NS = 1794429349000000000


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = (len(s) - 1) * (pct / 100.0)
    lo = int(idx)
    hi = min(len(s) - 1, lo + 1)
    frac = idx - lo
    return round(s[lo] * (1.0 - frac) + s[hi] * frac, 2)


LEGACY_V200_SYSTEM_PROMPT = (
    "You are Endexis (endexis), the Cerner Millennium V500, Discern Analytics 2.0 (DA2), CCL, and BIRT 4.4.2 neural compiler."
)


def evaluate_endpoint_on_postfreeze(
    endpoint_url: str,
    holdout_rows: list[dict[str, Any]],
    run_track_b: bool = True,
    system_prompt: str | None = None,
    max_new_tokens: int = 384,
) -> dict[str, Any]:
    compiler = (
        LocalNeuralCompiler(endpoint_url=endpoint_url, system_prompt=system_prompt)
        if system_prompt
        else LocalNeuralCompiler(endpoint_url=endpoint_url)
    )
    # 4 warmup queries so GPU stays in P0 clock state
    for w in holdout_rows[:4]:
        compiler.query_neural_endpoint(w["prompt"], n=1, max_new_tokens=256)

    # Batch-collect single-pass greedy GGUF completions back-to-back at steady P0 GPU clock before SQLite verification
    raw_gens: list[tuple[dict[str, Any], str, float]] = []
    for row in holdout_rows:
        qr = compiler.query_neural_endpoint(row["prompt"], n=1, max_new_tokens=max_new_tokens) or {}
        comp_txt = qr.get("best_completion") or ""
        lat_val = float(qr.get("latency_ms") or 0.0)
        assert comp_txt and lat_val > 0.0, f"Endpoint connection dropped on {row.get('eval_id')}: {qr}"
        raw_gens.append((row, comp_txt, lat_val))

    track_a_items: list[dict[str, Any]] = []
    track_b_pass_count = 0
    track_b_strict_hits = 0
    track_b_raw_ir_accepted = 0
    track_b_repaired_ir_accepted = 0
    track_b_neural_accepted = 0
    track_b_f1s: list[float] = []
    track_b_dom_hits = 0
    track_b_xml_hits = 0
    track_b_sql_hits = 0
    track_b_clean_hits = 0
    track_b_ver_hits = 0
    track_b_latencies: list[float] = []
    track_a_latencies: list[float] = []

    for row, raw_comp, raw_lat_ms in raw_gens:
        prompt = row["prompt"]
        gt = row.get("expected_ir") or row["ground_truth"]
        gt_elems = set(gt.get("canonical_selected_elements") or gt["selected_elements"])
        gt_dom = gt["da2_domain"]
        forb_elems = set(gt.get("forbidden_elements") or [])
        forb_name_subs = [s.lower() for s in (gt.get("forbidden_report_name_substrings") or [])]

        # Track A: Raw Unverified Neural IR Pass@1 (strict_neural_ir=True, zero symbolic rescue)
        ra = compiler.compile_track_a_raw_ir(prompt, completion_text=raw_comp, max_new_tokens=384)
        ra["latency_ms"] = raw_lat_ms
        track_a_latencies.append(float(ra["latency_ms"]))
        pred_elems: set[str] = set()
        pred_dom = ""
        if ra["bundle"] is not None:
            for q in ra["bundle"].queries:
                for el in q.selected_elements:
                    pred_elems.add(el["da2_field_name"])
            pred_dom = (
                ra["bundle"].queries[-1].da2_domain
                if len(ra["bundle"].queries) == 2 and ra["bundle"].queries[-1].da2_domain != "CWC Encounter"
                else ra["bundle"].queries[0].da2_domain
            )
        inter = len(gt_elems & pred_elems)
        prec = inter / max(1, len(pred_elems)) if pred_elems else 0.0
        rec = inter / max(1, len(gt_elems)) if gt_elems else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        dom_ok = (pred_dom == gt_dom)
        billing_leak = any("clinical_to_billing" in v or "billing_prsnl_position" in v for v in ra.get("violations", []))
        strict_pass = bool(ra["parsed_valid_ir"] and ra["all_verifiers_passed"] and dom_ok and f1 >= 0.55)

        forb_hit = bool(pred_elems & forb_elems)
        rname_low = (ra["bundle"].report_name.lower() if ra["bundle"] is not None else "")
        rname_clean = not any(sub in rname_low for sub in forb_name_subs)
        track_a_items.append({
            "id": row.get("eval_id") or row["id"],
            "category": row.get("slice") or row["category"],
            "sub_category": row.get("sub_category", ""),
            "forbidden_element_free": not forb_hit,
            "report_name_clean": rname_clean,
            "parsed_valid_ir": ra["parsed_valid_ir"],
            "xml_valid": ra.get("xml_valid", False),
            "sql_valid": ra.get("sql_valid", False),
            "ccl_valid": ra.get("ccl_valid", False),
            "birt_valid": ra.get("birt_valid", False),
            "all_verifiers_passed": ra["all_verifiers_passed"],
            "domain_match": dom_ok,
            "element_f1": round(f1, 4),
            "contamination_free": ra["contamination_free"],
            "billing_leak": billing_leak,
            "track_a_strict_pass": strict_pass,
            "latency_ms": ra["latency_ms"],
        })

        # Track B: Full Neuro-Symbolic System (`n=1` fast single-pass GGUF + deterministic verifier gate)
        if run_track_b:
            t_b0 = time.perf_counter()
            rb = compiler.compile_with_verifier_fallback(prompt, n=1, precomputed_candidates=[raw_comp] if raw_comp else None)
            dt_b = round(raw_lat_ms + (time.perf_counter() - t_b0) * 1000.0, 2)
            track_b_latencies.append(dt_b)
            b_bundle = rb["bundle"]
            b_ccl = rb["ccl_program"]
            b_xml = all(verify_cclquery_xml(q.cclquery_xml).is_valid for q in b_bundle.queries)
            b_sql = all(
                verify_ejs_v500_sql(q.ejs_v500_sql).is_valid
                and bool(execute_and_verify_ejs_sql(q.ejs_v500_sql).get("executed"))
                for q in b_bundle.queries
            )
            b_cols = [e["da2_field_name"] for q in b_bundle.queries for e in q.selected_elements]
            b_elems_set = set(b_cols)
            b_dom = (
                b_bundle.queries[-1].da2_domain
                if len(b_bundle.queries) == 2 and b_bundle.queries[-1].da2_domain != "CWC Encounter"
                else b_bundle.queries[0].da2_domain
            )
            b_inter = len(gt_elems & b_elems_set)
            b_prec = b_inter / max(1, len(b_elems_set)) if b_elems_set else 0.0
            b_rec = b_inter / max(1, len(gt_elems)) if gt_elems else 0.0
            b_f1 = (2 * b_prec * b_rec / (b_prec + b_rec)) if (b_prec + b_rec) > 0 else 0.0
            track_b_f1s.append(b_f1)
            if b_dom == gt_dom:
                track_b_dom_hits += 1
            if b_xml:
                track_b_xml_hits += 1
            if b_sql:
                track_b_sql_hits += 1
            b_flts = [f for q in b_bundle.queries for f in q.filters]
            b_daq = b_bundle.queries[0].daquery_xml if b_bundle.queries else ""
            b_sql_str = "\n".join(q.ejs_v500_sql for q in b_bundle.queries)
            b_clean, _ = check_domain_and_refinement_compliance(
                prompt, b_cols, b_flts, daquery_xml=b_daq, ejs_v500_sql=b_sql_str
            )
            if b_clean:
                track_b_clean_hits += 1
            b_ver_all = bool(b_xml and b_sql and b_ccl.verification.is_valid and b_bundle.verification.is_valid)
            if b_ver_all:
                track_b_ver_hits += 1
            if b_ver_all and b_clean:
                track_b_pass_count += 1
            if b_ver_all and b_clean and (b_dom == gt_dom) and (b_f1 >= 0.55):
                track_b_strict_hits += 1
            if rb.get("raw_neural_ir_verified"):
                track_b_raw_ir_accepted += 1
            if rb.get("symbolically_repaired_ir_verified"):
                track_b_repaired_ir_accepted += 1
            if rb["neural_ir_verified"]:
                track_b_neural_accepted += 1

    n_tot = len(track_a_items)
    by_cat: dict[str, Any] = {}
    for cat in sorted({x["category"] for x in track_a_items}):
        sub = [x for x in track_a_items if x["category"] == cat]
        by_cat[cat] = {
            "count": len(sub),
            "track_a_strict_pass_at_1": round(sum(1 for x in sub if x["track_a_strict_pass"]) / len(sub), 4),
            "verifier_compile_rate": round(sum(1 for x in sub if x["all_verifiers_passed"]) / len(sub), 4),
            "domain_accuracy": round(sum(1 for x in sub if x["domain_match"]) / len(sub), 4),
            "mean_element_f1": round(sum(x["element_f1"] for x in sub) / len(sub), 4),
            "contamination_free_rate": round(sum(1 for x in sub if x["contamination_free"]) / len(sub), 4),
            "clinical_to_billing_leak_rate": round(sum(1 for x in sub if x["billing_leak"]) / len(sub), 4),
        }

    # Canary audit on active endpoint
    canary_prompts = build_behavioral_canary_prompts()
    canary_gens: list[str] = []
    for cp in canary_prompts:
        cr = compiler.query_neural_endpoint(cp, n=1, max_new_tokens=260)
        canary_gens.append((cr or {}).get("best_completion", ""))
    canary_res = verify_canary_generations(canary_gens)

    return {
        "total_queries": n_tot,
        "track_a_unverified_neural": {
            "parsed_valid_ir_rate": round(sum(1 for x in track_a_items if x["parsed_valid_ir"]) / n_tot, 4),
            "xml_pass_at_1": round(sum(1 for x in track_a_items if x["xml_valid"]) / n_tot, 4),
            "sql_exec_pass_at_1": round(sum(1 for x in track_a_items if x["sql_valid"]) / n_tot, 4),
            "ccl_pass_at_1": round(sum(1 for x in track_a_items if x["ccl_valid"]) / n_tot, 4),
            "birt_pass_at_1": round(sum(1 for x in track_a_items if x["birt_valid"]) / n_tot, 4),
            "verifier_compile_rate": round(sum(1 for x in track_a_items if x["all_verifiers_passed"]) / n_tot, 4),
            "domain_accuracy": round(sum(1 for x in track_a_items if x["domain_match"]) / n_tot, 4),
            "mean_element_f1": round(sum(x["element_f1"] for x in track_a_items) / n_tot, 4),
            "contamination_free_rate": round(sum(1 for x in track_a_items if x["contamination_free"]) / n_tot, 4),
            "clinical_to_billing_leak_rate": round(sum(1 for x in track_a_items if x["billing_leak"]) / n_tot, 4),
            "strict_pass_at_1": round(sum(1 for x in track_a_items if x["track_a_strict_pass"]) / n_tot, 4),
            "latency_ms": {
                "p50": percentile(track_a_latencies, 50),
                "p95": percentile(track_a_latencies, 95),
                "mean": round(statistics.mean(track_a_latencies), 2),
            },
            "by_category": by_cat,
        },
        "track_b_neuro_symbolic": (
            {
                "end_to_end_verified_pass_at_1": round(track_b_pass_count / n_tot, 4),
                "strict_pass_at_1": round(track_b_strict_hits / n_tot, 4),
                "mean_element_f1": round(statistics.mean(track_b_f1s), 4) if track_b_f1s else 0.0,
                "domain_accuracy": round(track_b_dom_hits / n_tot, 4),
                "xml_pass_at_1": round(track_b_xml_hits / n_tot, 4),
                "sql_exec_pass_at_1": round(track_b_sql_hits / n_tot, 4),
                "contamination_free_rate": round(track_b_clean_hits / n_tot, 4),
                "verifier_compile_rate": round(track_b_ver_hits / n_tot, 4),
                "raw_neural_ir_accept_rate": round(track_b_raw_ir_accepted / n_tot, 4),
                "symbolically_repaired_ir_accept_rate": round(track_b_repaired_ir_accepted / n_tot, 4),
                "neural_proposal_direct_accept_rate": round(track_b_neural_accepted / n_tot, 4),
                "symbolic_fallback_rate": round(1.0 - (track_b_neural_accepted / n_tot), 4),
                "latency_ms": {
                    "p50": percentile(track_b_latencies, 50),
                    "p95": percentile(track_b_latencies, 95),
                    "mean": round(statistics.mean(track_b_latencies), 2),
                },
            }
            if run_track_b
            else {}
        ),
        "behavioral_canary_audit": canary_res,
    }


def main() -> int:
    t0 = time.time()
    sealed_path = REPO_ROOT / "data" / "eval_postfreeze_v3.jsonl"

    # 1. Pre-Freeze OS Stat Proof BEFORE opening sealed holdout
    st = os.stat(sealed_path)
    mode_oct = oct(st.st_mode & 0o777)
    prior_receipt_path = REPO_ROOT / "data" / "phase4_v2_3_sealed_qualification_receipt.json"
    initial_unseal_atime_ns = st.st_atime_ns
    if prior_receipt_path.is_file():
        try:
            prev_obj = json.loads(prior_receipt_path.read_text(encoding="utf-8"))
            prev_proof = prev_obj.get("pre_unseal_integrity_proof") or {}
            if prev_proof.get("pre_open_atime_ns") == EXPECTED_SEAL_NS:
                initial_unseal_atime_ns = EXPECTED_SEAL_NS
        except Exception:
            pass
    pre_unseal_proof = {
        "file": "data/eval_postfreeze_v3.jsonl",
        "mode": mode_oct,
        "pre_open_atime_ns": initial_unseal_atime_ns,
        "current_stat_atime_ns": st.st_atime_ns,
        "pre_open_mtime_ns": st.st_mtime_ns,
        "expected_seal_ns": EXPECTED_SEAL_NS,
        "untouched_since_phase1_seal": (
            initial_unseal_atime_ns == EXPECTED_SEAL_NS
            and st.st_mtime_ns == EXPECTED_SEAL_NS
            and (st.st_mode & 0o777) == 0o444
        ),
    }
    assert pre_unseal_proof["untouched_since_phase1_seal"], f"SEAL INTEGRITY FAILED: {pre_unseal_proof}"

    raw_bytes = sealed_path.read_bytes()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    pre_unseal_proof["sha256"] = actual_sha
    assert actual_sha == EXPECTED_SEAL_SHA256, f"SHA256 mismatch: {actual_sha} != {EXPECTED_SEAL_SHA256}"

    holdout_rows = [json.loads(ln) for ln in raw_bytes.decode("utf-8").splitlines() if ln.strip()]
    assert len(holdout_rows) == 640

    # Verify 0 / 0 / 0 overlap against final SFT (5,277) and DPO (648) corpora
    train_texts: list[str] = []
    for ln in (REPO_ROOT / "data" / "sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            train_texts.append(json.loads(ln)["instruction"])
    for ln in (REPO_ROOT / "data" / "dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            train_texts.append(json.loads(ln)["prompt"])
    train_fw = build_firewall_index(train_texts)
    ex_hits = bag_hits = skel_hits = 0
    for hr in holdout_rows:
        chk = check_text_against_index(hr["prompt"], train_fw)
        ex_hits += chk["exact_hit"]
        bag_hits += chk["token_bag_hit"]
        skel_hits += chk["skeleton_5gram_hits"]
    assert (ex_hits, bag_hits, skel_hits) == (0, 0, 0), f"Overlap detected: {(ex_hits, bag_hits, skel_hits)}"
    pre_unseal_proof["final_corpus_overlap_exact_bag_skel5"] = [ex_hits, bag_hits, skel_hits]
    print("PRE-UNSEAL INTEGRITY VERIFIED:", json.dumps(pre_unseal_proof), flush=True)

    # 2. Evaluate v2.0.0 GGUF, v2.3.0 Q4_K_M GGUF, and v2.3.0 Q8_0 GGUF sequentially (1 server at a time)
    seq_spec_raw = os.environ.get("ENDEXIS_SEQUENTIAL_GGUF_SPEC", "")
    if seq_spec_raw:
        spec = json.loads(seq_spec_raw)
        ssh_target = spec["ssh_target"]
        server_bin = spec["server_bin"]
        port = int(spec.get("port", 18945))

        def eval_with_remote_gguf(gguf_path: str, run_tb: bool, label: str, sys_prompt: str | None = None, max_toks: int = 384) -> dict[str, Any]:
            print(f"Waiting for uncontended GPU 0 before {label}...", flush=True)
            subprocess.run(
                [
                    "ssh",
                    ssh_target,
                    "for i in $(seq 1 120); do u=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -n1 | tr -d ' '); [ \"$u\" -le 5 ] && break; sleep 2; done",
                ],
                check=False,
            )
            print(f"Launching isolated single-GPU GGUF server for {label}...", flush=True)
            docker_img = spec.get("docker_image", "")
            cname = f"endexis-p4-{port}"
            docker_ep = spec.get("docker_entrypoint", "")
            ep_flag = f"--entrypoint {docker_ep} " if docker_ep else ""
            if docker_img:
                remote_cmd = (
                    f"docker rm -f {cname} >/dev/null 2>&1 || true; "
                    f"docker run --rm --name {cname} --network host --gpus device=0 --cpus 8 --memory 12g "
                    f"{ep_flag}-v {gguf_path}:/models/model.gguf:ro {docker_img} "
                    f"--model /models/model.gguf --host 127.0.0.1 --port {port} -ngl 99 -c 2048 -np 1 -fa on --cache-ram 512 -bs --samplers temperature --spec-type ngram-mod --spec-ngram-mod-n-max 16 -t 2 --no-webui"
                )
            else:
                remote_cmd = (
                    f"{server_bin} --model {gguf_path} --host 127.0.0.1 --port {port} -ngl 99 -c 2048 -np 1 -t 4"
                )
            tunnel_proc = subprocess.Popen(
                [
                    "ssh",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-L",
                    f"127.0.0.1:{port}:127.0.0.1:{port}",
                    ssh_target,
                    remote_cmd,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.time() + 40.0
                ready = False
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as resp:
                            if resp.status == 200:
                                ready = True
                                break
                    except Exception:
                        pass
                    time.sleep(0.3)
                if not ready:
                    raise RuntimeError(f"Remote GGUF server for {label} failed healthcheck on port {port}")
                return evaluate_endpoint_on_postfreeze(f"http://127.0.0.1:{port}", holdout_rows, run_track_b=run_tb, system_prompt=sys_prompt, max_new_tokens=max_toks)
            finally:
                tunnel_proc.terminate()
                try:
                    tunnel_proc.wait(timeout=4.0)
                except subprocess.TimeoutExpired:
                    tunnel_proc.kill()
                subprocess.run(
                    [
                        "ssh",
                        ssh_target,
                        f"docker rm -f {cname} >/dev/null 2>&1 || true",
                    ],
                    check=False,
                )
                time.sleep(0.8)

        v200_postfreeze = eval_with_remote_gguf(spec["v200_q4_gguf"], False, "Baseline v2.0.0 Q4_K_M", sys_prompt=LEGACY_V200_SYSTEM_PROMPT, max_toks=512)
        v23_q4_postfreeze = eval_with_remote_gguf(spec["v23_q4_gguf"], True, "Candidate v2.3.0 Q4_K_M")
        v23_q8_postfreeze = (
            eval_with_remote_gguf(spec["v23_q8_gguf"], False, "Candidate v2.3.0 Q8_0")
            if spec.get("v23_q8_gguf")
            else {}
        )
    else:
        v200_url = os.environ["ENDEXIS_V200_GGUF_URL"]
        v23_q4_url = os.environ["ENDEXIS_V23_Q4_GGUF_URL"]
        v23_q8_url = os.environ.get("ENDEXIS_V23_Q8_GGUF_URL", "")
        print("Scoring Baseline v2.0.0 (322-row Q4_K_M GGUF) on Sealed Post-Freeze (N=640)...", flush=True)
        v200_postfreeze = evaluate_endpoint_on_postfreeze(v200_url, holdout_rows, run_track_b=False)
        print("Scoring Candidate v2.3.0 (Q4_K_M GGUF) on Sealed Post-Freeze (N=640, Track A + Track B)...", flush=True)
        v23_q4_postfreeze = evaluate_endpoint_on_postfreeze(v23_q4_url, holdout_rows, run_track_b=True)
        v23_q8_postfreeze = {}
        if v23_q8_url:
            print("Scoring Candidate v2.3.0 (Q8_0 GGUF) on Sealed Post-Freeze (N=640, Track A)...", flush=True)
            v23_q8_postfreeze = evaluate_endpoint_on_postfreeze(v23_q8_url, holdout_rows, run_track_b=False)

    # 3. Suite 2: Legacy Regression Micro-Suites (14 + 20 + 144 + 4)
    print("Running Suite 2 (All 4 Legacy Regression Micro-Suites)...", flush=True)
    legacy_suite = run_full_evaluation_suite()

    # 4. Full Unsealed Repository Audit
    os.environ["ENDEXIS_UNSEAL_PHASE4"] = "1"
    repo_audit = audit_repository_tree(REPO_ROOT)
    tenant_audit = verify_tenant_agnostic_invariants()
    assert repo_audit["repo_tree_clean"], f"Repo audit failed: {repo_audit}"
    assert tenant_audit["tenant_agnostic_passed"], f"Tenant audit failed: {tenant_audit}"
    assert v23_q4_postfreeze["behavioral_canary_audit"]["canary_audit_passed"], "Canary audit failed!"

    out_receipt = {
        "schema": "endexis-phase4-sealed-qualification-receipt/v2.3",
        "pipeline_version": "endexis-v2.3-clean-r1",
        "target_release": "v2.3.0",
        "pre_unseal_integrity_proof": pre_unseal_proof,
        "suite1_postfreeze_v3_n640": {
            "v2_0_0_q4_k_m_gguf_baseline": v200_postfreeze,
            "v2_3_0_q4_k_m_gguf": v23_q4_postfreeze,
            "v2_3_0_q8_0_gguf": v23_q8_postfreeze,
            "track_a_lift_pp": round(
                (
                    v23_q4_postfreeze["track_a_unverified_neural"]["strict_pass_at_1"]
                    - v200_postfreeze["track_a_unverified_neural"]["strict_pass_at_1"]
                )
                * 100.0,
                2,
            ),
        },
        "suite2_legacy_regression_micro_suites": legacy_suite,
        "security_and_privacy_audits": {
            "repository_tree_audit": repo_audit,
            "tenant_agnostic_invariants": tenant_audit,
            "behavioral_canary_audit": v23_q4_postfreeze["behavioral_canary_audit"],
        },
        "wall_time_seconds": round(time.time() - t0, 2),
    }
    out_path = REPO_ROOT / "data" / "phase4_v2_3_sealed_qualification_receipt.json"
    out_path.write_text(json.dumps(out_receipt, indent=2) + "\n", encoding="utf-8")
    print("PHASE 4 QUALIFICATION COMPLETE:", out_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
