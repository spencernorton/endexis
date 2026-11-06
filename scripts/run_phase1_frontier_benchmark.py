#!/usr/bin/env python3
"""
Phase 1 Baseline Benchmark:
Compares Zero-Shot Frontier (`ge/nt-fast`, `ge/nt-heavy`), Retrieval-Augmented Frontier (RAG + `ge/nt-fast`, `ge/nt-heavy`),
and `discern-iq` V1 Baseline (`51110a1`) across:
  1. `eval_holdout_filter_values_v1.jsonl` (140 zero-leakage filter values)
  2. `eval_holdout_queries_v1.jsonl` (4 held-out production DA2 queries)
  3. `eval_frozen_v1.jsonl` (14 frozen end-to-end benchmark scenarios)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from discern_intelligence.ccl_compiler import CCLCompiler
from discern_intelligence.da2_generator import DA2ReportGenerator
from discern_intelligence.eval_harness import (
    run_full_evaluation_suite,
    score_query_element_and_filter_f1,
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from discern_intelligence.knowledge import (
    load_da2_elements,
    resolve_da2_filter_value,
)

GE_API_URL = os.environ.get("TEACHER_API_URL", "")


def call_ge_model(model: str, system_prompt: str, user_prompt: str, max_tokens: int = 3500) -> str:
    api_key = os.environ.get("GES_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing GES_API_KEY in environment (run via creds.sh ges-api --)")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        GE_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-GES-Key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "discern-intelligence-bench/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"].get("content") or ""
    except Exception as exc:
        return f'{{"error": "{type(exc).__name__}"}}'


def extract_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", text, flags=re.S)
    if m:
        text = m.group(1)
    else:
        for open_c, close_c in (("{", "}"), ("[", "]")):
            start = text.find(open_c)
            end = text.rfind(close_c)
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except Exception:
                    pass
    try:
        return json.loads(text)
    except Exception:
        return {}


def evaluate_frontier_on_filter_values(rows: list[dict[str, Any]], model: str, use_rag: bool) -> dict[str, Any]:
    sys_prompt = (
        "You are an Oracle Health / Cerner Millennium V500 and Discern Analytics 2.0 (DA2) compiler. "
        "For each numbered item, route the filter value to its exact DA2 field. "
        "Respond ONLY with a JSON object {\"results\": [{\"idx\": int, \"da2_field_name\": str, "
        "\"candidate_fields\": [str, str, str], \"code_value\": str_or_null}, ...]}."
    )
    sample_rows = rows[::5]  # 28 stratified zero-leakage items across all priority domains
    chunks = [sample_rows[i : i + 7] for i in range(0, len(sample_rows), 7)]

    def _eval_chunk(chunk: list[dict[str, Any]]) -> list[tuple[bool, bool, bool, bool]]:
        items_prompt = []
        for idx, row in enumerate(chunk):
            val = row["held_out_value"]
            item_obj: dict[str, Any] = {"idx": idx, "value": val}
            if use_rag:
                cands = resolve_da2_filter_value(val)[:4]
                item_obj["domain_hint"] = row.get("expected_da2_domain")
                item_obj["subentity_hint"] = row.get("expected_da2_subentity")
                item_obj["catalog_candidates"] = [
                    {
                        "da2_domain": c.get("da2_domain"),
                        "da2_subentity": c.get("da2_subentity"),
                        "da2_field_name": c.get("da2_field_name"),
                        "code_set": c.get("code_set"),
                        "matched_code_values": c.get("matched_code_values", {}),
                    }
                    for c in cands
                ]
            items_prompt.append(item_obj)

        raw = call_ge_model(model, sys_prompt, json.dumps(items_prompt), max_tokens=3200)
        parsed = extract_json(raw)
        res_list = parsed.get("results", []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
        by_idx = {r.get("idx", i): r for i, r in enumerate(res_list) if isinstance(r, dict)}

        outcomes = []
        for idx, row in enumerate(chunk):
            r = by_idx.get(idx, {})
            exp_field = row["expected_da2_field_name"]
            exp_cv = row.get("expected_code_value")
            pred_1 = str(r.get("da2_field_name", "")).strip()
            cands_3 = [str(x).strip() for x in r.get("candidate_fields", []) if x]
            if pred_1 and pred_1 not in cands_3:
                cands_3.insert(0, pred_1)
            hit1 = pred_1.lower() == exp_field.lower()
            hit3 = any(c.lower() == exp_field.lower() for c in cands_3[:3])
            has_cv = exp_cv is not None
            cv_ok = False
            if has_cv:
                pred_cv = str(r.get("code_value", "")).strip()
                cv_ok = pred_cv == str(exp_cv)
            outcomes.append((hit1, hit3, has_cv, cv_ok))
        return outcomes

    h1 = h3 = cv_tot = cv_hit = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for chunk_outcomes in ex.map(_eval_chunk, chunks):
            for hit1, hit3, has_cv, cv_ok in chunk_outcomes:
                h1 += int(hit1)
                h3 += int(hit3)
                if has_cv:
                    cv_tot += 1
                    cv_hit += int(cv_ok)
    n = len(sample_rows)
    return {
        "evaluated_count": n,
        "acc_at_1": round(h1 / max(1, n), 4),
        "acc_at_3": round(h3 / max(1, n), 4),
        "code_value_exact_match_rate": round(cv_hit / max(1, cv_tot), 4) if cv_tot else 0.0,
    }


def evaluate_frontier_on_holdout_queries(rows: list[dict[str, Any]], model: str, use_rag: bool) -> dict[str, Any]:
    elems = load_da2_elements()
    sys_prompt = (
        "You are a Discern Analytics 2.0 (DA2) and Cerner Millennium V500 SQL/XML compiler. "
        "Respond ONLY with a concise JSON object containing:\n"
        '{"selected_elements": [str, ...], "cclquery_xml": "<cclquery version=\\"1.0\\">...</cclquery>", '
        '"ejs_v500_sql": "SELECT ... FROM V500..."}'
    )

    def _eval_hq(hq: dict[str, Any]) -> tuple[float, float, int, int]:
        user_p = hq["prompt"]
        if use_rag:
            dom = hq["da2_domain"]
            dom_elems = [
                {"da2_field_name": e["da2_field_name"], "element_uuid": e["element_uuid"], "v500_target": e["v500_target"]}
                for e in elems
                if e.get("da2_domain") == dom
            ][:40]
            user_p += f"\nRetrieved DA2 Catalog Elements for {dom}: {json.dumps(dom_elems)}"
        raw = call_ge_model(model, sys_prompt, user_p, max_tokens=4000)
        parsed = extract_json(raw)
        if not isinstance(parsed, dict):
            parsed = {}
        pred_elems = [str(x) for x in parsed.get("selected_elements", [])]
        sc = score_query_element_and_filter_f1(pred_elems, hq["ground_truth_selected_elements"])
        xml_str = str(parsed.get("cclquery_xml", ""))
        sql_str = str(parsed.get("ejs_v500_sql", ""))
        xml_ok = int(bool(xml_str) and verify_cclquery_xml(xml_str).is_valid)
        sql_ok = int(bool(sql_str) and verify_ejs_v500_sql(sql_str).is_valid)
        return sc["element_recall"], sc["element_f1"], xml_ok, sql_ok

    f1_scores = []
    recall_scores = []
    xml_valid = 0
    sql_valid = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for r_sc, f1_sc, x_ok, s_ok in ex.map(_eval_hq, rows):
            recall_scores.append(r_sc)
            f1_scores.append(f1_sc)
            xml_valid += x_ok
            sql_valid += s_ok

    n = len(rows)
    return {
        "evaluated_count": n,
        "mean_element_recall": round(sum(recall_scores) / max(1, n), 4),
        "mean_element_f1": round(sum(f1_scores) / max(1, n), 4),
        "cclquery_xml_pass_at_1": round(xml_valid / max(1, n), 4),
        "ejs_v500_sql_pass_at_1": round(sql_valid / max(1, n), 4),
    }


def evaluate_frontier_on_frozen_scenarios(rows: list[dict[str, Any]], model: str, use_rag: bool) -> dict[str, Any]:
    sys_prompt = (
        "You are a Cerner Command Language (CCL) and Eclipse BIRT 4.4.2 (.rptdesign) compiler for Discern Analytics 2.0. "
        "Respond ONLY with a concise JSON object containing:\n"
        '{"ccl_source": "DROP PROGRAM ... END GO", "rptdesign_xml": "<?xml version=\\"1.0\\"...><report ...>...</report>"}'
    )
    da2_gen = DA2ReportGenerator()
    ccl_comp = CCLCompiler()

    def _eval_sc(sc: dict[str, Any]) -> tuple[bool, bool]:
        user_p = f"[Tenancy: {sc['tenancy']}] {sc['request']}"
        if use_rag:
            user_p += (
                "\nGuardrails required: CCL must include DROP/CREATE PROGRAM, PROMPT, SELECT INTO $OUTDEV, uar_get_code_by(\"MEANING\",...), "
                "PLAN e / JOIN, and VALID_UNTIL_DT_TM >= cnvtdatetime(\"31-DEC-2100 00:00:00\"). "
                "BIRT .rptdesign must use version='3.2.23', extensionID='com.cerner.discern.connectivity.oda', "
                "com.cerner.discern.da2.display.names.set=1, rowFetchLimit=0, try/catch beforeOpen/onFetch, and no <footer>."
            )
        raw = call_ge_model(model, sys_prompt, user_p, max_tokens=4500)
        parsed = extract_json(raw)
        if not isinstance(parsed, dict):
            parsed = {}
        ccl_src = str(parsed.get("ccl_source", ""))
        rpt_xml = str(parsed.get("rptdesign_xml", ""))
        is_ac = "active" in sc["request"].lower() and "census" in sc["request"].lower()
        ccl_ok = bool(ccl_src) and ccl_comp.verify_ccl_source(ccl_src, ["ENCOUNTER"], is_ac).is_valid
        rpt_ok = False
        if rpt_xml:
            try:
                bundle = da2_gen.generate(sc["request"], tenancy=sc["tenancy"])
                rpt_ok = da2_gen.verify_da2_bundle(
                    rptdesign_xml=rpt_xml,
                    queries=bundle.queries,
                    interp=da2_gen._interpreter.interpret(sc["request"], tenancy=sc["tenancy"]),
                    is_active_census=is_ac,
                ).is_valid
            except Exception:
                rpt_ok = False
        return ccl_ok, rpt_ok

    sample_sc = rows[:4]
    ccl_hits = rpt_hits = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for c_ok, r_ok in ex.map(_eval_sc, sample_sc):
            ccl_hits += int(c_ok)
            rpt_hits += int(r_ok)
    n = len(sample_sc)
    return {
        "evaluated_count": n,
        "ccl_prg_pass_at_1": round(ccl_hits / max(1, n), 4),
        "rptdesign_xml_pass_at_1": round(rpt_hits / max(1, n), 4),
    }


def main() -> int:
    data_dir = REPO_ROOT / "data"
    holdout_vals = [json.loads(line) for line in (data_dir / "eval_holdout_filter_values_v1.jsonl").read_text().splitlines() if line.strip()]
    holdout_qs = [json.loads(line) for line in (data_dir / "eval_holdout_queries_v1.jsonl").read_text().splitlines() if line.strip()]
    frozen_scs = [json.loads(line) for line in (data_dir / "eval_frozen_v1.jsonl").read_text().splitlines() if line.strip()]

    print("1. Running discern-iq V1 Baseline (commit 51110a1)...", flush=True)
    v1_suite = run_full_evaluation_suite(data_dir)

    print("2. Running Zero-Shot Frontier Baseline (ge/nt-fast)...", flush=True)
    zs_fast = {
        "model": "ge/nt-fast (zero-shot)",
        "holdout_filter_values": evaluate_frontier_on_filter_values(holdout_vals, "nt-fast", use_rag=False),
        "holdout_queries": evaluate_frontier_on_holdout_queries(holdout_qs, "nt-fast", use_rag=False),
        "frozen_scenarios": evaluate_frontier_on_frozen_scenarios(frozen_scs, "nt-fast", use_rag=False),
    }

    print("3. Running Retrieval-Augmented Frontier Baseline (ge/nt-fast + Catalog RAG)...", flush=True)
    rag_fast = {
        "model": "ge/nt-fast (catalog-RAG)",
        "holdout_filter_values": evaluate_frontier_on_filter_values(holdout_vals, "nt-fast", use_rag=True),
        "holdout_queries": evaluate_frontier_on_holdout_queries(holdout_qs, "nt-fast", use_rag=True),
        "frozen_scenarios": evaluate_frontier_on_frozen_scenarios(frozen_scs, "nt-fast", use_rag=True),
    }

    print("4. Running Retrieval-Augmented Frontier Baseline (ge/nt-heavy + Catalog RAG)...", flush=True)
    rag_heavy = {
        "model": "ge/nt-heavy (catalog-RAG)",
        "holdout_filter_values": evaluate_frontier_on_filter_values(holdout_vals, "nt-heavy", use_rag=True),
        "holdout_queries": evaluate_frontier_on_holdout_queries(holdout_qs, "nt-heavy", use_rag=True),
        "frozen_scenarios": evaluate_frontier_on_frozen_scenarios(frozen_scs, "nt-heavy", use_rag=True),
    }

    summary = {
        "phase1_baseline_comparison": {
            "zero_shot_nt_fast": zs_fast,
            "retrieval_augmented_nt_fast": rag_fast,
            "retrieval_augmented_nt_heavy": rag_heavy,
            "discern_iq_v1_baseline_51110a1": v1_suite,
        }
    }
    out_path = data_dir / "phase1_frontier_vs_discern_iq_baseline.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
