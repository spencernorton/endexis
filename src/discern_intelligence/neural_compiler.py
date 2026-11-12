"""
Neuro-Symbolic Client & Compiler (`LocalNeuralCompiler`) for Endexis (`endexis` v2.3.0).

Connects to the merged Stage-2 RPO-DPO model (`endexis-qwen3-0.6b-dpo-merged` / GGUF `llama-swap`)
served via local HTTP (`http://127.0.0.1:8945`) or an optional configured endpoint, supports:
- **Track A (`compile_track_a_raw_ir`)**: Unverified raw neural IR parsing and compilation without
  symbolic rescue for honest 5-fold OOF and sealed post-freeze evaluation.
- **Track B (`compile_with_verifier_fallback`)**: Best-of-N (`N=1` or `N=4`) neural IR proposals
  gated 100% through deterministic AST/XML/SQL/SQLite/CCL/.rptdesign + clinical-vs-billing
  isolation + multi-turn refinement verifiers with automatic neuro-symbolic fallback.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import http.client
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .ccl_compiler import CCLCompiler
from .da2_generator import DA2ReportGenerator, _resolve_override_filter_values
from .eval_harness import (
    verifier_guided_best_of_n,
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from .lexical_guards import (
    is_financial_adjustment_request,
    is_financial_charge_or_revenue_request,
    is_financial_payment_request,
    is_personnel_position_request,
)
from .schemas import NeuralCompilationIR, RefinementSpec, TenantConfig
from .sql_execution_harness import execute_and_verify_ejs_sql

DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:8945"
DEFAULT_MODEL_ID = "endexis-qwen3-0.6b-dpo-merged"
CANONICAL_SYSTEM_PROMPT = (
    "You are Endexis (endexis), the Cerner Millennium V500, Discern Analytics 2.0 (DA2), CCL, and BIRT 4.4.2 neural compiler. "
    "Emit a concise <reasoning>...</reasoning> trace followed by valid JSON compilation output inside <output>...</output>."
)


def _extract_legacy_ir_from_xml(completion_text: str) -> NeuralCompilationIR | None:
    """Allows fair Track-A scoring of legacy v2.0.0 completions that emitted <cclquery> XML instead of JSON IR."""
    if "<cclquery" not in completion_text:
        return None
    m_xml = re.search(r"(<cclquery[\s\S]*?</cclquery>)", completion_text)
    if not m_xml:
        return None
    try:
        root = ET.fromstring(m_xml.group(1))
        elems: list[str] = []
        for el in root.findall(".//elements/element"):
            nm = (el.attrib.get("name") or el.attrib.get("display") or "").strip()
            if nm and nm not in elems:
                elems.append(nm)
        if not elems:
            return None
        return NeuralCompilationIR(selected_elements=elems)
    except Exception:
        return None


def check_domain_and_refinement_compliance(
    request_text: str,
    selected_element_names: list[str],
    filters: list[str],
    daquery_xml: str = "",
    ejs_v500_sql: str = "",
    tenant_config: TenantConfig | None = None,
) -> tuple[bool, list[str]]:
    """Checks clinical-vs-billing contamination and multi-turn negative/override refinement satisfaction across filters, XML, and SQL."""
    violations: list[str] = []
    allow_adj = is_financial_adjustment_request(request_text)
    allow_pay = is_financial_payment_request(request_text)
    allow_chg = is_financial_charge_or_revenue_request(request_text)
    allow_pos = is_personnel_position_request(request_text)

    for col in selected_element_names:
        if col == "Prsnl Position" and not allow_pos:
            violations.append("billing_prsnl_position_contamination:Prsnl Position")
        if (col.startswith("Adj ") or col == "AdjTrans Alias") and not allow_adj:
            violations.append(f"clinical_to_billing_adj_contamination:{col}")
        if (col.startswith("Pay ") or col == "PayTrans Alias") and not allow_pay:
            violations.append(f"clinical_to_billing_pay_contamination:{col}")
        if col.startswith(("Chg ", "BO ", "ChgRecon:")) and not (allow_chg or allow_adj or allow_pay):
            violations.append(f"clinical_to_billing_chg_contamination:{col}")

    ref = RefinementSpec.from_request(request_text)
    sel_lows = {c.lower() for c in selected_element_names}
    for excl_c in ref.exclude_columns:
        if excl_c.lower() in sel_lows:
            violations.append(f"excluded_column_retained:{excl_c}")

    ov_low_keys = {k.lower() for k in ref.override_filters}
    eff_excl_flts = [ef for ef in ref.exclude_filters if ef.lower() not in ov_low_keys]
    flt_blob_low = " | ".join(filters).lower()

    qual_xml_low = ""
    if daquery_xml:
        try:
            dq_root = ET.fromstring(daquery_xml.strip())
            qual_nodes = dq_root.findall(".//qualification")
            qual_xml_low = "\n".join(ET.tostring(qn, encoding="unicode") for qn in qual_nodes).lower()
        except Exception:
            qual_xml_low = ""

    where_sql = ""
    if ejs_v500_sql and "\nWHERE " in ejs_v500_sql:
        where_sql = ejs_v500_sql.split("\nWHERE ", 1)[1]

    sql_col_map = {
        "enc type": "CWC_ENCOUNTER.ENCNTR_TYPE_CD",
        "enc loc nurse unit": "CWC_ENCOUNTER.LOC_NURSE_UNIT_CD",
        "enc loc facility": "CWC_ENCOUNTER.LOC_FACILITY_CD",
        "enc status": "CWC_ENCOUNTER.ENCNTR_STATUS_CD",
        "ce result status": "CWC_CLINICAL_EVENT.RESULT_STATUS_CD",
        "order status": "CWC_ORDERS.ORDER_STATUS_CD",
    }

    for excl_f in eff_excl_flts:
        ef_low = excl_f.lower()
        if ef_low in flt_blob_low:
            violations.append(f"excluded_filter_retained:{excl_f}")
        if qual_xml_low and f'name="{ef_low}"' in qual_xml_low:
            violations.append(f"excluded_filter_retained_in_daquery:{excl_f}")
        mapped_sql_col = sql_col_map.get(ef_low)
        if where_sql and mapped_sql_col and mapped_sql_col in where_sql:
            violations.append(f"excluded_filter_retained_in_sql:{excl_f}")

    for ov_k, ov_vals in ref.override_filters.items():
        if ov_k.lower() not in flt_blob_low:
            violations.append(f"override_filter_missing:{ov_k}")
            continue
        try:
            _, ov_pairs = _resolve_override_filter_values(ov_k, ov_vals, tenant_config=tenant_config)
        except ValueError as exc:
            violations.append(f"override_filter_invalid:{ov_k}:{exc}")
            continue
        exp_codes = {c for _, c in ov_pairs}
        mapped_sql_col = sql_col_map.get(ov_k.lower())
        if where_sql and mapped_sql_col and (ov_k.lower() != "order status" or "V500.ORDERS CWC_ORDERS" in ejs_v500_sql):
            m_in = re.search(rf"{re.escape(mapped_sql_col)}\s+IN\s*\(([^)]+)\)", where_sql)
            if not m_in:
                violations.append(f"override_filter_sql_missing:{ov_k}")
            else:
                actual_codes = {tok.strip() for tok in m_in.group(1).split(",") if tok.strip()}
                if actual_codes != exp_codes:
                    violations.append(f"override_filter_sql_mismatch:{ov_k}:{sorted(actual_codes)}!={sorted(exp_codes)}")

    return len(violations) == 0, violations


class LocalNeuralCompiler:
    """
    Neuro-symbolic compiler uniting the fine-tuned `endexis-qwen3-0.6b-dpo-merged` model
    with deterministic V500 / DA2 / CCL / BIRT 4.4.2 verification and fallback.
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        ssh_host: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        system_prompt: str = CANONICAL_SYSTEM_PROMPT,
    ) -> None:
        self.endpoint_url = (
            endpoint_url
            or os.environ.get("ENDEXIS_NEURAL_URL")
            or os.environ.get("NEURAL_COMPILER_URL")
            or DEFAULT_LOCAL_ENDPOINT
        ).rstrip("/")
        self.ssh_host = ssh_host or os.environ.get("ENDEXIS_SSH_HOST")
        self.model_id = model_id
        self.system_prompt = system_prompt
        self._endpoint_mode: str | None = None
        self._http_conn: http.client.HTTPConnection | None = None

    def health(self, timeout: float = 2.0) -> dict[str, Any]:
        """Checks runtime availability of the neural endpoint (local HTTP or optional SSH tunnel)."""
        try:
            req = urllib.request.Request(f"{self.endpoint_url}/health")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                data["transport"] = "http_direct"
                return data
        except Exception:
            pass

        if self.ssh_host:
            try:
                proc = subprocess.run(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2", self.ssh_host, "curl -s --max-time 2 http://127.0.0.1:8945/health"],
                    capture_output=True,
                    text=True,
                    timeout=4.0,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip().startswith("{"):
                    data = json.loads(proc.stdout.strip())
                    data["transport"] = f"ssh_{self.ssh_host}"
                    return data
            except Exception:
                pass

        return {
            "status": "offline_fallback",
            "service": "endexis-deterministic-fallback",
            "model": f"{self.model_id} (offline verifier fallback)",
            "transport": "offline_deterministic_verifier",
        }

    def _post_json_keepalive(self, path: str, payload_bytes: bytes, timeout: float = 15.0) -> dict[str, Any]:
        parsed = urllib.parse.urlparse(self.endpoint_url)
        if parsed.scheme != "http" or not parsed.hostname:
            req = urllib.request.Request(
                f"{self.endpoint_url}{path}",
                data=payload_bytes,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        port = parsed.port or 80
        base_path = parsed.path.rstrip("/")
        full_path = f"{base_path}{path}"
        for attempt in range(2):
            try:
                if self._http_conn is None:
                    self._http_conn = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
                self._http_conn.request(
                    "POST",
                    full_path,
                    body=payload_bytes,
                    headers={"Content-Type": "application/json", "Connection": "keep-alive"},
                )
                resp = self._http_conn.getresponse()
                raw = resp.read()
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
                return json.loads(raw.decode("utf-8"))
            except Exception:
                if self._http_conn is not None:
                    try:
                        self._http_conn.close()
                    except Exception:
                        pass
                    self._http_conn = None
                if attempt == 1:
                    raise
        raise RuntimeError("Unreachable")

    def query_neural_endpoint(self, prompt: str, n: int = 4, max_new_tokens: int = 384) -> dict[str, Any] | None:
        """Queries the local or remote neural endpoint (/v1/compile or OpenAI-compatible GGUF /v1/chat/completions)."""
        payload_str = json.dumps({"prompt": prompt, "n": n, "max_new_tokens": max_new_tokens})
        if self._endpoint_mode in (None, "compile"):
            try:
                t0 = time.perf_counter()
                res = self._post_json_keepalive("/v1/compile", payload_str.encode("utf-8"), timeout=8.0)
                res.setdefault("latency_ms", round((time.perf_counter() - t0) * 1000.0, 2))
                res["transport"] = "http_direct"
                self._endpoint_mode = "compile"
                return res
            except Exception:
                if self._endpoint_mode == "compile":
                    self._endpoint_mode = None

        # OpenAI-compatible /v1/chat/completions (llama.cpp llama-server, llama-swap, vLLM)
        try:
            t0 = time.perf_counter()
            candidates: list[str] = []
            # Single-slot (-np 1) compatible: always run greedy (T=0.0) first; if n > 1, try n-way or sequential T=0.3
            req_dict: dict[str, Any] = {
                "model": self.model_id,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.0,
                "n": 1,
                "max_tokens": max_new_tokens,
                "stop": ["</output>"],
                "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            chat_res = self._post_json_keepalive("/v1/chat/completions", json.dumps(req_dict).encode("utf-8"), timeout=15.0)
            self._endpoint_mode = "openai_chat"
            for ch in (chat_res.get("choices") or []):
                msg = ch.get("message", {})
                rc = msg.get("reasoning_content") or ""
                cc = msg.get("content") or ""
                txt = (f"<reasoning>\n{rc}\n</reasoning>\n{cc}".strip() if rc and "<reasoning>" not in cc else cc)
                if txt and "<output>" in txt and "</output>" not in txt:
                    txt = txt.rstrip() + "\n</output>"
                if txt:
                    candidates.append(txt)
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if candidates:
                return {
                    "best_completion": candidates[0],
                    "candidates": candidates,
                    "transport": "openai_compatible_gguf_http",
                    "latency_ms": dt_ms,
                    "vram_allocated_mb": 0.0,
                }
        except Exception:
            pass

        if self.ssh_host:
            try:
                proc = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=2",
                        self.ssh_host,
                        "curl -s --max-time 12 -X POST http://127.0.0.1:8945/v1/compile -H 'Content-Type: application/json' -d @-",
                    ],
                    input=payload_str,
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip().startswith("{"):
                    res = json.loads(proc.stdout.strip())
                    res["transport"] = f"ssh_{self.ssh_host}"
                    return res
            except Exception:
                pass

        return None

    def compile_track_a_raw_ir(
        self,
        request_text: str,
        completion_text: str | None = None,
        max_new_tokens: int = 384,
    ) -> dict[str, Any]:
        """
        Track-A Raw Unverified Neural Evaluation:
        Parses `NeuralCompilationIR` directly from the raw model completion and compiles strictly
        from that IR (`strict_neural_ir=True`) WITHOUT symbolic interpreter/guard rescue or fallback.
        """
        latency_ms = 0.0
        raw_text = completion_text
        if raw_text is None:
            resp = self.query_neural_endpoint(request_text, n=1, max_new_tokens=max_new_tokens)
            if resp:
                raw_text = resp.get("best_completion") or ""
                latency_ms = float(resp.get("latency_ms") or 0.0)

        has_reasoning = bool(raw_text and "<reasoning>" in raw_text and "</reasoning>" in raw_text)
        ir = NeuralCompilationIR.parse_from_completion(raw_text or "")
        if ir is None and raw_text:
            ir = _extract_legacy_ir_from_xml(raw_text)

        if ir is None or not ir.selected_elements:
            return {
                "parsed_valid_ir": False,
                "has_reasoning_trace": has_reasoning,
                "ir": None,
                "all_verifiers_passed": False,
                "contamination_free": False,
                "violations": ["unparseable_or_empty_neural_ir"],
                "latency_ms": latency_ms,
                "bundle": None,
                "ccl_program": None,
            }

        gen = DA2ReportGenerator()
        try:
            bundle = gen.generate(
                request_text,
                tenancy=ir.tenancy,
                neural_ir=ir,
                strict_neural_ir=True,
            )
            ccl_prog = CCLCompiler().compile_from_neural_ir(ir, request_text=request_text)
        except ValueError as exc:
            return {
                "parsed_valid_ir": True,
                "has_reasoning_trace": has_reasoning,
                "ir": ir,
                "xml_valid": False,
                "sql_valid": False,
                "ccl_valid": False,
                "birt_valid": False,
                "contamination_free": False,
                "all_verifiers_passed": False,
                "violations": [f"invalid_ir_value:{exc}"],
                "latency_ms": latency_ms,
                "bundle": None,
                "ccl_program": None,
            }

        xml_ok = all(verify_cclquery_xml(q.cclquery_xml).is_valid for q in bundle.queries)
        sql_ok = all(
            verify_ejs_v500_sql(q.ejs_v500_sql).is_valid and bool(execute_and_verify_ejs_sql(q.ejs_v500_sql).get('executed'))
            for q in bundle.queries
        )
        ccl_ok = ccl_prog.verification.is_valid
        birt_ok = bundle.verification.is_valid

        emitted_cols = [e["da2_field_name"] for q in bundle.queries for e in q.selected_elements]
        emitted_flts = [f for q in bundle.queries for f in q.filters]
        daq_str = bundle.queries[0].daquery_xml if bundle.queries else ""
        sql_str = "\n".join(q.ejs_v500_sql for q in bundle.queries)
        clean_ok, violations = check_domain_and_refinement_compliance(
            request_text,
            emitted_cols,
            emitted_flts,
            daquery_xml=daq_str,
            ejs_v500_sql=sql_str,
        )

        return {
            "parsed_valid_ir": True,
            "has_reasoning_trace": has_reasoning,
            "ir": ir,
            "xml_valid": xml_ok,
            "sql_valid": sql_ok,
            "ccl_valid": ccl_ok,
            "birt_valid": birt_ok,
            "contamination_free": clean_ok,
            "all_verifiers_passed": bool(xml_ok and sql_ok and ccl_ok and birt_ok and clean_ok),
            "violations": violations,
            "latency_ms": latency_ms,
            "bundle": bundle,
            "ccl_program": ccl_prog,
        }

    def compile_with_verifier_fallback(
        self,
        request_text: str,
        n: int = 4,
        tenancy: str | None = None,
        report_name: str | None = None,
        precomputed_candidates: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Executes Track-B Full Neuro-Symbolic Compilation:
        1. Samples `N` neural candidates from the local neural endpoint when reachable.
        2. Evaluates parsed `NeuralCompilationIR` candidates strictly (`raw_neural_ir`) first, then with
           symbolic IR repair (`symbolically_repaired_ir`), gated through deterministic DA2/XML/SQL/SQLite/CCL/BIRT
           + domain-isolation + multi-turn refinement verifiers.
        3. Guarantees 100% deterministic verifier compliance via `verifier_guided_best_of_n` (`symbolic_fallback`).
        """
        if precomputed_candidates is not None:
            neural_resp: dict[str, Any] | None = {
                "best_completion": precomputed_candidates[0] if precomputed_candidates else "",
                "candidates": list(precomputed_candidates),
                "transport": "openai_compatible_gguf_http",
                "latency_ms": 0.0,
                "vram_allocated_mb": 0.0,
            }
        else:
            neural_resp = self.query_neural_endpoint(request_text, n=n)
        t_ver0 = time.perf_counter()
        neural_latency_ms = float(neural_resp.get("latency_ms", 0.0)) if neural_resp else 0.0
        neural_used = neural_resp is not None and bool(neural_resp.get("best_completion"))
        neural_trace = neural_resp["best_completion"] if neural_used else None
        if neural_trace and "</reasoning>" not in neural_trace:
            neural_trace = neural_trace.strip() + "\n</reasoning>"

        verified_neural_bundle = None
        verified_neural_ccl = None
        neural_verified_count = 0
        acceptance_mode = "symbolic_fallback"

        def _try_candidate(cand_text: str, strict_mode: bool, gen_inst: DA2ReportGenerator, ccl_inst: CCLCompiler) -> tuple[Any, Any] | None:
            ir = NeuralCompilationIR.parse_from_completion(cand_text)
            if ir is None or not ir.selected_elements:
                return None
            try:
                cand_bundle = gen_inst.generate(
                    request_text,
                    report_name=report_name,
                    tenancy=tenancy or (ir.tenancy if strict_mode else None),
                    neural_ir=ir,
                    strict_neural_ir=strict_mode,
                )
                cand_ccl = (
                    ccl_inst.compile_from_neural_ir(ir, request_text=request_text)
                    if strict_mode
                    else ccl_inst.compile(request_text, tenancy=tenancy or cand_bundle.target_tenancy)
                )
            except ValueError:
                return None
            xml_ok = all(verify_cclquery_xml(q.cclquery_xml).is_valid for q in cand_bundle.queries)
            sql_ok = all(
                verify_ejs_v500_sql(q.ejs_v500_sql).is_valid
                and bool(execute_and_verify_ejs_sql(q.ejs_v500_sql).get("executed"))
                for q in cand_bundle.queries
            )
            emitted_cols = [e["da2_field_name"] for q in cand_bundle.queries for e in q.selected_elements]
            emitted_flts = [f for q in cand_bundle.queries for f in q.filters]
            daq_str = cand_bundle.queries[0].daquery_xml if cand_bundle.queries else ""
            sql_str = "\n".join(q.ejs_v500_sql for q in cand_bundle.queries)
            clean_ok, _ = check_domain_and_refinement_compliance(
                request_text,
                emitted_cols,
                emitted_flts,
                daquery_xml=daq_str,
                ejs_v500_sql=sql_str,
            )
            if xml_ok and sql_ok and cand_ccl.verification.is_valid and cand_bundle.verification.is_valid and clean_ok:
                return cand_bundle, cand_ccl
            return None

        if neural_resp and neural_resp.get("candidates"):
            gen = DA2ReportGenerator()
            ccl_comp = CCLCompiler()
            # Pass 1: Strict unassisted neural IR verification ("raw_neural_ir")
            for cand_text in neural_resp["candidates"]:
                hit = _try_candidate(cand_text, True, gen, ccl_comp)
                if hit is not None:
                    neural_verified_count += 1
                    if verified_neural_bundle is None:
                        verified_neural_bundle, verified_neural_ccl = hit
                        acceptance_mode = "raw_neural_ir"
            # Pass 2: Neuro-symbolic IR repair ("symbolically_repaired_ir") when strict raw IR did not pass
            if verified_neural_bundle is None:
                for cand_text in neural_resp["candidates"]:
                    hit = _try_candidate(cand_text, False, gen, ccl_comp)
                    if hit is not None:
                        neural_verified_count += 1
                        if verified_neural_bundle is None:
                            verified_neural_bundle, verified_neural_ccl = hit
                            acceptance_mode = "symbolically_repaired_ir"

        if verified_neural_bundle is None and n > 1 and precomputed_candidates is None and self._endpoint_mode == "openai_chat":
            gen = DA2ReportGenerator()
            ccl_comp = CCLCompiler()
            for retry_idx in range(min(n - 1, 3)):
                try:
                    t_r0 = time.perf_counter()
                    r_payload = json.dumps({
                        "model": self.model_id,
                        "messages": [
                            {"role": "system", "content": CANONICAL_SYSTEM_PROMPT},
                            {"role": "user", "content": request_text},
                        ],
                        "temperature": 0.3,
                        "seed": 42 + retry_idx,
                        "n": 1,
                        "max_tokens": 384,
                        "stop": ["</output>"],
                        "cache_prompt": True,
                    }).encode("utf-8")
                    r_res = self._post_json_keepalive("/v1/chat/completions", r_payload, timeout=15.0)
                    neural_latency_ms += (time.perf_counter() - t_r0) * 1000.0
                    ch0 = (r_res.get("choices") or [{}])[0]
                    cand_text = (ch0.get("message") or {}).get("content") or ""
                    if cand_text and "<output>" in cand_text and "</output>" not in cand_text:
                        cand_text = cand_text.rstrip() + "\n</output>"
                    hit = _try_candidate(cand_text, True, gen, ccl_comp)
                    if hit is not None:
                        neural_verified_count += 1
                        verified_neural_bundle, verified_neural_ccl = hit
                        acceptance_mode = "raw_neural_ir"
                        neural_trace = cand_text
                        break
                    hit_rep = _try_candidate(cand_text, False, gen, ccl_comp)
                    if hit_rep is not None:
                        neural_verified_count += 1
                        verified_neural_bundle, verified_neural_ccl = hit_rep
                        acceptance_mode = "symbolically_repaired_ir"
                        neural_trace = cand_text
                        break
                except Exception:
                    break

        if verified_neural_bundle is not None and verified_neural_ccl is not None:
            chosen_bundle = verified_neural_bundle
            chosen_ccl = verified_neural_ccl
            n_eval = len(neural_resp.get("candidates", [])) if neural_resp else 1
            n_ver = neural_verified_count
            sel_score = 1.0
            ver_trace = neural_trace or "<reasoning>\nVerified neural IR compilation.\n</reasoning>"
        else:
            acceptance_mode = "symbolic_fallback"
            bon = verifier_guided_best_of_n(request_text, n=n, tenancy=tenancy, report_name=report_name)
            chosen_bundle = bon["bundle"]
            chosen_ccl = bon["ccl_program"]
            n_eval = max(bon["n_candidates_evaluated"], len(neural_resp.get("candidates", [])) if neural_resp else 0)
            n_ver = max(bon["all_candidates_verified_count"], neural_verified_count)
            sel_score = bon["selected_verifier_score"]
            ver_trace = bon["reasoning_trace"]

        elapsed_after_initial_ms = (time.perf_counter() - t_ver0) * 1000.0
        init_neural_ms = float(neural_resp.get("latency_ms", 0.0)) if neural_resp else 0.0
        extra_neural_ms = max(0.0, neural_latency_ms - init_neural_ms)
        verifier_latency_ms = round(max(0.0, elapsed_after_initial_ms - extra_neural_ms), 2)
        total_latency_ms = round(init_neural_ms + elapsed_after_initial_ms, 2)

        return {
            "model": self.model_id,
            "neural_endpoint_active": neural_used,
            "acceptance_mode": acceptance_mode,
            "raw_neural_ir_verified": acceptance_mode == "raw_neural_ir",
            "symbolically_repaired_ir_verified": acceptance_mode == "symbolically_repaired_ir",
            "neural_ir_verified": acceptance_mode in ("raw_neural_ir", "symbolically_repaired_ir"),
            "transport": neural_resp.get("transport", "deterministic_verifier_fallback") if neural_resp else "deterministic_verifier_fallback",
            "n_candidates_evaluated": n_eval,
            "all_candidates_verified_count": n_ver,
            "selected_verifier_score": sel_score,
            "neural_reasoning_trace": neural_trace,
            "verified_reasoning_trace": ver_trace,
            "neural_latency_ms": round(neural_latency_ms, 2),
            "verifier_latency_ms": verifier_latency_ms,
            "latency_ms": total_latency_ms,
            "vram_allocated_mb": neural_resp.get("vram_allocated_mb", 0.0) if neural_resp else 0.0,
            "bundle": chosen_bundle,
            "ccl_program": chosen_ccl,
        }


# Backward-compatible alias
ApolloNeuralCompiler = LocalNeuralCompiler
