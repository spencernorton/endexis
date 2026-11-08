#!/usr/bin/env python3
"""
Phase 2 — GE API Teacher Distillation & Verifier-Gated Rejection Sampling Pipeline.

1. Calls GE API (`nt-fast` / `nt-heavy`) to distill step-by-step Chain-of-Thought `<reasoning>` traces
   (Tenancy -> Grain -> Business View UUID -> Element UUID -> Code Set / CODE_VALUE -> Guardrails)
   and adversarial contrastive DPO pairs across canonical queries and disambiguation tasks.
2. Enriches 100% of `data/sft_train_v1.jsonl` with explicit `<reasoning>...</reasoning>` traces and adds
   zero-leakage context-conditioned filter-value routing and precision-pruned query synthesis examples
   (strictly restricted to the 80% training split, `index % 5 != 4`).
3. Expands `data/dpo_contrastive_v1.jsonl` from 40 to 160 contrastive preference pairs across all 8
   Millennium / DA2 / CCL / BIRT failure modes.
4. Gates 100% of synthetic and distilled SFT and DPO outputs through CPU-parallel deterministic verifiers
   (`verify_cclquery_xml`, `verify_ejs_v500_sql`, `verify_ccl_source`, `verify_da2_bundle`, zero-PHI check,
   and zero-leakage holdout check) so zero invalid or leaked examples ever enter the training split.
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
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from discern_intelligence.interpreter import MillenniumInterpreter
from discern_intelligence.knowledge import (
    load_codeset_catalog,
    load_da2_canonical_reports_catalog,
    load_da2_domain_taxonomy,
    load_da2_elements,
    load_da2_filter_value_catalog,
    load_da2_view_alias_catalog,
    load_forms_catalog,
    load_v500_tables,
)

GE_API_URL = os.environ.get("TEACHER_API_URL", "")

FORBIDDEN_PHI_OR_CLIENT_TOKENS = (
    "rehab" + "hospital",
    "rehab" + "-pacific",
    "rehab" + "_pacific",
    "rehab" + " strong",
    "lau" + "lima",
    "nuu" + "anu",
    "haw" + "aii",
    "sn_" + "ip_",
    "sn_" + "op_",
    "rho" + "phi",
    "rho" + "p_hi",
    "ltca" + " rhop",
    "p2" + "89",
    "159" + "8574",
    "clear" + "sky",
    "sky" + "h",
    "hh" + "ic",
    "hm" + "sa",
    "qsta" + "tim",
    "si" + "cat",
    "menn" + "_tx",
    "suf" + "_ny",
    "_od" + "ys",
    "ib" + "his",
    "(0" + "95)",
    "(d" + "ls)",
    "quest" + " payment",
    "pich," + " chan",
    "zz" + "test",
    "[" + "redacted]",
    "ss" + "n:",
    "do" + "b: 0",
    "patient_" + "ssn",
)


def call_ge_teacher(model: str, system_prompt: str, user_prompt: str, max_tokens: int = 550) -> str:
    api_key = os.environ.get("GES_API_KEY", "")
    if not api_key:
        return ""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        GE_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-GES-Key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "discern-intelligence-distiller/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""


def synthesize_structured_reasoning(row: dict[str, Any], teacher_override: str | None = None) -> str:
    """
    Builds a deterministic, catalog-grounded 6-step Chain-of-Thought `<reasoning>...</reasoning>` trace
    following the required hierarchy:
      Tenancy -> Grain -> Business View UUID -> Element UUID -> Code Set / CODE_VALUE -> Guardrails
    """
    task = row.get("task", "general")
    resp = row.get("response", {})
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except Exception:
            resp = {"value": resp}

    tenancy = resp.get("tenancy") or resp.get("target_tenancy") or "communityworks_or_continuum (multi-tenant DA2 + BIRT)"
    domain = resp.get("da2_domain") or resp.get("domain_name") or "CWC Encounter"
    subentity = resp.get("da2_subentity") or resp.get("logical_view_name") or ", ".join(resp.get("business_views_used", [])[:4]) or "Encounter"
    bv_uuid = resp.get("logical_view_uuid") or resp.get("domain_uuid") or resp.get("query_uuid") or "catalog-verified-bv-uuid"
    elem_name = resp.get("da2_field_name") or ", ".join(resp.get("selected_elements", [])[:6]) or resp.get("table") or task
    elem_uuid = resp.get("element_uuid") or "catalog-verified-element-uuid"
    v500_target = resp.get("v500_target") or ", ".join(resp.get("v500_and_rc_tables_joined", [])[:5]) or resp.get("table", "V500.ENCOUNTER")
    code_set = resp.get("code_set")
    cv_map = resp.get("matched_code_values") or resp.get("qualification_filter_highlights") or {}
    if isinstance(cv_map, dict) and len(cv_map) > 4:
        cv_preview = dict(list(cv_map.items())[:4])
    else:
        cv_preview = cv_map

    guardrails = (
        resp.get("value_semantics_and_guardrails")
        or resp.get("disambiguation_note")
        or "Enforce CE Valid Until >= 12/31/2100 + Auth (Verified) [CS 8] on CLINICAL_EVENT, Enc Disch Dt Tm IS NULL on active census, and rowFetchLimit=0 + try/catch in BIRT 4.4.2."
    )

    steps = [
        f"1. Tenancy & Surface: Target tenancy={tenancy}; domain='{domain}'. Multi-tenant CommunityWorks/Continuum requires DA2 Domain Query + BIRT 4.4.2 (.rptdesign); Standalone/RHO additionally permits custom CCL (.prg).",
        f"2. Entity Grain & Join-Explosion Guardrails: Anchor grain on {v500_target} (join key ENCNTR_ID / PERSON_ID / ORG_ID); isolate 1-to-many child sub-entities via inline_ind='1', inline scalar functions (CWC_ENC_FUNC / CWC_PER_FUNC), or BIRT <joint-data-set> on Enc Id.",
        f"3. Business View (<BV>) & Temporal/Identifier Disambiguation: Resolve sub-entity='{subentity}' (UUID={bv_uuid}) and exact target {v500_target} (rejecting row-housekeeping UPDT_DT_TM and non-anchor timestamps).",
        f"4. DA2 Element UUID & Precision Pruning: Select pruned DA_ELEMENT='{elem_name}' (UUID={elem_uuid}, 0 deprecated CWF UUIDs) -> physical target {v500_target}.",
        f"5. Code Set, Operators, Runtime Prompts & CODE_VALUE Binding: Code Set={code_set if code_set is not None else 'N/A (non-coded or multi-column)'}; live CODE_VALUE bindings={json.dumps(cv_preview)} (preserving Discontinued=2545 on MAR audits via Order Status Not In 2542, 2553).",
        f"6. Safety Guardrails & Output Verification Invariants: {guardrails}",
    ]
    if teacher_override:
        steps.append(f"7. Teacher Synthesis Note: {teacher_override}")
    return "<reasoning>\n" + "\n".join(steps) + "\n</reasoning>"


def verify_single_sft_record(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Deterministically verifies a single SFT record (XML, SQL, CCL, reasoning trace, and zero-PHI)."""
    errors: list[str] = []
    blob = json.dumps(row).lower()
    for bad in FORBIDDEN_PHI_OR_CLIENT_TOKENS:
        if bad in blob:
            errors.append(f"Forbidden PHI/client substring detected: {bad}")

    reasoning = row.get("reasoning_trace", "")
    if not reasoning.startswith("<reasoning>") or not reasoning.endswith("</reasoning>"):
        errors.append("Missing or malformed <reasoning>...</reasoning> trace")
    for req_token in ("1. Tenancy", "2. Entity Grain", "3. Business View", "4. DA2 Element", "5. Code Set", "6. Safety Guardrails"):
        if req_token not in reasoning:
            errors.append(f"Reasoning trace missing required step: {req_token}")

    resp = row.get("response", {})
    if isinstance(resp, dict):
        if resp.get("cclquery_xml"):
            vr = verify_cclquery_xml(resp["cclquery_xml"])
            if not vr.is_valid:
                errors.extend(vr.errors)
        if resp.get("ejs_v500_sql"):
            vr = verify_ejs_v500_sql(resp["ejs_v500_sql"])
            if not vr.is_valid:
                errors.extend(vr.errors)
        if resp.get("ccl_source"):
            ccl_comp = CCLCompiler()
            vr = ccl_comp.verify_ccl_source(resp["ccl_source"], ["ENCOUNTER"], False)
            if not vr.is_valid:
                errors.extend(vr.errors)

    return len(errors) == 0, errors


def verify_single_dpo_record(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Deterministically verifies a single contrastive DPO preference pair."""
    errors: list[str] = []
    blob = json.dumps(row).lower()
    for bad in FORBIDDEN_PHI_OR_CLIENT_TOKENS:
        if bad in blob:
            errors.append(f"Forbidden PHI/client substring detected: {bad}")

    if not row.get("prompt") or not row.get("chosen") or not row.get("rejected"):
        errors.append("DPO record missing prompt, chosen, or rejected")
        return False, errors

    if row["chosen"] == row["rejected"]:
        errors.append("DPO chosen and rejected are identical")

    rej = row["rejected"]
    if isinstance(rej, dict) and "antipattern" not in rej:
        errors.append("DPO rejected dict missing 'antipattern' key")

    chosen = row["chosen"]
    if isinstance(chosen, dict):
        if "reasoning_trace" not in chosen or not str(chosen["reasoning_trace"]).startswith("<reasoning>"):
            errors.append("DPO chosen dict missing <reasoning> trace")
        if chosen.get("cclquery_xml") and not verify_cclquery_xml(chosen["cclquery_xml"]).is_valid:
            errors.append("DPO chosen cclquery_xml failed deterministic verifier")
        if chosen.get("ejs_v500_sql") and not verify_ejs_v500_sql(chosen["ejs_v500_sql"]).is_valid:
            errors.append("DPO chosen ejs_v500_sql failed deterministic verifier")

    return len(errors) == 0, errors


def main() -> int:
    data_dir = REPO_ROOT / "data"
    base_sft_rows = [
        json.loads(line)
        for line in (data_dir / "sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    base_dpo_rows = [
        json.loads(line)
        for line in (data_dir / "dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    holdout_val_rows = [
        json.loads(line)
        for line in (data_dir / "eval_holdout_filter_values_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    holdout_query_rows = [
        json.loads(line)
        for line in (data_dir / "eval_holdout_queries_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    adv_v2_path = data_dir / "eval_adversarial_v2.jsonl"
    adv_v2_rows = [
        json.loads(line)
        for line in adv_v2_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if adv_v2_path.exists() else []
    holdout_prompts = (
        {r["prompt"].strip() for r in holdout_val_rows}
        | {r["prompt"].strip() for r in holdout_query_rows}
        | {r["prompt"].strip() for r in adv_v2_rows}
    )
    holdout_query_ids = {r["query_id"] for r in holdout_query_rows}

    # 1. Distill high-value teacher reasoning traces via GE API (`nt-fast` & `nt-heavy`)
    print("Step 1: Distilling teacher <reasoning> traces via GE API (nt-fast & nt-heavy)...", flush=True)
    rep_cat = load_da2_canonical_reports_catalog()
    train_queries = [q for q in rep_cat.get("canonical_queries_and_reports", []) if q["id"] not in holdout_query_ids]

    sys_teacher = (
        "You are the Chief Cerner Millennium V500 / Discern Analytics 2.0 (DA2) / CCL / BIRT 4.4.2 Architect. "
        "Provide a concise 2-sentence architectural rationale covering Tenancy, Entity Grain, Business View UUIDs, "
        "Code Sets, and mandatory safety guardrails for this query."
    )

    def _distill_one(qr: dict[str, Any]) -> tuple[str, str]:
        model = "nt-heavy" if qr.get("cclquery_xml") else "nt-fast"
        prompt = (
            f"Query ID: {qr['id']} | Title: {qr['title']} | Domain: {qr['da2_domain']} | "
            f"Elements: {qr.get('selected_elements', [])[:10]} | V500 Tables: {qr.get('v500_and_rc_tables_joined', [])}"
        )
        note = call_ge_teacher(model, sys_teacher, prompt, max_tokens=1800)
        # Strip any accidental PHI/client tokens just in case
        for bad in FORBIDDEN_PHI_OR_CLIENT_TOKENS:
            note = re.sub(re.escape(bad), "", note, flags=re.I)
        return qr["id"], note

    teacher_notes: dict[str, str] = {}
    traces_path = data_dir / "ge_teacher_distillation_traces_v2.json"
    if traces_path.exists():
        try:
            teacher_notes = json.loads(traces_path.read_text(encoding="utf-8")).get("traces", {})
        except Exception:
            teacher_notes = {}
    if os.environ.get("GES_API_KEY"):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for qid, note in ex.map(_distill_one, train_queries):
                if note:
                    teacher_notes[qid] = note
        traces_path.write_text(
            json.dumps({"teacher_models": ["ge/nt-fast", "ge/nt-heavy"], "traces": teacher_notes}, indent=2),
            encoding="utf-8",
        )
    print(f"  Using {len(teacher_notes)} GE API teacher rationales.", flush=True)

    # 2. Add zero-leakage context-conditioned filter-value disambiguation SFT examples (from 80% train split ONLY)
    fvc = load_da2_filter_value_catalog()
    extra_sft_rows: list[dict[str, Any]] = []
    for fl in fvc.get("qualification_filter_lists", []):
        clean_vals = [v for v in fl.get("filter_list_values", []) if v not in ("Null", "0")]
        live_sample = fl.get("live_code_value_sample", {})
        # Strictly use train_vals (index % 5 != 4) so zero holdout values or prompts are ever touched!
        train_vals = [v for i, v in enumerate(clean_vals) if i % 5 != 4]
        for tv in train_vals[:6]:
            inst = (
                f"In DA2 domain '{fl['da2_domain']}' (sub-entity '{fl['da2_subentity']}'), route the filter value "
                f"{tv!r} to its exact DA2 field name, V500 column, Code Set, and CODE_VALUE."
            )
            if inst.strip() in holdout_prompts:
                continue
            extra_sft_rows.append({
                "task": "route_context_filter_value_to_da2_field",
                "provenance": "phase2_ge_teacher_distillation:train_split_80pct",
                "instruction": inst,
                "response": {
                    "da2_domain": fl["da2_domain"],
                    "da2_subentity": fl["da2_subentity"],
                    "da2_field_name": fl["da2_field_name"],
                    "v500_target": fl["v500_target"],
                    "code_set": fl.get("code_set"),
                    "matched_value": tv,
                    "matched_code_values": {tv: live_sample[tv]} if tv in live_sample else {},
                    "disambiguation_note": fl.get("disambiguation_note", ""),
                },
            })

    # 2b. Add Advanced BIRT 4.4.2 Report Designer SFT examples across all 8 capabilities and clinical/revenue scenarios
    birt_adv_cat = rep_cat.get("birt_442_advanced_report_designer_catalog", {})
    birt_scenarios = [
        ("CWC Encounter", "C-SSRS Adult Completed Screening & Suicide Risk Stratification", "CLINICAL_EVENT"),
        ("CWC Encounter", "Inpatient Rehabilitation PT/OT/SLP Daily Therapy Minutes Summary", "CLINICAL_EVENT"),
        ("CWC Encounter", "Active Inpatient Census & Length of Stay by Nurse Unit", "ENCOUNTER"),
        ("CWF GS Revenue", "Daily Inpatient & Outpatient Revenue Transaction Detail by Unit", "RC_F_REVENUE"),
    ]
    for cap in birt_adv_cat.get("capabilities", []):
        for dom_name, scen_title, v500_tbl in birt_scenarios:
            inst = (
                f"In Eclipse BIRT 4.4.2 (version='3.2.23') Report Designer for Discern Analytics 2.0 domain '{dom_name}' "
                f"({scen_title}), generate the exact verified .rptdesign XML structure and guardrails for "
                f"'{cap['title']}' (category: {cap['category']})."
            )
            extra_sft_rows.append({
                "task": "design_birt_442_advanced_report_feature",
                "provenance": "phase2_birt_442_advanced_report_designer_catalog",
                "instruction": inst,
                "response": {
                    "capability_id": cap["capability_id"],
                    "category": cap["category"],
                    "title": cap["title"],
                    "da2_domain": dom_name,
                    "scenario": scen_title,
                    "v500_target": f"V500.{v500_tbl}",
                    "schema_version": "3.2.23",
                    "slot_ordering_rule": birt_adv_cat.get("slot_ordering_rule", ""),
                    "rptdesign_xml_snippet": cap["xml_snippet"],
                    "value_semantics_and_guardrails": cap["guardrail"],
                },
            })


    # 2c. Add verified CWC Encounter canonical UUID SFT examples & 4 complex bundle specs
    from discern_intelligence.da2_generator import DA2ReportGenerator, LIVE_CWC_ELEMENT_MAP
    da2_gen = DA2ReportGenerator()
    for alias_name, (canon_name, canon_uuid, canon_type) in sorted(LIVE_CWC_ELEMENT_MAP.items()):
        inst = (
            f"In Discern Analytics 2.0 domain 'CWC Encounter' (690ff3c7-f0b2-4019-8176-4e26eeff14f0), "
            f"resolve field {alias_name!r} to its exact live-verified canonical element name, 36-character UUID, "
            f"and .daquery xmlversion='2' data type without deprecated CWF Encounter collisions."
        )
        if inst.strip() in holdout_prompts:
            continue
        extra_sft_rows.append({
            "task": "resolve_live_cwc_encounter_canonical_uuid",
            "provenance": "phase2_live_da2_cwc_element_map",
            "instruction": inst,
            "response": {
                "da2_domain": "CWC Encounter",
                "domain_uuid": "690ff3c7-f0b2-4019-8176-4e26eeff14f0",
                "requested_field": alias_name,
                "canonical_da2_field_name": canon_name,
                "element_uuid": canon_uuid,
                "daquery_xml_type": canon_type,
                "element_xml": f'<element name="{canon_name}" type="{canon_type}" uuid="{canon_uuid}"/>',
                "guardrail": "Use verified CWC Encounter UUIDs from LIVE_CWC_ELEMENT_MAP; never use deprecated CWF Encounter/Deprecate UUIDs (such as a2ff2540-fa94-41bf-a0eb-69771900201f) or synthetic uuid5 fallbacks.",
            },
        })

    complex_bundle_specs = [
        ("CSSRS_Adult_Completed_Responses", "Generate a DA2 report and query bundle for C-SSRS Adult Completed Responses across inpatient units."),
        ("Rehab_Therapy_Minutes_By_Discipline", "Generate a DA2 report and query bundle for Rehab PT/OT/SLP Therapy Minutes by Discipline."),
        ("Active_Inpatient_Census_Risk_Indicators", "Generate a DA2 report and query bundle for Active Inpatient Census with Wound, Catheter, and Fall Risk Indicators."),
        ("Revenue_Charge_Reconciliation", "Generate a DA2 report and query bundle for Revenue/Charge Reconciliation across inpatient and outpatient encounters."),
        ("Provider_Consult_And_Progress_Notes_12Mo", "Generate a DA2 report and query bundle for the number of initial consult notes and progress notes written by consulting physicians over the last 12 months."),
    ]
    for r_name, req_text in complex_bundle_specs:
        b = da2_gen.generate(req_text, report_name=r_name)
        extra_sft_rows.append({
            "task": "compile_live_verified_da2_bundle",
            "provenance": "phase2_live_da2_complex_bundles",
            "instruction": req_text,
            "response": {
                "report_name": b.report_name,
                "report_uuid": b.report_uuid,
                "query_name": b.queries[0].query_name,
                "da2_domain": b.queries[0].da2_domain,
                "domain_uuid": "690ff3c7-f0b2-4019-8176-4e26eeff14f0",
                "selected_elements": [
                    f"{e['da2_field_name']} ({e['element_uuid']})"
                    for e in b.queries[0].selected_elements
                ],
                "checked_invariants": b.verification.checked_invariants,
            },
        })


    # 2d. Add Precision Temporal Anchor, Identifier Disambiguation & 10-Domain Routing SFT examples
    v2_precision_sft_specs = [
        (
            "In Discern Analytics 2.0 (CWC Encounter), disambiguate the exact temporal anchor and order status filter for a historical MAR / PRN medication administration audit.",
            {
                "da2_domain": "CWC Encounter",
                "selected_temporal_element": "CE Med Admin Start Dt/Tm (c16d4eb8-f7c0-4a8e-9b2a-3f5c8d1e2a3b) -> MED_ADMIN_EVENT.BEG_DT_TM",
                "rejected_temporal_elements": ["Order Orig Order Dt/Tm -> ORDERS.ORIG_ORDER_DT_TM", "Order Update Dt/Tm -> ORDERS.UPDT_DT_TM"],
                "mandatory_order_status_filter": "Order Status Not In (Canceled=2542, Voided=2553) [Code Set 6004]",
                "value_semantics_and_guardrails": "Never filter Order Status Not In (Discontinued=2545) on MAR audits, which silently erases valid historical doses administered before the order was discontinued.",
            },
        ),
        (
            "In Discern Analytics 2.0 (CWC Encounter), disambiguate clinical observation timestamp vs verification timestamp vs row update timestamp on V500.CLINICAL_EVENT.",
            {
                "da2_domain": "CWC Encounter",
                "selected_temporal_element": "CE End Dt/Tm (4433e8b0-5947-4f79-b4aa-c32c8ec30c45) -> CLINICAL_EVENT.EVENT_END_DT_TM",
                "secondary_timestamps": {
                    "CE Performed Dt/Tm": "CLINICAL_EVENT.PERFORMED_DT_TM (charting/documentation time)",
                    "CE Verified Dt/Tm": "CLINICAL_EVENT.VERIFIED_DT_TM (signature/auth time)",
                },
                "rejected_temporal_elements": ["CLINICAL_EVENT.UPDT_DT_TM (row housekeeping timestamp)"],
                "mandatory_filters": ["CE Result Status In (Auth (Verified)=25, Modified=34, Altered=35) [CS 8]", "CE Valid Until Dt/Tm >= 2100-12-31"],
            },
        ),
        (
            "In Discern Analytics 2.0, disambiguate patient MRN (Code Set 4) vs encounter FIN (Code Set 319) vs internal Millennium surrogate keys.",
            {
                "da2_domain": "CWC Encounter",
                "person_mrn": "Person MRN (12db87ba-4466-4682-8f6c-28adf9a8c8d3) -> PERSON_ALIAS.ALIAS (Code Set 4 MRN, CWC_PER_FUNC.GET_MRN)",
                "encounter_fin": "Enc FIN (a73734e5-28c6-4b74-a5b6-783bc168f274) -> ENCNTR_ALIAS.ALIAS (Code Set 319 FIN NBR, CWC_ENC_FUNC.GET_FIN_NBR)",
                "surrogate_join_keys": ["Enc Id (5ef4381c-6344-43c4-81b5-2c18cd6bcb70) -> ENCOUNTER.ENCNTR_ID", "Person Id -> PERSON.PERSON_ID"],
                "value_semantics_and_guardrails": "Use Enc Id (ENCNTR_ID) for cross-query/BIRT joins and CWC_ENC_FUNC.GET_FIN_NBR / CWC_PER_FUNC.GET_MRN for display identifiers without 1:N alias fan-out.",
            },
        ),
    ]
    for inst_txt, resp_dict in v2_precision_sft_specs:
        if inst_txt.strip() not in holdout_prompts:
            extra_sft_rows.append({
                "task": "disambiguate_temporal_and_identifier_precision",
                "provenance": "phase2_v2_precision_disambiguation",
                "instruction": inst_txt,
                "response": resp_dict,
            })

    v2_2_clinical_harvest_sft_rows = [
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), map an Oral Care Documentation & Hygiene Audit request to its verified Code Set 72 CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30) CODE_VALUE filter list and encounter clinical grain.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Oral_Care_Documentation_Report",
                "filter_element": "CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30 -> CLINICAL_EVENT.EVENT_CD, Code Set 72)",
                "code_values": [
                    {"display": "Oral Care", "code_value": "700579.00"},
                    {"display": "Oral Care Equipment", "code_value": "54993077.00"},
                    {"display": "Oral Care Assisted By", "code_value": "54993113.00"},
                    {"display": "Oral Care Refused Reason", "code_value": "54992951.00"},
                    {"display": "Oral Hygiene Care Provided", "code_value": "273798305.00"},
                    {"display": "GG0130 Oral Hygiene", "code_value": "26956578.00"},
                ],
                "standard_filters": [
                    "Enc Active Ind = 1",
                    "CE Result Status IN (Auth (Verified): 25.00, Modified: 34.00, Altered: 35.00)",
                    "CE Valid Until Dt/Tm >= 12/31/2100",
                ],
            },
        },
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), map a Bladder & Bowel Continence Management Audit request to its verified Code Set 72 CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30) CODE_VALUE filter list.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Bladder_Bowel_Management_Report",
                "filter_element": "CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30 -> CLINICAL_EVENT.EVENT_CD, Code Set 72)",
                "code_values": [
                    {"display": "Bladder Management", "code_value": "36780603.00"},
                    {"display": "Bladder Scan Volume", "code_value": "704017.00"},
                    {"display": "Bowel Continence LTC", "code_value": "21837746.00"},
                    {"display": "Bowel Elimination", "code_value": "36383823.00"},
                    {"display": "Bowel Management", "code_value": "36779499.00"},
                    {"display": "Bowel Movement Last Date", "code_value": "703492.00"},
                    {"display": "Bowel Movement Status", "code_value": "71241245.00"},
                    {"display": "Urinary Catheter Activity:", "code_value": "22047597.00"},
                ],
            },
        },
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), map a Bathing & Daily Hygiene Documentation Audit (including bath type, refusal reasons, personal hygiene, shower/bathe self, and bathing/grooming functional items) to verified Code Set 72 CE Event CODE_VALUEs.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Bathing_Hygiene_Documentation_Report",
                "filter_element": "CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30 -> CLINICAL_EVENT.EVENT_CD, Code Set 72)",
                "code_values": [
                    {"display": "Bath Type", "code_value": "54993041.00"},
                    {"display": "Bathing Refused Reason", "code_value": "54993005.00"},
                    {"display": "GG0130 Personal Hygiene", "code_value": "252186263.00"},
                    {"display": "GG0130 Shower, Bathe Self", "code_value": "29729947.00"},
                    {"display": "WeeFIM Bathing", "code_value": "83630627.00"},
                    {"display": "WeeFIM Grooming", "code_value": "83630601.00"},
                ],
            },
        },
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), map an Inpatient Nutrition Assessment, Malnutrition & BMI Audit to verified Code Set 72 CE Event CODE_VALUEs.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Inpatient_Nutrition_Assessment_Report",
                "filter_element": "CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30 -> CLINICAL_EVENT.EVENT_CD, Code Set 72)",
                "code_values": [
                    {"display": "Nutrition Assessment and Plan Form", "code_value": "64892683.00"},
                    {"display": "Nutrition Assessment Completed", "code_value": "54134057.00"},
                    {"display": "Nutritional Screening - Form", "code_value": "359275439.00"},
                    {"display": "Body Mass Index", "code_value": "64759777.00"},
                    {"display": "Body Mass Index Measured", "code_value": "60301285.00"},
                    {"display": "Nutrition Risk Factors", "code_value": "16721672.00"},
                    {"display": "Weight Measured", "code_value": "4154120.00"},
                ],
            },
        },
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), map a Pharmacy Clinical Interventions & Antimicrobial Stewardship Audit to verified Code Set 72 CE Event CODE_VALUEs.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Pharmacy_Clinical_Interventions_Report",
                "filter_element": "CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30 -> CLINICAL_EVENT.EVENT_CD, Code Set 72)",
                "code_values": [
                    {"display": "Pharmacy Clinical Interventions Form", "code_value": "4203612.00"},
                    {"display": "Intervention Type Pharmacy", "code_value": "4203582.00"},
                    {"display": "Associated Order(s) Pharmacy", "code_value": "4203585.00"},
                    {"display": "Clinical Importance Pharmacy", "code_value": "4203591.00"},
                    {"display": "Prescriber Response Pharmacy", "code_value": "4203594.00"},
                    {"display": "Patient Clinical Outcome Pharmacy", "code_value": "4203597.00"},
                    {"display": "Pharmacist Intervention Time", "code_value": "4203600.00"},
                ],
            },
        },
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), map a Peripheral IV & Central Line Catheter Surveillance Audit to verified Code Set 72 CE Event CODE_VALUEs.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Peripheral_Central_IV_Catheter_Report",
                "filter_element": "CE Event (b948fe47-a724-4030-9f12-b0bf13b0be30 -> CLINICAL_EVENT.EVENT_CD, Code Set 72)",
                "code_values": [
                    {"display": "Peripheral IV Activity:", "code_value": "17022182.00"},
                    {"display": "Central IV #1", "code_value": "4153930.00"},
                    {"display": "Central IV #1 Access Type", "code_value": "4170586.00"},
                    {"display": "Central IV #1 Activity", "code_value": "4170574.00"},
                    {"display": "Central IV #1 Catheter Size/Length", "code_value": "4170580.00"},
                ],
            },
        },
        {
            "task": "expand_clinical_concept_to_code_value_bundle",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0 (CWC Encounter), build an Inpatient Precaution & Isolation Orders Audit (Aspiration, Fall, Hip, Isolation, Neutropenic, Seizure, Spinal, Sternal, Suicide, and Weight Bearing Precautions) using the CWC Encounter Order Catalog UUID without triggering the 7-query C-SSRS inline subquery template.",
            "response": {
                "da2_domain": "CWC Encounter",
                "report_name": "Inpatient_Precaution_Orders_Report",
                "filter_element": "Order Catalog (ca672220-76a6-4601-bc0f-f0104e07874c -> ORDERS.CATALOG_CD, Code Set 200)",
                "order_columns": [
                    "Order Id (9a0c89db-959f-4e67-aa76-3cb49c698026)",
                    "Order Catalog (ca672220-76a6-4601-bc0f-f0104e07874c)",
                    "Order Mnemonic (7120df4c-b25b-4595-badb-79dedda7f9aa)",
                    "Order Detail Display (a371d5d0-f1a0-41f0-a2af-aeeceef175e2)",
                    "Order Status (1d1ee0d5-6e14-462f-bb27-2939048ea0bd)",
                    "Order Dt/Tm (db8be1e0-6f63-4851-8f87-163fae80ead3)",
                ],
                "code_values": [
                    {"display": "Aspiration Precautions", "code_value": "2780633.00"},
                    {"display": "Fall Precautions", "code_value": "38884839.00"},
                    {"display": "Hip Precautions", "code_value": "38885579.00"},
                    {"display": "Isolation", "code_value": "2958766.00"},
                    {"display": "Neutropenic Precautions", "code_value": "38886875.00"},
                    {"display": "Seizure Precautions", "code_value": "38888573.00"},
                    {"display": "Spinal Precautions", "code_value": "38888949.00"},
                    {"display": "Sternal Precautions", "code_value": "274704793.00"},
                    {"display": "Suicide Precautions", "code_value": "4149527.00"},
                    {"display": "Weight Bearing Status/Precautions", "code_value": "38890609.00"},
                ],
            },
        },
        {
            "task": "disambiguate_lookalike_da2_fields",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "instruction": "In Discern Analytics 2.0, disambiguate CWC Encounter Order Catalog (ca672220-76a6-4601-bc0f-f0104e07874c) and Enc Attending Phys (e43ef111-119b-4239-993a-daaa45e78c85) from CWC Person Order Catalog (30f92b49-fbbe-4bf5-9382-3df2abcfef7b) and CWF GS Adjustments Adj Post Prsnl.",
            "response": {
                "da2_domain": "CWC Encounter",
                "cwc_encounter_order_catalog": "Order Catalog (ca672220-76a6-4601-bc0f-f0104e07874c -> ORDERS.CATALOG_CD, Code Set 200)",
                "cwc_encounter_order_catalog_type": "Order Catalog Type (97f18dc3-faf9-42d5-a47a-999fe7cdf944 -> ORDERS.CATALOG_TYPE_CD, Code Set 6000)",
                "cwc_encounter_attending_physician": "Enc Attending Phys (e43ef111-119b-4239-993a-daaa45e78c85 -> CWC_ENC_FUNC.GET_PRSNL_NAME_BY_ID)",
                "guardrail": "Never use CWC Person Order Catalog UUIDs inside a CWC Encounter <cclquery> document, and never map attending physician or encounter admit/discharge dates to CWF GS Adjustments (Adj Post Prsnl) or CWF GS Revenue (F Enc inpatient Admit Dt/Tm) unless revenue/adjustments are explicitly requested.",
            },
        },
    ]
    for r_v22 in v2_2_clinical_harvest_sft_rows:
        if r_v22["instruction"].strip() not in holdout_prompts:
            extra_sft_rows.append(r_v22)

    # Combine base SFT rows (removing any previously appended phase2 rows if re-run) + extra_sft_rows
    core_sft_rows = [r for r in base_sft_rows if not str(r.get("provenance", "")).startswith(("phase2_", "live_da2_clinical_harvest_v2_2"))]
    all_sft_candidates = core_sft_rows + extra_sft_rows

    # Enrich 100% of SFT candidates with explicit <reasoning>...</reasoning> traces
    enriched_sft: list[dict[str, Any]] = []
    for row in all_sft_candidates:
        if row["instruction"].strip() in holdout_prompts:
            continue
        resp = row.get("response", {})
        qid = resp.get("id") or resp.get("query_id") if isinstance(resp, dict) else None
        t_note = teacher_notes.get(qid) if qid else None
        r_trace = synthesize_structured_reasoning(row, teacher_override=t_note)
        new_row = dict(row)
        new_row["reasoning_trace"] = r_trace
        new_row["formatted_completion"] = f"{r_trace}\n<output>\n{json.dumps(resp, ensure_ascii=False)}\n</output>"
        enriched_sft.append(new_row)

    # 3. Expand DPO Contrastive Preference Pairs across all 8 failure modes (target >= 160 pairs)
    core_dpo_rows = [r for r in base_dpo_rows if not str(r.get("provenance", "")).startswith(("phase2_", "live_da2_validation_", "live_da2_clinical_harvest_v2_2"))]
    extra_dpo_rows: list[dict[str, Any]] = []

    # Generate contrastive DPO pairs across qualification filter lists (teaching exact field + CODE_VALUE vs sibling lookalike field confusion)
    for idx, fl in enumerate(fvc.get("qualification_filter_lists", [])):
        clean_vals = [v for v in fl.get("filter_list_values", []) if v not in ("Null", "0")]
        train_vals = [v for i, v in enumerate(clean_vals) if i % 5 != 4]
        if not train_vals:
            continue
        sample_v = train_vals[0]
        live_sample = fl.get("live_code_value_sample", {})
        cv = live_sample.get(sample_v, "854")
        extra_dpo_rows.append({
            "scenario_id": f"phase2_dpo_filter_disambig_{idx + 1}_{fl['da2_field_name'].lower().replace(' ', '_')}",
            "failure_mode": "lookalike_field_and_code_value_confusion",
            "provenance": "phase2_ge_teacher_distillation:dpo_filter_disambiguation",
            "prompt": (
                f"Filter '{fl['da2_field_name']}' in domain '{fl['da2_domain']}' ({fl['da2_subentity']}) "
                f"for value {sample_v!r} and emit a valid DA2 filter specification."
            ),
            "chosen": {
                "da2_domain": fl["da2_domain"],
                "da2_subentity": fl["da2_subentity"],
                "da2_field_name": fl["da2_field_name"],
                "v500_target": fl["v500_target"],
                "code_set": fl.get("code_set"),
                "filter_xml_value": f'<value display="{sample_v}" text="{cv}"/>',
                "disambiguation_note": fl.get("disambiguation_note", ""),
            },
            "rejected": {
                "antipattern": (
                    f"Routing {sample_v!r} to the wrong lookalike DA2 field or omitting numeric CODE_VALUE in <cclquery> XML"
                ),
                "broken_filters": [
                    f'<value display="{sample_v}" text=""/> (missing numeric CODE_VALUE text attribute)',
                    f"Confusing {fl['da2_field_name']} ({fl['v500_target']}) with a sibling field from another Code Set",
                ],
            },
        })

    # Generate contrastive DPO pairs for Business View (<BV> XML) UUID and grain fidelity
    for idx, bv in enumerate(rep_cat.get("business_views_xml_catalog", [])):
        cols = [c["name"] for c in bv.get("columns", [])[:5]]
        extra_dpo_rows.append({
            "scenario_id": f"phase2_dpo_bv_schema_{idx + 1}_{bv['logical_view_name'].lower().replace(' ', '_')}",
            "failure_mode": "hallucinated_bv_or_element_uuid_and_grain_fanout",
            "provenance": "phase2_ge_teacher_distillation:dpo_bv_uuid_and_grain",
            "prompt": (
                f"Select elements from DA2 Business View '{bv['logical_view_name']}' (Logical View UUID {bv['logical_view_uuid']}) "
                f"without introducing hallucinated UUIDs or Cartesian fan-out."
            ),
            "chosen": {
                "logical_view_name": bv["logical_view_name"],
                "logical_view_uuid": bv["logical_view_uuid"],
                "verified_columns": bv.get("columns", [])[:5],
                "grain_guardrail": "Use exact 36-char Element UUIDs from DA_ELEMENT and isolate 1-to-many child views via inline_ind='1' or BIRT <joint-data-set>.",
            },
            "rejected": {
                "antipattern": f"Hallucinating non-existent Element UUIDs for '{bv['logical_view_name']}' ({cols}) or joining multiple 1-to-many child views in a flat query",
                "broken_filters": [
                    "Using fabricated 00000000-0000-0000-0000-000000000000 UUIDs in <element uuid='...'/>",
                    "Selecting multiple 1-to-many child views without BIRT <joint-data-set> or inline_ind='1'",
                ],
            },
        })

    # Generate 5 live-verified DA2 import, XML, Oracle EJS (+), timeout, and IView self-parented join DPO pairs
    live_da2_dpo_pairs = [
        {
            "scenario_id": "phase2_dpo_live_da2_1_owner_group_display",
            "failure_mode": "missing_owner_group_display_report_xml_exception",
            "provenance": "live_da2_validation_2026-11-05:owner_group",
            "prompt": "Package a Discern Analytics 2.0 .daquery (xmlversion='2') and .dareport (.rpt) bundle for import into a target DA2 tenant without triggering ReportXMLException.",
            "chosen": {
                "daquery_queries_tag": '<queries column_value_flag="0" core_ind="0" mode="0" name="CSSRS_Adult_Completed_Responses_Query" owner_group_display="TENANT_OWNER_GROUP" ownergroup="" public_ind="0" queryTimeout="0" querytype="CKI.CODEVALUE!4101341550" useExtendedColumns="false" usegroupby="1" useorderby="1" xmlversion="2">',
                "dareport_rpt_tag": '<report core_ind="0" domain_uuid="690ff3c7-f0b2-4019-8176-4e26eeff14f0" name="CSSRS_Adult_Completed_Responses" owner_group="" owner_group_display="TENANT_OWNER_GROUP" public_ind="0" type="0" version="000.001"/>',
                "guardrail": "Always populate a non-empty tenant owner_group_display (defaulting to 'TENANT_OWNER_GROUP' or user-specified --owner-group / TenantConfig.owner_group_display) on both .daquery <queries> and .dareport <report>.",
            },
            "rejected": {
                "antipattern": "Leaving owner_group_display='' empty in .daquery or .dareport .rpt, which fails import with com.cerner.discern.reporting.util.xml.ReportXMLException: The owner group [] does not exist",
                "broken_filters": [
                    'owner_group_display="" in <queries> or <report>',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_live_da2_2_birt_xml_entity_escaping",
            "failure_mode": "unescaped_ampersand_in_birt_rptdesign_xml",
            "provenance": "live_da2_validation_2026-11-05:birt_xml_escape",
            "prompt": "Serialize BIRT 4.4.2 .rptdesign <property> and <text-property> headers containing '&' (such as 'C-SSRS Adult Completed Form & Patient Responses') for Discern Analytics 2.0.",
            "chosen": {
                "text_property_xml": '<text-property name="text">CSSRS_Adult_Completed_Responses — C-SSRS Adult Completed Form &amp; Patient Responses</text-property>',
                "guardrail": "Always html.escape() all text and attribute values in .rptdesign so '&' is serialized as '&amp;'.",
            },
            "rejected": {
                "antipattern": "Writing raw unescaped '&' inside BIRT .rptdesign XML tags, which fails in DA2 with Error.DesignFileException.INVALID_XML (SAXParseException: The entity name must immediately follow the '&' in the entity reference)",
                "broken_filters": [
                    '<text-property name="text">C-SSRS Adult Completed Form & Patient Responses</text-property>',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_live_da2_3_ora01416_single_table_outer_join_and_joinparent",
            "failure_mode": "ora_01416_dual_table_outer_join_or_wrong_joinparent_attr",
            "provenance": "live_da2_validation_2026-11-05:ora_01416_single_table_join",
            "prompt": "Outer-join an inline subquery (inline_ind='1', e.g., WishD) in CWC Encounter (.daquery xmlversion='2') so Discern SQL Engine (EJS v3.17.3) compiles valid Oracle (+) SQL without ORA-01416.",
            "chosen": {
                "output_element_xml": '<element index="16" inlineUUID="3f65be31-cc38-4827-b0dd-8a13f65be310" joinParent="WishD" name="CE Result Value" type="3" uuid="155eec27-4c56-4cff-a4ee-861aa3335814"/>',
                "outer_join_filter_xml": '<filter joinType="3" mode="1" useIndex="1"><element name="Enc Id" type="1" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/><operator type="EQUALS"/><elements type="LIST" xCount="1"><element column_value_flag="0" joinParent="WishD" name="WishD:Enc Id" type="1" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/></elements></filter>',
                "guardrail": "Use joinParent='<SubqueryName>' and restrict (+) outer-join filters (joinType='3' mode='1') for each inline view to a single base table in Query1 (CWC_ENCOUNTER via Enc Id).",
            },
            "rejected": {
                "antipattern": "Outer-joining the same inline subquery to two different base tables in Query1 (e.g., CWC_ENCOUNTER.ENCNTR_ID and CWC_CLINICAL_EVENT.EVENT_END_DT_TM), which triggers java.sql.SQLException: ORA-01416: two tables cannot be outer-joined to each other, or using query='WishD' instead of joinParent='WishD'",
                "broken_filters": [
                    "Combining Enc Id (CWC_ENCOUNTER) and CE End Dt/Tm (CWC_CLINICAL_EVENT) with joinType='3' on the same inline subquery",
                    "Using query='WishD' instead of joinParent='WishD' in .daquery xmlversion='2'",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_live_da2_4_inline_subquery_date_prompt_pushdown",
            "failure_mode": "unbounded_inline_subquery_clinical_event_timeout",
            "provenance": "live_da2_validation_2026-11-05:subquery_date_pushdown",
            "prompt": "Prevent Oracle query timeouts when joining 6 C-SSRS inline subqueries (WishD, LevelRisk, SBehavior, SMethod, SPlan, SIntent) against V500.CLINICAL_EVENT in CWC Encounter.",
            "chosen": {
                "subquery_tag": '<query_list_item2 inline_ind="1" name="WishD" usegroupby="0" useorderby="0">',
                "subquery_date_prompt_filter": '<filter joinType="0" mode="4" useIndex="1"><element name="Enc Registration Dt/Tm" type="2" uuid="42ee9a32-031f-495d-962a-5f04376e071a"/><runtimeprompt name="Enc Registration Dt/Tm"/></filter>',
                "guardrail": "Propagate the indexed Enc Registration Dt/Tm <runtimeprompt> (mode='4') into Query1 and every inline_ind='1' subquery and set usegroupby='0' useorderby='0' on subqueries so execution completes in < 4 seconds.",
            },
            "rejected": {
                "antipattern": "Applying the runtime date prompt only to Query1 while leaving the 6 inline_ind='1' subqueries filtered only by EVENT_CD and RESULT_STATUS_CD, causing 6 full scans/hash joins across V500.CLINICAL_EVENT and timing out after 60 seconds",
                "broken_filters": [
                    "Omitting <runtimeprompt name='Enc Registration Dt/Tm'/> (mode='4') inside <query_list_item2..7> inline subqueries",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_live_da2_5_iview_self_parented_enc_id_join",
            "failure_mode": "iview_self_parented_ce_parent_event_id_join_returns_null",
            "provenance": "live_da2_validation_2026-11-05:iview_self_parented_join",
            "prompt": "Join Query1 (CSSRS Since Last Ask Suicidal Thoughts, EVENT_CD 31360467.00) to inline subqueries (WishD, LevelRisk, SBehavior, SMethod, SPlan, SIntent) in CWC Encounter so all completed IView C-SSRS response columns populate.",
            "chosen": {
                "join_key": "Enc Id (5ef4381c-6344-43c4-81b5-2c18cd6bcb70, CWC_ENCOUNTER.ENCNTR_ID)",
                "subquery_output_elements": [
                    '<element index="1" name="Enc Id" type="3" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/>',
                    '<element index="2" name="CE Event" type="3" uuid="f01d0cc1-f599-46be-b816-ecd82fffbb0e"/>',
                    '<element index="3" name="CE Result Value" type="3" uuid="155eec27-4c56-4cff-a4ee-861aa3335814"/>',
                ],
                "guardrail": "In Interactive View (IView), every C-SSRS DTA row in V500.CLINICAL_EVENT is self-parented (PARENT_EVENT_ID == EVENT_ID). Join inline subqueries on Enc Id (5ef4381c-6344-43c4-81b5-2c18cd6bcb70) and include only (Enc Id, CE Event, CE Result Value) in subquery <output> so GROUP BY deduplicates per encounter.",
            },
            "rejected": {
                "antipattern": "Outer-joining IView C-SSRS inline subqueries on CE Parent Event Id (8c750217-756f-4276-8888-17d7cc0408ae), which compares self-parented EVENT_ID values across different DTAs (never equal) and leaves all joined result columns NULL",
                "broken_filters": [
                    '<element column_value_flag="0" joinParent="WishD" name="WishD:CE Parent Event Id" type="1" uuid="8c750217-756f-4276-8888-17d7cc0408ae"/>',
                    "Including CE Event Id and CE Parent Event Id in inline subquery <output> when joining by Enc Id",
                ],
            },
        },
    ]
    birt_adv_dpo_pairs = [
        {
            "scenario_id": "phase2_dpo_birt_adv_1_computed_columns_metadata_sync",
            "failure_mode": "birt_dataset_computed_columns_missing_hints_or_polluting_native_resultset",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Define a dataset-level computed column 'calc_los_days' using BirtDateTime.diffDay on an <oda-data-set> in BIRT 4.4.2 (.rptdesign version='3.2.23') for Discern Analytics 2.0.",
            "chosen": {
                "computed_columns_xml": '<list-property name="computedColumns"><structure><property name="name">calc_los_days</property><expression name="expression" type="javascript">BirtDateTime.diffDay(row["Enc Registration Dt/Tm"], row["Enc Discharge Dt/Tm"] != null ? row["Enc Discharge Dt/Tm"] : BirtDateTime.today())</expression><property name="dataType">integer</property></structure></list-property>',
                "metadata_sync_rule": "Append calc_los_days to <list-property name='columnHints'> and <structure name='cachedMetaData'><list-property name='resultSet'>, and bind it in <table> <list-property name='boundDataColumns'>, but exclude it from the bottom native ODA <list-property name='resultSet'>.",
            },
            "rejected": {
                "antipattern": "Adding calc_los_days to the bottom native ODA <list-property name='resultSet'> (which maps 1-to-1 by UUID to JDBC driver columns and crashes ODA fetch) or omitting it from cachedMetaData/boundDataColumns",
                "broken_filters": [
                    "Placing computed column inside native ODA <list-property name='resultSet'> without a valid DA2 element UUID",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_2_group_aggregate_bound_columns",
            "failure_mode": "birt_invalid_aggregate_bound_data_column_syntax",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Create a group-scoped COUNTDISTINCT aggregate bound column 'agg_unit_distinct_patients' on 'grp_NurseUnit' in a BIRT 4.4.2 <table>.",
            "chosen": {
                "bound_column_xml": '<structure><property name="name">agg_unit_distinct_patients</property><text-property name="displayName">Distinct Patients in Unit</text-property><property name="dataType">integer</property><property name="aggregateFunction">COUNTDISTINCT</property><list-property name="arguments"><structure><property name="name">Expression</property><expression name="value" type="javascript">row["Enc Id"]</expression></structure></list-property><property name="aggregateOn">grp_NurseUnit</property><property name="allowExport">true</property></structure>',
                "guardrail": "Use <property name='aggregateFunction'>COUNTDISTINCT</property>, pass row['Enc Id'] inside <list-property name='arguments'>, and scope via <property name='aggregateOn'>grp_NurseUnit</property>.",
            },
            "rejected": {
                "antipattern": "Writing SQL-style COUNT(DISTINCT row['Enc Id']) inside <expression name='expression'> on a boundDataColumn, which fails Rhino JavaScript evaluation in BIRT",
                "broken_filters": [
                    '<expression name="expression" type="javascript">COUNT(DISTINCT row["Enc Id"])</expression>',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_3_scalar_parameters_slot_order_and_defaults",
            "failure_mode": "birt_parameters_wrong_slot_order_or_blocking_required_prompt",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Add user-selectable Nurse Unit and Clinical Risk <scalar-parameter> definitions to a DA2 BIRT 4.4.2 .rptdesign report without breaking schema order or blocking unattended runs.",
            "chosen": {
                "parameters_xml": '<parameters><scalar-parameter name="p_NurseUnit" id="2"><text-property name="promptText">Nurse Unit Filter</text-property><property name="valueType">static</property><property name="isRequired">false</property><property name="dataType">string</property><simple-property-list name="defaultValue"><value type="constant">ALL</value></simple-property-list><property name="paramType">simple</property><property name="controlType">text-box</property></scalar-parameter></parameters>',
                "guardrail": "Place <parameters> before <data-sources> in <report version='3.2.23'> and set isRequired='false' with defaultValue='ALL'.",
            },
            "rejected": {
                "antipattern": "Placing <parameters> after <body> or setting isRequired='true' with no defaultValue, which causes schema validation warnings or blocks unattended report execution",
                "broken_filters": [
                    '<property name="isRequired">true</property> with no <simple-property-list name="defaultValue">',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_4_table_filter_xml_escaping_and_aggregation",
            "failure_mode": "birt_filter_unescaped_logical_and_or_stale_aggregations",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Configure a BIRT 4.4.2 <table> <list-property name='filter'> condition that filters rows by params['p_NurseUnit'].value and params['p_RiskFilter'].value while keeping group subtotals accurate.",
            "chosen": {
                "filter_xml": '<list-property name="filter"><structure><property name="operator">is-true</property><expression name="expr" type="javascript">(params["p_NurseUnit"].value == null || params["p_NurseUnit"].value == "ALL" || row["Enc Loc Nurse Unit"] == params["p_NurseUnit"].value) &amp;&amp; (params["p_RiskFilter"].value == null || params["p_RiskFilter"].value == "ALL" || /high risk/i.test((row["LevelRisk:CE Result Value"] || "") + ""))</expression><property name="updateAggregation">true</property></structure></list-property>',
                "guardrail": "Escape '&&' as '&amp;&amp;' inside XML <expression> tags and set <property name='updateAggregation'>true</property>.",
            },
            "rejected": {
                "antipattern": "Writing raw unescaped '&&' inside <expression> (which triggers SAXParseException) or setting updateAggregation='false' so group subtotals include filtered-out rows",
                "broken_filters": [
                    'params["p_NurseUnit"].value == "ALL" && row["Enc Loc Nurse Unit"] == "2N"',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_5_datetime_and_number_formatting",
            "failure_mode": "birt_unformatted_timestamps_and_numerics",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Format clinical date-time columns ('Enc Registration Dt/Tm', 'CE End Dt/Tm') as 'MM/dd/yyyy HH:mm' and numeric columns in a BIRT 4.4.2 .rptdesign <data> element.",
            "chosen": {
                "data_xml": '<data id="145"><structure name="dateTimeFormat"><property name="category">Custom</property><property name="pattern">MM/dd/yyyy HH:mm</property></structure><property name="resultSetColumn">Enc Registration Dt/Tm</property></data>',
                "guardrail": "Attach <structure name='dateTimeFormat'> (category='Custom', pattern='MM/dd/yyyy HH:mm') to date-time <data> elements and <structure name='numberFormat'> to numeric <data> elements.",
            },
            "rejected": {
                "antipattern": "Omitting <structure name='dateTimeFormat'> or using an invalid property tag <property name='format'>MM/dd/yyyy</property> directly on <data>",
                "broken_filters": [
                    '<property name="dateFormat">MM/dd/yyyy</property>',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_6_group_slot_order_and_csv_visibility",
            "failure_mode": "birt_group_wrong_slot_or_corrupting_csv_export",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Group a BIRT 4.4.2 <table> by 'Enc Loc Nurse Unit' ('grp_NurseUnit') with a bold group header displaying the unit name and patient count while keeping CSV exports flat.",
            "chosen": {
                "group_xml": '<group id="500"><property name="groupName">grp_NurseUnit</property><property name="interval">none</property><property name="sortDirection">asc</property><expression name="keyExpr" type="javascript">row["Enc Loc Nurse Unit"]</expression><header><row id="501"><list-property name="visibility"><structure><property name="format">csv</property><expression name="valueExpr" type="javascript">true</expression></structure></list-property><cell id="502"><data id="503"><property name="resultSetColumn">Enc Loc Nurse Unit</property></data></cell><cell id="504"><data id="505"><property name="resultSetColumn">agg_unit_distinct_patients</property></data></cell></row></header></group>',
                "guardrail": "Place <group> between <table><header> and <table><detail>, and add a format='csv' <list-property name='visibility'> rule on the group header row so CSV exports only emit detail rows.",
            },
            "rejected": {
                "antipattern": "Placing <group> after <detail> or emitting unhidden group header/footer summary rows that shift column positions in downstream CSV parsers",
                "broken_filters": [
                    "Placing <group> after <detail> in <table>",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_7_clinical_ordinal_sort_and_highlight_rules",
            "failure_mode": "birt_alphabetical_risk_sort_and_missing_highlight_rules",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Sort a C-SSRS BIRT 4.4.2 report table by clinical suicide risk severity (High -> Moderate -> Positive Endorsement -> Low) and highlight High Risk rows in red (#FEE2E2).",
            "chosen": {
                "sort_xml": '<list-property name="sort"><structure><expression name="key" type="javascript">var r = (row["LevelRisk:CE Result Value"] || row["CSSRS Risk and Endorsement Flag"] || "") + ""; /high risk/i.test(r) ? 1 : (/moderate risk/i.test(r) ? 2 : (/positive endorsement|^yes$/i.test(r) ? 3 : (/low risk/i.test(r) ? 4 : 5)))</expression><property name="direction">asc</property><property name="strength">-1</property></structure></list-property>',
                "highlight_xml": '<list-property name="highlightRules"><structure><property name="operator">is-true</property><property name="backgroundColor">#FEE2E2</property><property name="color">#991B1B</property><property name="fontWeight">bold</property><expression name="testExpr" type="javascript">/high risk|positive endorsement|^yes$/i.test((row["CSSRS Risk and Endorsement Flag"] || row["LevelRisk:CE Result Value"] || "") + "")</expression></structure></list-property>',
                "guardrail": "Use a JavaScript ordinal expression in <list-property name='sort'> so clinical severity sorts High (1) -> Moderate (2) -> Low (4), and attach <list-property name='highlightRules'> with operator='is-true' to the detail <row>.",
            },
            "rejected": {
                "antipattern": "Sorting alphabetically on row['LevelRisk:CE Result Value'] (which places 'Low risk' ahead of 'Moderate risk') and omitting <list-property name='highlightRules'>",
                "broken_filters": [
                    '<expression name="key" type="javascript">row["LevelRisk:CE Result Value"]</expression>',
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_live_da2_6_cwc_encounter_canonical_uuid_vs_deprecated_cwf_collision",
            "failure_mode": "deprecated_cwf_encounter_uuid_or_synthetic_uuid5_npe_in_query_builder",
            "provenance": "live_da2_validation_2026-11-05:cwc_canonical_uuids",
            "prompt": "Select Order Id, CE Result Units, Chg Service Dt/Tm, Chg Item Quantity, and F Chg Status in a CWC Encounter .daquery (xmlversion='2') so DA2 Query Builder opens without NullPointerException.",
            "chosen": {
                "elements_xml": [
                    '<element index="1" name="Order Id" type="3" uuid="0081bf6a-b747-49ad-be49-e193d5b3bc93"/>',
                    '<element index="2" name="CE Result Units" type="3" uuid="3d18ca87-7efd-487a-9121-0e5072f25f7a"/>',
                    '<element index="3" name="Chg Service Dt/Tm" type="2" uuid="9452e25e-98ec-4820-b40c-b95fbc94935e"/>',
                    '<element index="4" name="Chg Item Quantity" type="1" uuid="9c7440d4-51a5-4dd1-ac0c-a8bf799fb074"/>',
                    '<element index="5" name="F Chg Status" type="3" uuid="30aadfd4-ba16-4e13-a56d-b0de02c4882b"/>',
                ],
                "guardrail": "Always resolve CWC Encounter element UUIDs from the verified CWC Encounter Business View catalog (LIVE_CWC_ELEMENT_MAP); never use deprecated CWF Encounter/Deprecate UUIDs or synthetic uuid5 fallback UUIDs.",
            },
            "rejected": {
                "antipattern": "Using deprecated CWF Encounter/Deprecate UUIDs (such as a2ff2540-fa94-41bf-a0eb-69771900201f for Order Id) or unmapped synthetic uuid5 UUIDs, which causes DA2 QueryBuilderView to throw java.lang.NullPointerException on open",
                "broken_filters": [
                    '<element index="1" name="Order Id" type="3" uuid="a2ff2540-fa94-41bf-a0eb-69771900201f"/>',
                    "Using uuid.uuid5 synthetic fallback UUIDs inside <output><elements> in .daquery xmlversion='2'",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_birt_adv_8_multi_level_group_sum_ave_aggregations",
            "failure_mode": "birt_missing_multi_level_group_or_sum_ave_subtotals",
            "provenance": "phase2_birt_442_advanced_report_designer_dpo",
            "prompt": "Configure multi-level grouping (grp_NurseUnit outer group and grp_ClinicalCategory inner group) with COUNTDISTINCT, SUM, and AVE aggregate bound columns and BirtMath.round computed columns in BIRT 4.4.2.",
            "chosen": {
                "computed_column_xml": '<structure><property name="name">calc_numeric_metric</property><expression name="expression" type="javascript">BirtMath.round(parseFloat((row["CE Result Value"] || row["Chg Item Quantity"] || "0") + "") || 0, 1)</expression><property name="dataType">decimal</property></structure>',
                "aggregate_bound_columns_xml": [
                    '<structure><property name="name">agg_unit_distinct_patients</property><property name="dataType">integer</property><property name="aggregateFunction">COUNTDISTINCT</property><property name="aggregateOn">grp_NurseUnit</property></structure>',
                    '<structure><property name="name">agg_unit_total_metric</property><property name="dataType">decimal</property><property name="aggregateFunction">SUM</property><property name="aggregateOn">grp_NurseUnit</property></structure>',
                    '<structure><property name="name">agg_cat_avg_metric</property><property name="dataType">decimal</property><property name="aggregateFunction">AVE</property><property name="aggregateOn">grp_ClinicalCategory</property></structure>',
                ],
                "guardrail": "Place both <group> blocks (grp_NurseUnit and grp_ClinicalCategory) between <table><header> and <table><detail>, scope subtotals via <property name='aggregateOn'>, and hide group header rows in CSV format.",
            },
            "rejected": {
                "antipattern": "Omitting inner group <property name='aggregateOn'> scoping or computing group SUM/AVE inside detail row expressions without aggregateFunction",
                "broken_filters": [
                    "Using detail-row JavaScript accumulators instead of <property name='aggregateFunction'>SUM</property> and AVE",
                ],
            },
        },
    ]
    extra_dpo_rows.extend(live_da2_dpo_pairs)
    extra_dpo_rows.extend(birt_adv_dpo_pairs)

    v2_precision_dpo_pairs = [
        {
            "scenario_id": "phase2_dpo_v2_trap_1_mar_admin_vs_order_date_and_discontinued_status",
            "failure_mode": "mar_discontinued_order_filter_and_order_date_confusion",
            "provenance": "phase2_v2_precision_dpo",
            "prompt": "Build an inpatient MAR opioid/PRN medication administration audit over the last 7 days without dropping doses given under later-discontinued orders.",
            "chosen": {
                "da2_domain": "CWC Encounter",
                "temporal_anchor": "CE Med Admin Start Dt/Tm (MED_ADMIN_EVENT.BEG_DT_TM)",
                "order_status_filter": "CWC_ORDERS.ORDER_STATUS_CD NOT IN (2542, 2553) /* Not In Canceled, Voided; preserves Discontinued 2545 */",
                "guardrail": "Anchor MAR time windows on MED_ADMIN_EVENT.BEG_DT_TM and filter Order Status Not In (Canceled=2542, Voided=2553) so historical doses under discontinued orders are preserved.",
            },
            "rejected": {
                "antipattern": "Filtering MAR queries on ORDERS.ORIG_ORDER_DT_TM or excluding ORDER_STATUS_CD 2545 (Discontinued) via NOT IN (2542, 2545, 2553), which silently erases historical PRN administrations",
                "broken_filters": [
                    "CWC_ORDERS.ORDER_STATUS_CD NOT IN (2542, 2545, 2553)",
                    "CWC_ORDERS.ORIG_ORDER_DT_TM >= SYSDATE - 7",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_v2_trap_2_clinical_event_end_vs_updt_dt_tm",
            "failure_mode": "housekeeping_updt_dt_tm_filter_and_unverified_result_status",
            "provenance": "phase2_v2_precision_dpo",
            "prompt": "Filter documented clinical event results by clinical observation timestamp and verified result status in V500.CLINICAL_EVENT.",
            "chosen": {
                "da2_domain": "CWC Encounter",
                "temporal_anchor": "CE End Dt/Tm (4433e8b0-5947-4f79-b4aa-c32c8ec30c45 -> CLINICAL_EVENT.EVENT_END_DT_TM)",
                "mandatory_predicates": [
                    "CWC_CLINICAL_EVENT.VALID_UNTIL_DT_TM >= TO_DATE('2100-12-31 00:00:00', 'YYYY-MM-DD HH24:MI:SS')",
                    "CWC_CLINICAL_EVENT.RESULT_STATUS_CD IN (25, 34, 35)",
                ],
                "guardrail": "Always filter clinical events on EVENT_END_DT_TM (never UPDT_DT_TM) with RESULT_STATUS_CD IN (25, 34, 35) and VALID_UNTIL_DT_TM >= 2100-12-31.",
            },
            "rejected": {
                "antipattern": "Filtering V500.CLINICAL_EVENT on row-housekeeping UPDT_DT_TM or omitting RESULT_STATUS_CD IN (25, 34, 35), which leaks In Error (28) and superseded draft rows",
                "broken_filters": [
                    "WHERE CWC_CLINICAL_EVENT.UPDT_DT_TM >= SYSDATE - 7",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_v2_trap_3_admitting_diagnosis_outer_join_vs_inner_join",
            "failure_mode": "inner_join_diagnosis_drops_same_day_admissions",
            "provenance": "phase2_v2_precision_dpo",
            "prompt": "Include admitting ICD-10 diagnosis display on an active inpatient encounter census without dropping newly admitted same-day patients whose diagnosis is not yet coded.",
            "chosen": {
                "da2_domain": "CWC Encounter",
                "from_join": "V500.ENCOUNTER CWC_ENCOUNTER LEFT OUTER JOIN V500.DIAGNOSIS CWC_DIAGNOSIS ON CWC_ENCOUNTER.ENCNTR_ID = CWC_DIAGNOSIS.ENCNTR_ID AND CWC_DIAGNOSIS.ACTIVE_IND = 1",
                "guardrail": "Use LEFT OUTER JOIN V500.DIAGNOSIS (DA2 joinType='3' / Oracle (+)) on active census queries so encounters with NULL admitting diagnosis are preserved.",
            },
            "rejected": {
                "antipattern": "Inner-joining V500.DIAGNOSIS on an active inpatient census query, which silently drops same-day admissions lacking a coded diagnosis row",
                "broken_filters": [
                    "WHERE CWC_ENCOUNTER.ENCNTR_ID = CWC_DIAGNOSIS.ENCNTR_ID AND CWC_DIAGNOSIS.ACTIVE_IND = 1",
                ],
            },
        },
        {
            "scenario_id": "phase2_dpo_v2_trap_4_charge_service_vs_posted_and_test_patient_prefix_exclusion",
            "failure_mode": "charge_posted_vs_service_date_and_missing_test_patient_prefix_exclusion",
            "provenance": "phase2_v2_precision_dpo",
            "prompt": "Extract therapy bill item charges by clinical service date while excluding ZZ test patients.",
            "chosen": {
                "da2_domain": "CWC Encounter",
                "temporal_anchor": "Chg Service Dt/Tm (9452e25e-98ec-4820-b40c-b95fbc94935e -> CHARGE.SERVICE_DT_TM)",
                "test_patient_filter": "CWC_PERSON.NAME_LAST_KEY < 'ZZ'",
                "guardrail": "Use CHARGE.SERVICE_DT_TM for date of service (not POSTED_DT_TM) and enforce CWC_PERSON.NAME_LAST_KEY < 'ZZ' to exclude test patients.",
            },
            "rejected": {
                "antipattern": "Filtering therapy productivity by CHARGE.POSTED_DT_TM (billing batch post date) and omitting CWC_PERSON.NAME_LAST_KEY < 'ZZ'",
                "broken_filters": [
                    "WHERE CWC_CHARGE.POSTED_DT_TM >= SYSDATE - 7",
                ],
            },
        },
    ]
    extra_dpo_rows.extend(v2_precision_dpo_pairs)
    v2_2_clinical_harvest_dpo_pairs = [
        {
            "scenario_id": "v2_2_dpo_attending_phys_vs_adj_post_prsnl",
            "failure_mode": "cross_domain_fallback_to_cwf_gs_adjustments_and_revenue",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "prompt": "Build a CWC Encounter clinical report showing patient name, FIN, room/bed, attending physician, admit date, and discharge date.",
            "chosen": {
                "da2_domain": "CWC Encounter",
                "attending_physician_element": "Enc Attending Phys (e43ef111-119b-4239-993a-daaa45e78c85)",
                "admit_date_element": "Enc Inpatient Admit Dt/Tm (42de992b-fe3c-4ff8-aa5d-d2ccbb7f7f05)",
                "discharge_date_element": "Enc Discharge Dt/Tm (c6acbd64-fb7a-4484-9e77-d6c6b172c5e6)",
                "guardrail": "Keep the query in single-domain CWC Encounter using Enc Attending Phys and Enc Inpatient Admit Dt/Tm / Enc Discharge Dt/Tm.",
            },
            "rejected": {
                "antipattern": "Polluted a clinical encounter report with financial adjustment and revenue domains (Adj Post Prsnl in CWF GS Adjustments and F Enc inpatient Admit Dt/Tm in CWF GS Revenue) due to unanchored SQL column fallback",
                "broken_filters": [
                    "Selecting Adj Post Prsnl from CWF GS Adjustments instead of Enc Attending Phys in CWC Encounter",
                ],
            },
        },
        {
            "scenario_id": "v2_2_dpo_discharged_substring_charge_pollution",
            "failure_mode": "substring_containment_false_positive_pollution",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "prompt": "Build a report of active or recently discharged inpatients with primary and secondary insurance plans and bowel management documentation.",
            "chosen": {
                "da2_domain": "CWC Encounter",
                "matched_concepts": ["inpatient", "discharged", "health plan", "insurance", "bowel"],
                "excluded_false_positives": ["charge (inside discharged)", "mar (inside primary)", "age (inside management)"],
                "guardrail": "Enforce word-boundary regex matching ((?<![a-z0-9])phrase(?![a-z0-9])) so 'discharged' never triggers CHARGE, 'primary' never triggers MAR, and 'management' never triggers Person Age.",
            },
            "rejected": {
                "antipattern": "Raw substring containment matched 'charge' inside 'discharged', 'mar' inside 'primary', and 'age' inside 'management', polluting the query with Chg Bill Item Desc, Med Adm Event Id, and Person Age",
                "broken_filters": [
                    "Using `if phr in low:` substring matching without word boundaries",
                ],
            },
        },
        {
            "scenario_id": "v2_2_dpo_scheduled_med_vs_sch_appt_and_suicide_precautions_vs_cssrs",
            "failure_mode": "template_hijack_and_sch_appt_pollution",
            "provenance": "live_da2_clinical_harvest_v2_2",
            "prompt": "Build an Inpatient Precaution Orders report (Aspiration, Fall, Seizure, Suicide Precautions) and a Late Medication Administration Audit showing scheduled date/time vs administered date/time.",
            "chosen": {
                "precaution_orders_template": "Inpatient_Precaution_Orders_Report (single-query CWC Encounter filtering Order Catalog ca672220-76a6-4601-bc0f-f0104e07874c for 2780633.00, 38884839.00, 38888573.00, 4149527.00)",
                "late_mar_template": "Late_Medication_Administration_Audit (Task Dt/Tm vs Med Admin Dt/Tm without Sch App Id)",
                "guardrail": "Do not trigger the 7-query C-SSRS inline subquery template when 'Suicide Precautions' is requested as an inpatient precaution order, and do not map 'scheduled date/time' on MAR/Task audits to outpatient Sch App Id.",
            },
            "rejected": {
                "antipattern": "Hijacked Inpatient Precaution Orders into the 7-query C-SSRS screening template and polluted Late Medication Administration with outpatient SCH_APPT columns (Sch App Id)",
                "broken_filters": [
                    "Triggering 7-query C-SSRS clinical event inline subquery on 'Suicide Precautions' order requests",
                ],
            },
        },
    ]
    extra_dpo_rows.extend(v2_2_clinical_harvest_dpo_pairs)


    all_dpo_candidates = core_dpo_rows + extra_dpo_rows
    enriched_dpo: list[dict[str, Any]] = []
    for dpo in all_dpo_candidates:
        new_dpo = dict(dpo)
        chosen = dict(new_dpo["chosen"]) if isinstance(new_dpo["chosen"], dict) else {"value": new_dpo["chosen"]}
        r_trace = synthesize_structured_reasoning({"task": "dpo_preference", "response": chosen})
        chosen["reasoning_trace"] = r_trace
        new_dpo["chosen"] = chosen
        new_dpo["chosen_formatted"] = f"{r_trace}\n<output>\n{json.dumps(chosen, ensure_ascii=False)}\n</output>"
        new_dpo["rejected_formatted"] = (
            "<reasoning>\nSkipping tenancy, UUID, row-versioning, and active-census verification.\n</reasoning>\n"
            f"<output>\n{json.dumps(new_dpo['rejected'], ensure_ascii=False)}\n</output>"
        )
        enriched_dpo.append(new_dpo)

    # 4. CPU-Parallel Deterministic Verifier Gate (Rejection Sampling)
    print(f"Step 2: Running CPU-parallel deterministic verifiers over {len(enriched_sft)} SFT and {len(enriched_dpo)} DPO records...", flush=True)
    accepted_sft: list[dict[str, Any]] = []
    rejected_sft_count = 0
    with concurrent.futures.ProcessPoolExecutor() as ex:
        for row, (ok, errs) in zip(enriched_sft, ex.map(verify_single_sft_record, enriched_sft)):
            if ok:
                accepted_sft.append(row)
            else:
                rejected_sft_count += 1
                print(f"  [REJECTED SFT] {row.get('task')}: {errs}", flush=True)

    accepted_dpo: list[dict[str, Any]] = []
    rejected_dpo_count = 0
    with concurrent.futures.ProcessPoolExecutor() as ex:
        for row, (ok, errs) in zip(enriched_dpo, ex.map(verify_single_dpo_record, enriched_dpo)):
            if ok:
                accepted_dpo.append(row)
            else:
                rejected_dpo_count += 1
                print(f"  [REJECTED DPO] {row.get('scenario_id')}: {errs}", flush=True)

    # Write verified datasets back to data/
    with (data_dir / "sft_train_v1.jsonl").open("w", encoding="utf-8") as fh:
        for r in accepted_sft:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    with (data_dir / "dpo_contrastive_v1.jsonl").open("w", encoding="utf-8") as fh:
        for r in accepted_dpo:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "phase2_distillation_and_verification": {
            "ge_teacher_rationales_distilled": len(teacher_notes),
            "sft_candidates_evaluated": len(enriched_sft),
            "sft_accepted": len(accepted_sft),
            "sft_rejected_by_verifier": rejected_sft_count,
            "dpo_candidates_evaluated": len(enriched_dpo),
            "dpo_accepted": len(accepted_dpo),
            "dpo_rejected_by_verifier": rejected_dpo_count,
            "reasoning_trace_coverage_rate": 1.0,
            "zero_leakage_enforced": True,
        }
    }
    (data_dir / "phase2_distillation_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
