"""
Phase 1.1 Holdout Synthesizer, Multi-Surface Deterministic Verifier & OS Seal
(`scripts/build_postfreeze_v3_holdout.py`).

Constructs and seals `data/eval_postfreeze_v3.jsonl` (N = 640, chmod 0444) BEFORE
any Phase 2 symbolic compiler edits or Phase 3 neural training begin.

Composition (640 zero-PHI items):
- Slice 1 (`canonical_template_paraphrase`, N = 396): 11 diverse holdout-exclusive
  paraphrases across all 36 canonical DA2/V500/CCL templates.
- Slice 2 (`concept_collision_and_boundary_trap`, N = 132): 6 adversarial collision
  families (clinical attending/provider vs billing Adj/Prsnl Position, discharged vs
  CHARGE, clinical dose adjustment vs CWF GS Adjustments, word-boundary LOS/ORDER/HIM
  traps, negated billing/column clauses, and explicit financial/credentialing controls).
- Slice 3 (`multiturn_negative_and_override_refinement`, N = 112): structured multi-turn
  follow-ups testing `exclude_columns` (`remove_columns`), `exclude_filters`
  (`remove_filters`), `override_filters` (`replace_filter_value`), and conversational
  filler stripping in `_derive_report_name()`.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import stat
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from corpus_firewall import (  # noqa: E402
    build_firewall_index,
    check_text_against_index,
    normalize_exact,
    normalize_token_bag,
    save_firewall_hashes,
)
from discern_intelligence.ccl_compiler import CCLCompiler, TABLE_ALIASES  # noqa: E402
from discern_intelligence.da2_generator import (  # noqa: E402
    DEPRECATED_CWC_UUIDS,
    DOMAIN_UUID_MAP,
    LIVE_CWC_ELEMENT_MAP,
)
from discern_intelligence.eval_harness import (  # noqa: E402
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from discern_intelligence.knowledge import (  # noqa: E402
    load_da2_canonical_reports_catalog,
    load_da2_elements,
)
from discern_intelligence.release_audit import (  # noqa: E402
    COMPILED_AUDIT_PATTERNS,
    FORBIDDEN_SUBSTRINGS,
)
from discern_intelligence.sql_execution_harness import (  # noqa: E402
    create_synthetic_v500_connection,
    execute_and_verify_ejs_sql,
)

BILLING_CONTAMINATION_ELEMENTS = [
    "Adj Post Prsnl",
    "Prsnl Position",
    "Adj Trans Sub Type",
    "Adj Trans Reason",
    "Adj Post Dt",
    "Adj Amount",
    "AdjTrans Alias",
    "Adj Debit GL Number",
    "Adj Credit GL Number",
]

CHARGE_CONTAMINATION_ELEMENTS = [
    "Chg Charge Item Id",
    "Chg Item Id",
    "Chg Description",
    "Chg Bill Item Desc",
    "Chg Quantity",
    "Chg Item Quantity",
    "Chg Item Extended Price",
    "Chg Service Dt Tm",
    "Chg Service Dt/Tm",
]

FINANCIAL_TEMPLATE_IDS = {
    "cwf_gs_revenue_transaction_detail_extract",
    "cwf_gs_payments_transaction_detail_extract",
    "cwf_gs_adjustments_transaction_detail_extract",
    "cross_domain_inline_subquery_inpatient_revenue",
    "patient_accounting_denials_and_benefit_order_extract",
    "encounter_charges_with_ndc_and_duplicate_charge_audit",
    "patient_accounting_billing_guarantor_and_hp_verification_extract",
}

TEMPLATE_FAMILY_MAP: dict[str, str] = {
    "cwf_gs_revenue_transaction_detail_extract": "revenue_cycle_ledger",
    "cwf_gs_payments_transaction_detail_extract": "revenue_cycle_ledger",
    "cwf_gs_adjustments_transaction_detail_extract": "revenue_cycle_ledger",
    "cross_domain_inline_subquery_inpatient_revenue": "revenue_cycle_ledger",
    "patient_accounting_denials_and_benefit_order_extract": "patient_accounting_billing",
    "encounter_charges_with_ndc_and_duplicate_charge_audit": "patient_accounting_billing",
    "patient_accounting_billing_guarantor_and_hp_verification_extract": "patient_accounting_billing",
    "birt_rptdesign_discharge_disposition_by_facility": "census_and_disposition",
    "multi_query_union_census_extract": "census_and_disposition",
    "detailed_cdc_race_and_ethnicity_person_info_extract": "census_and_disposition",
    "bed_board_lines_catheters_documentation_24h": "vascular_and_catheter_surveillance",
    "cauti_indwelling_urinary_catheter_and_culture_surveillance": "vascular_and_catheter_surveillance",
    "peripheral_iv_and_central_line_catheter_surveillance": "vascular_and_catheter_surveillance",
    "bed_board_specialty_orders_audit": "clinical_orders_and_precautions",
    "bed_board_resuscitation_status_all_inpatients": "clinical_orders_and_precautions",
    "outside_appointments_and_diagnostic_radiology_orders": "clinical_orders_and_precautions",
    "inpatient_precaution_and_isolation_orders_audit": "clinical_orders_and_precautions",
    "rehab_pt_ot_slp_therapy_minutes_extract": "nursing_hygiene_and_rehab_flowsheets",
    "oral_care_documentation_and_hygiene_audit": "nursing_hygiene_and_rehab_flowsheets",
    "bladder_bowel_management_and_continence_audit": "nursing_hygiene_and_rehab_flowsheets",
    "bathing_and_daily_hygiene_documentation_audit": "nursing_hygiene_and_rehab_flowsheets",
    "pressure_injury_and_wound_flowsheet_audit": "wound_nutrition_and_bariatric_audits",
    "pressure_wound_and_hospital_acquired_pressure_injury_audit": "wound_nutrition_and_bariatric_audits",
    "inpatient_bariatric_weight_and_bmi_clinical_audit": "wound_nutrition_and_bariatric_audits",
    "inpatient_nutrition_assessment_and_bmi_audit": "wound_nutrition_and_bariatric_audits",
    "dietary_orders_and_food_allergy_reconciliation": "wound_nutrition_and_bariatric_audits",
    "opioid_benzo_and_pdmp_discharge_medication_audit": "pharmacy_and_mar_safety",
    "bcma_medication_administration_and_missed_med_tasks": "pharmacy_and_mar_safety",
    "opioid_mme_administered_and_ordered_surveillance": "pharmacy_and_mar_safety",
    "pain_medication_reassessment_joint_dataset_audit": "pharmacy_and_mar_safety",
    "pharmacy_medication_verification_grouped_audit": "pharmacy_and_mar_safety",
    "pharmacy_clinical_interventions_detail_audit": "pharmacy_and_mar_safety",
    "oppe_fppe_provider_notes_and_dynamic_doc_timeliness": "provider_coding_and_scheduling",
    "outpatient_scheduling_and_slot_utilization": "provider_coding_and_scheduling",
    "him_icd10_diagnosis_and_drg_coding_extract": "provider_coding_and_scheduling",
    "inpatient_vte_prophylaxis_and_icd10_diagnosis_audit": "provider_coding_and_scheduling",
}

# Succinct semantic topic descriptors per canonical template (zero raw catalog title copy)
TEMPLATE_SEMANTIC_SPECS: dict[str, tuple[str, str]] = {
    "cwf_gs_revenue_transaction_detail_extract": (
        "general ledger revenue transaction line items with CDM, CPT4, HCPCS, revenue codes, and debit/credit GL numbers",
        "hospital revenue cycle accounting",
    ),
    "cwf_gs_payments_transaction_detail_extract": (
        "posted cash payment transactions including payment method, transaction reason, effective date, and health plan",
        "treasury remittance reconciliation",
    ),
    "cwf_gs_adjustments_transaction_detail_extract": (
        "contractual and write-off financial adjustment transactions showing post date, adjustment amount, reason, and GL accounts",
        "revenue integrity write-off review",
    ),
    "cross_domain_inline_subquery_inpatient_revenue": (
        "inpatient encounter roster joined via inline revenue subquery to total revenue amount, quantity, and revenue code",
        "cross-domain encounter revenue rollups",
    ),
    "birt_rptdesign_discharge_disposition_by_facility": (
        "facility-grouped inpatient discharge disposition breakdown with admit timestamp, discharge timestamp, and length of stay",
        "case management transition tracking",
    ),
    "multi_query_union_census_extract": (
        "combined multi-query union extract merging current active inhouse census rows alongside completed inpatient discharges",
        "capacity command center operations",
    ),
    "bed_board_lines_catheters_documentation_24h": (
        "unit bed-board invasive line, peripheral IV, and urinary catheter flowsheet documentation recorded during prior 24 hours",
        "nursing shift vascular device check",
    ),
    "bed_board_specialty_orders_audit": (
        "active bed-board specialty clinical orders spanning telemetry, hemodialysis, oxygen, negative-pressure wound vac, enteral nutrition, and noninvasive ventilation",
        "acuity resource huddles",
    ),
    "bed_board_resuscitation_status_all_inpatients": (
        "unit bed-board resuscitation and code status orders across active hospitalized inpatients",
        "rapid response readiness review",
    ),
    "rehab_pt_ot_slp_therapy_minutes_extract": (
        "physical therapy, occupational therapy, and speech-language pathology direct, group, and concurrent treatment minutes",
        "rehabilitation therapy productivity",
    ),
    "pressure_injury_and_wound_flowsheet_audit": (
        "present-on-admission pressure ulcer, hospital-acquired pressure injury, and incision or wound #1-#3 flowsheet charting",
        "skin integrity nursing rounds",
    ),
    "opioid_benzo_and_pdmp_discharge_medication_audit": (
        "concurrent opioid plus benzodiazepine prescribing and PDMP review documentation on discharge prescriptions",
        "controlled substance stewardship",
    ),
    "bcma_medication_administration_and_missed_med_tasks": (
        "barcode medication administration events, positive patient/med scan indicators, and overdue or missed MAR tasks",
        "BCMA nursing safety surveillance",
    ),
    "patient_accounting_denials_and_benefit_order_extract": (
        "patient accounting claim denials, benefit order financial class, HCFA bill type, denial category, and account balances",
        "denials management workqueue",
    ),
    "detailed_cdc_race_and_ethnicity_person_info_extract": (
        "granular CDC race plus ethnic background attributes recorded within PERSON_INFO user-defined demographics",
        "regulatory health equity reporting",
    ),
    "oppe_fppe_provider_notes_and_dynamic_doc_timeliness": (
        "OPPE and FPPE provider progress notes, consult notes, and discharge summary verification timeliness",
        "medical staff credentialing quality",
    ),
    "outpatient_scheduling_and_slot_utilization": (
        "ambulatory clinic appointment bookings, resource slot utilization states, cancellations, and no-show reasons",
        "clinic access operations",
    ),
    "dietary_orders_and_food_allergy_reconciliation": (
        "active inpatient dietary nutrition orders reconciled against documented food allergies and reaction severity",
        "culinary dietetics safety check",
    ),
    "him_icd10_diagnosis_and_drg_coding_extract": (
        "HIM coded ICD-10-CM diagnoses by discharge window with clinical priority, present-on-admission flag, HAC indicator, and DRG coding",
        "health information coding audit",
    ),
    "outside_appointments_and_diagnostic_radiology_orders": (
        "diagnostic imaging radiology orders spanning X-ray, CT, MRI, and ultrasound modalities plus outside appointment coordination",
        "diagnostic imaging coordination",
    ),
    "encounter_charges_with_ndc_and_duplicate_charge_audit": (
        "encounter pharmacy charge items carrying NDC modifier fields, extended price, manual charge flag, and duplicate charge detection",
        "pharmacy charge capture integrity",
    ),
    "cauti_indwelling_urinary_catheter_and_culture_surveillance": (
        "CAUTI bundle surveillance tracking indwelling Foley catheter activity, placement date, urine culture results, and temperature",
        "infection prevention catheter rounds",
    ),
    "pressure_wound_and_hospital_acquired_pressure_injury_audit": (
        "hospital-acquired pressure ulcers contrasted against admission-documented pressure staging and dynamic wound assessments",
        "wound ostomy quality committee",
    ),
    "opioid_mme_administered_and_ordered_surveillance": (
        "hospitalized adult morphine milligram equivalent opioid ordered versus MAR administered dose and route surveillance",
        "inpatient pain stewardship",
    ),
    "pain_medication_reassessment_joint_dataset_audit": (
        "pain analgesia MAR administration paired via joint dataset with follow-up pain reassessment scores and task completion",
        "analgesia reassessment compliance",
    ),
    "pharmacy_medication_verification_grouped_audit": (
        "medication order actions and clinical pharmacist verification timestamps across inpatient nursing units",
        "inpatient pharmacy order review",
    ),
    "inpatient_vte_prophylaxis_and_icd10_diagnosis_audit": (
        "venous thromboembolism prophylaxis coverage paired with final ICD-10-CM diagnosis codes on hospitalized encounters",
        "thrombosis prevention surveillance",
    ),
    "inpatient_bariatric_weight_and_bmi_clinical_audit": (
        "bariatric inpatient weight 300+ lbs and body mass index 40+ clinical event measurements with result units",
        "bariatric specialty equipment planning",
    ),
    "patient_accounting_billing_guarantor_and_hp_verification_extract": (
        "guarantor relationship contact details, address/phone types, and health plan eligibility verification status",
        "registration financial clearance",
    ),
    "oral_care_documentation_and_hygiene_audit": (
        "inpatient oral care, mouth hygiene assistance, denture care, and oral care refusal flowsheet documentation",
        "ventilator and pneumonia prevention",
    ),
    "bladder_bowel_management_and_continence_audit": (
        "inpatient bladder and bowel management, post-void residual bladder scan volume, and continence flowsheet entries",
        "continence rehabilitation rounds",
    ),
    "inpatient_nutrition_assessment_and_bmi_audit": (
        "inpatient nutritional screening forms, dietary risk factors, nutrition evaluation, and measured BMI charting",
        "clinical dietitian consult triage",
    ),
    "pharmacy_clinical_interventions_detail_audit": (
        "clinical pharmacist intervention forms, intervention importance, prescriber response, and pharmacoeconomic time logs",
        "pharmacotherapy intervention tracking",
    ),
    "bathing_and_daily_hygiene_documentation_audit": (
        "daily inpatient bathing method, shower or bed-bath assistance, WeeFIM grooming, and bathing refusal reasons",
        "nursing daily hygiene compliance",
    ),
    "peripheral_iv_and_central_line_catheter_surveillance": (
        "peripheral IV access and central venous line catheter size, insertion activity, and dressing surveillance",
        "vascular access team rounds",
    ),
    "inpatient_precaution_and_isolation_orders_audit": (
        "active inpatient fall, aspiration, seizure, hip, sternal, suicide, and isolation precaution orders",
        "unit safety precaution signage",
    ),
}

# 11 Holdout-Exclusive Syntactic Frames (carefully phrased so zero 5-grams match legacy SFT/DPO!)
HOLDOUT_FRAMES = [
    "Operational workflow [{ctx}] ({ten_tag}): assemble a verified extraction covering {topic}.",
    "Quality review [{ctx}] ({ten_tag}): deliver a clinical-or-fiscal dataset capturing {topic}.",
    "Shift handoff [{ctx}] ({ten_tag}): compile structured analytics focused on {topic}.",
    "Governance docket [{ctx}] ({ten_tag}): construct an upload-ready specification detailing {topic}.",
    "Service line brief [{ctx}] ({ten_tag}): output validated query artifacts examining {topic}.",
    "Audit charter [{ctx}] ({ten_tag}): synthesize an encounter-safe packet tracking {topic}.",
    "Committee packet [{ctx}] ({ten_tag}): formulate a zero-fanout worklist surfacing {topic}.",
    "Compliance sweep [{ctx}] ({ten_tag}): produce verified reporting logic inspecting {topic}.",
    "Director dashboard [{ctx}] ({ten_tag}): arrange a schema-checked extract summarizing {topic}.",
    "Surveillance ticket [{ctx}] ({ten_tag}): prepare deterministic query definitions monitoring {topic}.",
    "Operations roster [{ctx}] ({ten_tag}): furnish an execution-tested bundle enumerating {topic}.",
]

TENANCY_CYCLE = [
    ("communityworks", "cw-multitenant"),
    ("continuum", "continuum-hosted"),
    ("standalone_rho", "rho-single-tenant"),
]


def _build_element_uuid_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for e in load_da2_elements():
        if e.get("da2_field_name") and e.get("element_uuid"):
            u = e["element_uuid"]
            if u.lower() not in {d.lower() for d in DEPRECATED_CWC_UUIDS}:
                lookup[e["da2_field_name"].lower()] = u
    cat = load_da2_canonical_reports_catalog()
    for bv in cat.get("business_views_xml_catalog", []):
        for col in bv.get("columns", []):
            u = col["uuid"]
            if u.lower() not in {d.lower() for d in DEPRECATED_CWC_UUIDS}:
                lookup.setdefault(col["name"].lower(), u)
    for qr in cat.get("canonical_queries_and_reports", []):
        if qr.get("cclquery_xml"):
            try:
                root = ET.fromstring(qr["cclquery_xml"])
                for el in root.findall(".//element"):
                    n = el.attrib.get("name", "")
                    u = el.attrib.get("uuid", "")
                    if n and u and u.lower() not in {d.lower() for d in DEPRECATED_CWC_UUIDS}:
                        lookup.setdefault(n.lower(), u)
                        if ":" in n:
                            lookup.setdefault(n.split(":", 1)[1].lower(), u)
            except ET.ParseError:
                pass
    for k, (cn, cu, _) in LIVE_CWC_ELEMENT_MAP.items():
        lookup[k.lower()] = cu
        lookup[cn.lower()] = cu
    return lookup


def _deterministic_uuid4_from_seed(seed_text: str) -> str:
    digest = bytearray(hashlib.sha256(seed_text.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40  # version 4
    digest[8] = (digest[8] & 0x3F) | 0x80  # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(digest)))


def _canonical_domain_for_template(qr: dict[str, Any]) -> tuple[str, str]:
    raw_dom = qr["da2_domain"]
    if raw_dom.startswith("CWC Encounter"):
        return "CWC Encounter", DOMAIN_UUID_MAP["CWC Encounter"]
    if raw_dom in DOMAIN_UUID_MAP:
        return raw_dom, DOMAIN_UUID_MAP[raw_dom]
    return "CWC Encounter", DOMAIN_UUID_MAP["CWC Encounter"]


def _build_verified_reference_artifacts(
    eval_id: str,
    da2_domain: str,
    domain_uuid: str,
    selected_elements: list[str],
    override_filters: dict[str, list[str]],
    exclude_filters: list[str],
    is_active_census: bool,
    uuid_lookup: dict[str, str],
    ccl_compiler: CCLCompiler,
) -> dict[str, Any]:
    q_uuid = _deterministic_uuid4_from_seed(f"query:{eval_id}")
    q_name = f"Ref_{eval_id}"

    # 1. Build <cclquery version="1.0"> XML
    el_lines: list[str] = []
    for fname in selected_elements:
        base = fname.split(":", 1)[1].strip() if ":" in fname else fname.strip()
        el_u = uuid_lookup.get(fname.lower()) or uuid_lookup.get(base.lower())
        if not el_u:
            # All catalog names are in _known_uuids_and_names(); mint RFC4122 v4 UUID (never v5)
            el_u = _deterministic_uuid4_from_seed(f"elem:{base.lower()}")
        el_lines.append(f'        <element name="{html.escape(base)}" uuid="{el_u}"/>')

    filter_blocks: list[str] = []
    # Map common filter overrides to verified numeric Millennium CODE_VALUEs
    cv_lookup = {
        "inpatient": ("Inpatient", "309308"),
        "outpatient": ("Outpatient", "309309"),
        "emergency": ("Emergency", "309310"),
        "observation": ("Observation", "309312"),
        "active": ("Active", "854"),
        "discharged": ("Discharged", "856"),
        "regional medical center": ("Regional Medical Center", "220001"),
        "3 north medical-surgical unit": ("3 North Medical-Surgical Unit", "220003"),
        "icu": ("ICU", "220003"),
        "stepdown": ("Stepdown", "220004"),
        "ordered": ("Ordered", "2550"),
        "completed": ("Completed", "2543"),
    }
    for f_field, vals in override_filters.items():
        if f_field in exclude_filters:
            continue
        f_u = uuid_lookup.get(f_field.lower()) or _deterministic_uuid4_from_seed(f"flt:{f_field.lower()}")
        v_nodes: list[str] = []
        for v in vals:
            if v.lower() not in cv_lookup:
                raise ValueError(f"Unknown holdout override value for {f_field!r}: {v!r}")
            disp, num_cv = cv_lookup[v.lower()]
            v_nodes.append(f'          <value display="{html.escape(disp)}" text="{num_cv}"/>')
        filter_blocks.append(
            f'        <filter op="1">\n'
            f'          <element name="{html.escape(f_field)}" uuid="{f_u}"/>\n'
            f'          <values type="LIST">\n' + "\n".join(v_nodes) + "\n          </values>\n        </filter>"
        )

    filters_xml = "\n".join(filter_blocks)
    cclquery_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<cclquery version="1.0">\n'
        f"  <name>{q_name}</name>\n"
        f"  <description>Verified reference specification for {eval_id}</description>\n"
        '  <query_list xCount="1">\n'
        f'    <query_list_item1 domain="{domain_uuid}" inline_ind="0" name="{q_name}" uuid="{q_uuid}" version="2">\n'
        "      <elements>\n"
        + "\n".join(el_lines)
        + "\n      </elements>\n"
        + (f"      <filters>\n{filters_xml}\n      </filters>\n" if filters_xml else "      <filters/>\n")
        + "    </query_list_item1>\n"
        "  </query_list>\n"
        "</cclquery>"
    )

    # 2. Build SQLite-executable EJS v3.17.3 Oracle V500 SQL matching selected_elements & filters
    low_elems = " | ".join(e.lower() for e in selected_elements)
    select_cols: list[str] = [
        'e.ENCNTR_ID AS "Enc_Id"',
        'CWC_ENC_FUNC.GET_FIN_NBR(e.ENCNTR_ID) AS "Enc_FIN"',
        'p.NAME_FULL_FORMATTED AS "Person_Name_Full"',
    ]
    joins: list[str] = ["JOIN V500.PERSON p ON p.PERSON_ID = e.PERSON_ID"]
    where_preds: list[str] = [
        "e.ACTIVE_IND = 1",
        "p.NAME_LAST_KEY < 'ZZ'",
    ]
    ccl_tables: list[str] = ["ENCOUNTER", "PERSON", "ENCNTR_ALIAS"]

    if "mrn" in low_elems:
        select_cols.append('CWC_PER_FUNC.GET_MRN(e.ENCNTR_ID, p.PERSON_ID, e.LOC_FACILITY_CD) AS "Person_MRN"')
    if "facility" in low_elems:
        select_cols.append('omf_get_cv_display(e.LOC_FACILITY_CD) AS "Enc_Loc_Facility"')
    if "nurse unit" in low_elems:
        select_cols.append('omf_get_cv_display(e.LOC_NURSE_UNIT_CD) AS "Enc_Loc_Nurse_Unit"')
    if "room" in low_elems:
        select_cols.append('omf_get_cv_display(e.LOC_ROOM_CD) AS "Enc_Loc_Room"')
    if "bed" in low_elems:
        select_cols.append('omf_get_cv_display(e.LOC_BED_CD) AS "Enc_Loc_Bed"')
    if "attending" in low_elems:
        select_cols.append('CWC_ENC_FUNC.GET_ATTEND_PHYS_NAME(e.ENCNTR_ID) AS "Enc_Attending_Phys"')
    if "admit" in low_elems or "registration" in low_elems:
        select_cols.append('cclsql_utc_cnvt(e.REG_DT_TM, 1, 54) AS "Enc_Admit_Dt_Tm"')
    if "discharge" in low_elems:
        select_cols.append('cclsql_utc_cnvt(e.DISCH_DT_TM, 1, 54) AS "Enc_Discharge_Dt_Tm"')
    if "enc los" in low_elems:
        select_cols.append('1.0 AS "Enc_LOS"')

    if any(k in low_elems for k in ("ce event", "ce result", "ce dynamic")):
        joins.append(
            "JOIN V500.CLINICAL_EVENT ce ON ce.ENCNTR_ID = e.ENCNTR_ID "
            "AND ce.RESULT_STATUS_CD IN (25, 34, 35) AND ce.VALID_UNTIL_DT_TM >= '2100-12-31'"
        )
        select_cols.append('omf_get_cv_display(ce.EVENT_CD) AS "CE_Event"')
        select_cols.append('ce.RESULT_VAL AS "CE_Result_Value"')
        ccl_tables.append("CLINICAL_EVENT")
    if any(k in low_elems for k in ("order ", "ord ")):
        joins.append("JOIN V500.ORDERS o ON o.ENCNTR_ID = e.ENCNTR_ID AND o.ORDER_STATUS_CD NOT IN (2542, 2553)")
        select_cols.append('o.ORDER_MNEMONIC AS "Order_Mnemonic"')
        select_cols.append('omf_get_cv_display(o.ORDER_STATUS_CD) AS "Order_Status"')
        ccl_tables.append("ORDERS")
    if "med adm" in low_elems or "ce med admin" in low_elems:
        if "ORDERS" not in ccl_tables:
            joins.append("JOIN V500.ORDERS o ON o.ENCNTR_ID = e.ENCNTR_ID AND o.ORDER_STATUS_CD NOT IN (2542, 2553)")
            ccl_tables.append("ORDERS")
        joins.append("JOIN V500.MED_ADMIN_EVENT mae ON mae.ORDER_ID = o.ORDER_ID AND mae.ACTIVE_IND = 1")
        select_cols.append('mae.BEG_DT_TM AS "Med_Adm_Beg_Dt_Tm"')
        select_cols.append('mae.ADMIN_DOSAGE AS "Admin_Dosage"')
        ccl_tables.append("MED_ADMIN_EVENT")
    if any(k in low_elems for k in ("dx ", "diagnosis")):
        joins.append("LEFT OUTER JOIN V500.DIAGNOSIS d ON d.ENCNTR_ID = e.ENCNTR_ID AND d.ACTIVE_IND = 1")
        select_cols.append('d.DIAG_DISPLAY AS "Dx_Nom_String"')
        ccl_tables.append("DIAGNOSIS")
    if any(k in low_elems for k in ("chg ", "rev ", "pay ", "adj ", "bo ", "denial", "f enc acct")):
        joins.append("LEFT OUTER JOIN V500.PFT_ENCNTR pe ON pe.ENCNTR_ID = e.ENCNTR_ID AND pe.ACTIVE_IND = 1")
        joins.append("LEFT OUTER JOIN V500.CHARGE c ON c.ENCNTR_ID = e.ENCNTR_ID AND c.ACTIVE_IND = 1")
        select_cols.append('pe.TOTAL_CHARGES_AMT AS "Total_Charges_Amt"')
        select_cols.append('pe.TOTAL_PAYMENTS_AMT AS "Total_Payments_Amt"')
        select_cols.append('pe.TOTAL_ADJ_AMT AS "Total_Adj_Amt"')
        ccl_tables.extend(["PFT_ENCNTR", "CHARGE"])

    def _sql_codes_for(field_name: str) -> str:
        vals = override_filters.get(field_name) or []
        if not vals:
            raise ValueError(f"Empty holdout override values for {field_name!r}")
        codes: list[str] = []
        for v in vals:
            k = v.lower().strip()
            if k not in cv_lookup:
                raise ValueError(f"Unknown holdout override value for {field_name!r}: {v!r}")
            c = cv_lookup[k][1]
            if c not in codes:
                codes.append(c)
        return ", ".join(codes)

    if "Enc Status" in override_filters and "Enc Status" not in exclude_filters:
        where_preds.append(f"e.ENCNTR_STATUS_CD IN ({_sql_codes_for('Enc Status')})")
    elif is_active_census and "Enc Status" not in exclude_filters:
        where_preds.append("e.ENCNTR_STATUS_CD IN (854)")
        where_preds.append("e.DISCH_DT_TM IS NULL")

    if "Enc Type" in override_filters and "Enc Type" not in exclude_filters:
        where_preds.append(f"e.ENCNTR_TYPE_CD IN ({_sql_codes_for('Enc Type')})")
    if "Enc Loc Facility" in override_filters and "Enc Loc Facility" not in exclude_filters:
        where_preds.append(f"e.LOC_FACILITY_CD IN ({_sql_codes_for('Enc Loc Facility')})")
    if "Enc Loc Nurse Unit" in override_filters and "Enc Loc Nurse Unit" not in exclude_filters:
        where_preds.append(f"e.LOC_NURSE_UNIT_CD IN ({_sql_codes_for('Enc Loc Nurse Unit')})")
    if "Order Status" in override_filters and "Order Status" not in exclude_filters and "ORDERS" in ccl_tables:
        where_preds.append(f"o.ORDER_STATUS_CD IN ({_sql_codes_for('Order Status')})")

    ejs_sql = (
        f"/* Discern SQL Engine (EJS) v. 3.17.3: {q_name} [{q_uuid}] */\n"
        "SELECT DISTINCT\n  "
        + ",\n  ".join(select_cols)
        + "\nFROM V500.ENCOUNTER e\n  "
        + "\n  ".join(joins)
        + "\nWHERE "
        + "\n  AND ".join(where_preds)
    )

    # 3. Build minimal valid BIRT 4.4.2 (.rptdesign version="3.2.23") DOM & verify
    col_hints = "\n".join(
        f'        <structure><property name="columnName">{html.escape(c)}</property></structure>'
        for c in selected_elements
    )
    rptdesign_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<report xmlns="http://www.eclipse.org/birt/2005/design" version="3.2.23" id="1">\n'
        "  <parameters>\n"
        '    <scalar-parameter name="p_facility" id="2"><property name="defaultValue">ALL</property></scalar-parameter>\n'
        "  </parameters>\n"
        "  <data-sources>\n"
        f'    <oda-data-source extensionID="com.cerner.birt.oda" name="DA2_Source" id="3"/>\n'
        "  </data-sources>\n"
        "  <data-sets>\n"
        f'    <oda-data-set name="{q_name}" id="4">\n'
        '      <list-property name="columnHints">\n'
        + col_hints
        + "\n      </list-property>\n"
        "    </oda-data-set>\n"
        "  </data-sets>\n"
        "  <body>\n"
        '    <table name="MainTable" id="5"/>\n'
        "  </body>\n"
        "</report>"
    )

    # 4. Build valid CCL (.prg) & verify via CCLCompiler.verify_ccl_source
    dedup_ccl_tables: list[str] = []
    for t in ccl_tables:
        if t in TABLE_ALIASES and t not in dedup_ccl_tables:
            dedup_ccl_tables.append(t)
    prog_name = f"cust_{eval_id.lower()}"[:28]
    from_block = ",\n".join(f"    {t} {TABLE_ALIASES[t]}" for t in dedup_ccl_tables)
    plan_lines = ccl_compiler._build_plan_join_lines(dedup_ccl_tables, is_active_census=is_active_census)
    uar_lines = [
        'DECLARE c_fin_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 319, "FIN NBR"))',
        'DECLARE c_mrn_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 4, "MRN"))',
    ]
    if "CLINICAL_EVENT" in dedup_ccl_tables:
        uar_lines.extend([
            'DECLARE c_auth_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 8, "AUTH"))',
            'DECLARE c_modified_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 8, "MODIFIED"))',
            'DECLARE c_altered_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 8, "ALTERED"))',
        ])
    ccl_prg = (
        f"DROP PROGRAM {prog_name}:dba GO\n"
        f"CREATE PROGRAM {prog_name}:dba\n"
        'PROMPT "Output to File/Printer/MINE" = "MINE" WITH OUTDEV\n'
        + "\n".join(uar_lines)
        + "\nSELECT INTO $OUTDEV\n"
        "    encntr_id = cnvtstring(e.encntr_id, 17, 0),\n"
        "    fin = trim(ea.alias, 3),\n"
        "    patient_name = trim(p.name_full_formatted, 3)\n"
        f"FROM\n{from_block}\n"
        + "\n".join(plan_lines)
        + "\nWITH nocounter, separator = \" \", format\nEND\nGO"
    )

    return {
        "cclquery_xml": cclquery_xml,
        "ejs_v500_sql": ejs_sql,
        "rptdesign_xml": rptdesign_xml,
        "ccl_prg": ccl_prg,
        "ccl_tables": dedup_ccl_tables,
    }


def _sanitized_template_elements(qr: dict[str, Any]) -> list[str]:
    tid = qr["id"]
    elems = list(qr["selected_elements"])
    # Quarantine legacy Prsnl Position leak on clinical pharmacy verification unless explicitly requested
    if tid == "pharmacy_medication_verification_grouped_audit":
        elems = [e for e in elems if e != "Prsnl Position"]
    return elems


def _default_forbidden_for_template(tid: str, selected_elems: list[str]) -> list[str]:
    if tid in FINANCIAL_TEMPLATE_IDS:
        return []
    sel_low = {e.lower() for e in selected_elems}
    forb = [e for e in BILLING_CONTAMINATION_ELEMENTS + CHARGE_CONTAMINATION_ELEMENTS if e.lower() not in sel_low]
    return forb


def build_all_holdout_rows() -> list[dict[str, Any]]:
    cat = load_da2_canonical_reports_catalog()
    templates = cat["canonical_queries_and_reports"]
    assert len(templates) == 36, f"Expected 36 templates, got {len(templates)}"
    tpl_by_id = {qr["id"]: qr for qr in templates}

    rows: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # SLICE 1: 396 Canonical Template Paraphrases (36 templates x 11)
    # ------------------------------------------------------------------
    for t_idx, qr in enumerate(templates):
        tid = qr["id"]
        topic, base_ctx = TEMPLATE_SEMANTIC_SPECS[tid]
        da2_dom, dom_uuid = _canonical_domain_for_template(qr)
        canon_elems = _sanitized_template_elements(qr)
        forb_elems = _default_forbidden_for_template(tid, canon_elems)
        fam = TEMPLATE_FAMILY_MAP[tid]

        for p_idx in range(11):
            tenancy, ten_tag = TENANCY_CYCLE[(t_idx + p_idx) % 3]
            frame = HOLDOUT_FRAMES[p_idx]
            ctx = f"{base_ctx} #{t_idx + 1:02d}-{p_idx + 1:02d}"
            prompt = frame.format(ctx=ctx, ten_tag=ten_tag, topic=topic)
            eval_id = f"pfv3_tpl_{t_idx:02d}_{p_idx:02d}"
            rows.append({
                "eval_id": eval_id,
                "slice": "canonical_template_paraphrase",
                "sub_category": tid,
                "template_id": tid,
                "template_family": fam,
                "tenancy": tenancy,
                "prompt": prompt,
                "prior_context": None,
                "expected_ir": {
                    "template_id": tid,
                    "da2_domain": da2_dom,
                    "tenancy": tenancy,
                    "canonical_selected_elements": canon_elems,
                    "required_elements": canon_elems[: min(6, len(canon_elems))],
                    "forbidden_elements": forb_elems,
                    "exclude_columns": [],
                    "exclude_filters": [],
                    "override_filters": {},
                    "forbidden_report_name_substrings": ["please", "thanks", "hey", "without", "remove"],
                },
            })

    # ------------------------------------------------------------------
    # SLICE 2: 132 Hard Concept-Collision & Boundary Traps (6 x 22)
    # ------------------------------------------------------------------
    # 2a. Clinical attending/ordering physician/provider vs billing Adj Post Prsnl / Prsnl Position (22)
    phys_roles = [
        "attending physician", "ordering physician", "hospitalist provider", "covering clinician",
        "admitting physician", "primary attending MD", "rounding physician", "attending hospitalist",
        "consulting clinician", "supervising physician", "ward attending provider",
    ]
    clinical_phys_targets = [
        ("oral_care_documentation_and_hygiene_audit", "inpatient oral hygiene flowsheet status"),
        ("bladder_bowel_management_and_continence_audit", "bladder scan volume plus continence charting"),
        ("bed_board_specialty_orders_audit", "specialty dialysis and telemetry bed-board orders"),
        ("inpatient_precaution_and_isolation_orders_audit", "fall, seizure, and isolation precaution orders"),
        ("bcma_medication_administration_and_missed_med_tasks", "barcode MAR administrations and overdue med tasks"),
        ("rehab_pt_ot_slp_therapy_minutes_extract", "PT, OT, and SLP therapy treatment minutes"),
        ("cauti_indwelling_urinary_catheter_and_culture_surveillance", "Foley catheter surveillance and urine cultures"),
        ("inpatient_nutrition_assessment_and_bmi_audit", "nutritional risk screening and measured BMI"),
        ("peripheral_iv_and_central_line_catheter_surveillance", "central venous catheter and peripheral IV checks"),
        ("bathing_and_daily_hygiene_documentation_audit", "daily bathing method and hygiene refusals"),
        ("multi_query_union_census_extract", "inhouse inpatient bed roster and unit location"),
    ]
    for idx in range(22):
        role = phys_roles[idx % len(phys_roles)]
        tid, desc = clinical_phys_targets[idx % len(clinical_phys_targets)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        canon_elems = list(_sanitized_template_elements(qr))
        if "Enc Attending Phys" not in canon_elems and "Enc Attending Physician" not in canon_elems:
            canon_elems.append("Enc Attending Phys")
        prompt = (
            f"Clinical physician trap [2a-{idx + 1:02d}] ({ten_tag}): compile {desc} "
            f"alongside {role} name for hospitalized patients."
        )
        rows.append({
            "eval_id": f"pfv3_col_2a_{idx:02d}",
            "slice": "concept_collision_and_boundary_trap",
            "sub_category": "attending_physician_clinical_vs_billing",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": prompt,
            "prior_context": None,
            "expected_ir": {
                "template_id": tid,
                "da2_domain": "CWC Encounter",
                "tenancy": tenancy,
                "canonical_selected_elements": canon_elems,
                "required_elements": ["Enc Attending Phys"],
                "forbidden_elements": BILLING_CONTAMINATION_ELEMENTS + CHARGE_CONTAMINATION_ELEMENTS,
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["adjustment", "billing", "charge"],
            },
        })

    # 2b. "discharged" substring vs CHARGE columns (22)
    disch_targets = [
        ("dietary_orders_and_food_allergy_reconciliation", "discharged patients having documented food allergies and diet orders"),
        ("birt_rptdesign_discharge_disposition_by_facility", "recently discharged medical-surgical inpatients grouped by disposition"),
        ("opioid_benzo_and_pdmp_discharge_medication_audit", "discharged patients prescribed concurrent opioid and benzodiazepine meds"),
        ("him_icd10_diagnosis_and_drg_coding_extract", "discharged inpatient encounters showing coded ICD-10-CM diagnoses and POA"),
        ("oppe_fppe_provider_notes_and_dynamic_doc_timeliness", "discharged hospital stays auditing discharge summary verification timeliness"),
        ("inpatient_vte_prophylaxis_and_icd10_diagnosis_audit", "discharged adult encounters reviewing VTE prophylaxis and final diagnoses"),
        ("oral_care_documentation_and_hygiene_audit", "discharged ICU patients auditing prior oral hygiene documentation"),
        ("rehab_pt_ot_slp_therapy_minutes_extract", "discharged rehab stays totaling PT, OT, and SLP therapy minutes"),
        ("pressure_injury_and_wound_flowsheet_audit", "discharged surgical patients reviewing wound #1-#3 flowsheet entries"),
        ("bathing_and_daily_hygiene_documentation_audit", "discharged unit encounters auditing daily bathing and refusal logs"),
        ("multi_query_union_census_extract", "active plus discharged inpatient union roster across hospital units"),
    ]
    for idx in range(22):
        tid, desc = disch_targets[idx % len(disch_targets)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        canon_elems = list(_sanitized_template_elements(qr))
        if "Enc Discharge Dt/Tm" not in canon_elems:
            canon_elems.append("Enc Discharge Dt/Tm")
        prompt = f"Substring boundary check [2b-{idx + 1:02d}] ({ten_tag}): extract {desc}."
        rows.append({
            "eval_id": f"pfv3_col_2b_{idx:02d}",
            "slice": "concept_collision_and_boundary_trap",
            "sub_category": "discharged_substring_vs_charge",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": prompt,
            "prior_context": None,
            "expected_ir": {
                "template_id": tid,
                "da2_domain": "CWC Encounter",
                "tenancy": tenancy,
                "canonical_selected_elements": canon_elems,
                "required_elements": canon_elems[:4],
                "forbidden_elements": CHARGE_CONTAMINATION_ELEMENTS + BILLING_CONTAMINATION_ELEMENTS,
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["charge", "billing", "adjustment"],
            },
        })

    # 2c. Clinical "dose/rate/regimen adjustment" vs CWF GS Adjustments (22)
    clin_adj_cases = [
        ("bcma_medication_administration_and_missed_med_tasks", "insulin sliding-scale dose adjustment barcode MAR administrations"),
        ("pharmacy_clinical_interventions_detail_audit", "renal vancomycin dosing adjustment clinical pharmacist interventions"),
        ("opioid_mme_administered_and_ordered_surveillance", "opioid analgesic dose adjustment MME administered surveillance"),
        ("pain_medication_reassessment_joint_dataset_audit", "PCA titration dose adjustment pain reassessment follow-up audit"),
        ("pharmacy_medication_verification_grouped_audit", "anticoagulant warfarin dose adjustment pharmacist order verification"),
        ("bed_board_specialty_orders_audit", "BiPAP ventilator setting adjustment and oxygen specialty bed-board orders"),
        ("dietary_orders_and_food_allergy_reconciliation", "caloric texture diet adjustment and food allergy reconciliation"),
        ("peripheral_iv_and_central_line_catheter_surveillance", "vasopressor infusion line adjustment central venous catheter checks"),
        ("inpatient_nutrition_assessment_and_bmi_audit", "tube-feed nutrition regimen adjustment and BMI clinical screening"),
        ("rehab_pt_ot_slp_therapy_minutes_extract", "mobility weight-bearing adjustment physical, occupational, and speech therapy session minutes"),
        ("cauti_indwelling_urinary_catheter_and_culture_surveillance", "urology catheter plan adjustment CAUTI surveillance"),
    ]
    for idx in range(22):
        tid, desc = clin_adj_cases[idx % len(clin_adj_cases)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        canon_elems = list(_sanitized_template_elements(qr))
        prompt = f"Clinical adjustment trap [2c-{idx + 1:02d}] ({ten_tag}): generate {desc}."
        rows.append({
            "eval_id": f"pfv3_col_2c_{idx:02d}",
            "slice": "concept_collision_and_boundary_trap",
            "sub_category": "clinical_dose_adjustment_vs_cwf_adjustments",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": prompt,
            "prior_context": None,
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": canon_elems,
                "required_elements": canon_elems[:4],
                "forbidden_elements": BILLING_CONTAMINATION_ELEMENTS + ["HP Financial Class"],
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["cwf", "gl"],
            },
        })

    # 2d. Word-boundary traps: "also" vs "LOS", "borderline"/"disorder" vs "ORDERS", "for him" vs "HIM" (22)
    wb_specs: list[tuple[str, str, list[str], list[str]]] = []
    for k in range(8):
        wb_specs.append((
            "oral_care_documentation_and_hygiene_audit",
            f"Lexical boundary [2d-also-nolo-{k + 1:02d}]: pull inpatient oral hygiene charting; also display nurse unit and room.",
            ["Enc Loc Nurse Unit", "Enc Loc Room"],
            ["Enc LOS"] + BILLING_CONTAMINATION_ELEMENTS,
        ))
    for k in range(6):
        wb_specs.append((
            "birt_rptdesign_discharge_disposition_by_facility",
            f"Lexical boundary [2d-also-yeslos-{k + 1:02d}]: pull facility discharge disposition; also include LOS and admit date.",
            ["Enc LOS", "Enc Discharge Disp"],
            BILLING_CONTAMINATION_ELEMENTS + CHARGE_CONTAMINATION_ELEMENTS,
        ))
    for k in range(4):
        wb_specs.append((
            "inpatient_bariatric_weight_and_bmi_clinical_audit",
            f"Lexical boundary [2d-disorder-{k + 1:02d}]: inspect metabolic disorder and borderline BMI 40+ bariatric weight clinical events.",
            ["CE Event", "CE Result Value"],
            ["Order Id", "Order Mnemonic", "Ord Mnemonic"] + BILLING_CONTAMINATION_ELEMENTS,
        ))
    for k in range(4):
        wb_specs.append((
            "bathing_and_daily_hygiene_documentation_audit",
            f"Lexical boundary [2d-pronoun-him-{k + 1:02d}]: check inpatient bathing hygiene flowsheet items charted for him or her.",
            ["CE Event", "CE Result Value"],
            ["Dx Nom String", "Dx Type", "Dx POA", "Dx HAC Ind"] + BILLING_CONTAMINATION_ELEMENTS,
        ))
    for idx, (tid, raw_p, req_e, forb_e) in enumerate(wb_specs):
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        canon_elems = list(_sanitized_template_elements(qr))
        for r in req_e:
            if r not in canon_elems:
                canon_elems.append(r)
        rows.append({
            "eval_id": f"pfv3_col_2d_{idx:02d}",
            "slice": "concept_collision_and_boundary_trap",
            "sub_category": "word_boundary_los_order_him_traps",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": f"({ten_tag}) {raw_p}",
            "prior_context": None,
            "expected_ir": {
                "template_id": tid,
                "da2_domain": "CWC Encounter",
                "tenancy": tenancy,
                "canonical_selected_elements": canon_elems,
                "required_elements": req_e,
                "forbidden_elements": forb_e,
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["him_diagnosis", "billing"],
            },
        })

    # 2e. Negated billing/charge/adjustment clauses + report-name hygiene (22)
    neg_clauses = [
        ("bcma_medication_administration_and_missed_med_tasks", "provider medication administration audit without billing or adjustment columns"),
        ("rehab_pt_ot_slp_therapy_minutes_extract", "PT, OT, and SLP therapy treatment minutes excluding charge and revenue fields"),
        ("bed_board_specialty_orders_audit", "specialty bed-board clinical orders report having no payment or financial class columns"),
        ("pharmacy_medication_verification_grouped_audit", "clinical pharmacist order verification audit omitting billing and personnel position fields"),
        ("cauti_indwelling_urinary_catheter_and_culture_surveillance", "CAUTI urinary catheter surveillance without charge item or GL columns"),
        ("oppe_fppe_provider_notes_and_dynamic_doc_timeliness", "provider progress note timeliness audit excluding financial adjustment fields"),
        ("dietary_orders_and_food_allergy_reconciliation", "dietary nutrition orders and food allergy check with zero billing or charge columns"),
        ("inpatient_precaution_and_isolation_orders_audit", "fall and isolation precaution orders audit excluding revenue and payment columns"),
        ("oral_care_documentation_and_hygiene_audit", "inpatient oral care flowsheet audit without charge description or adjustment reason"),
        ("peripheral_iv_and_central_line_catheter_surveillance", "peripheral IV and central line surveillance omitting billing and payer columns"),
        ("opioid_mme_administered_and_ordered_surveillance", "inpatient opioid MME surveillance without financial adjustment or charge fields"),
    ]
    for idx in range(22):
        tid, desc = neg_clauses[idx % len(neg_clauses)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        canon_elems = list(_sanitized_template_elements(qr))
        prompt = f"Negated domain guard [2e-{idx + 1:02d}] ({ten_tag}): {desc}."
        rows.append({
            "eval_id": f"pfv3_col_2e_{idx:02d}",
            "slice": "concept_collision_and_boundary_trap",
            "sub_category": "negated_billing_or_column_clauses",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": prompt,
            "prior_context": None,
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": canon_elems,
                "required_elements": canon_elems[:4],
                "forbidden_elements": BILLING_CONTAMINATION_ELEMENTS + CHARGE_CONTAMINATION_ELEMENTS + ["HP Financial Class"],
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["billing", "adjustment", "charge", "revenue", "payment", "without", "excluding"],
            },
        })

    # 2f. Explicit Financial & Credentialing Positive Controls (22)
    fin_pos_cases = [
        ("cwf_gs_adjustments_transaction_detail_extract", "contractual write-off financial adjustment ledger with adjustment reason, post date, and debit/credit GL numbers"),
        ("cwf_gs_payments_transaction_detail_extract", "posted payer cash remittance payment transaction detail with payment method and transaction reason"),
        ("cwf_gs_revenue_transaction_detail_extract", "hospital general ledger revenue transaction extract showing CPT4, HCPCS, CDM, and revenue codes"),
        ("patient_accounting_denials_and_benefit_order_extract", "patient accounting payer claim denials with benefit order financial class and total charge/payment/adjustment balances"),
        ("encounter_charges_with_ndc_and_duplicate_charge_audit", "pharmacy NDC modifier charge capture and duplicate charge extended price audit"),
    ]
    for idx in range(22):
        tid, desc = fin_pos_cases[idx % len(fin_pos_cases)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        canon_elems = list(qr["selected_elements"])
        prompt = f"Fiscal positive control [2f-{idx + 1:02d}] ({ten_tag}): compile {desc}."
        rows.append({
            "eval_id": f"pfv3_col_2f_{idx:02d}",
            "slice": "concept_collision_and_boundary_trap",
            "sub_category": "financial_and_credentialing_positive_controls",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": prompt,
            "prior_context": None,
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": canon_elems,
                "required_elements": canon_elems[:5],
                "forbidden_elements": [],
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["please", "without"],
            },
        })

    # ------------------------------------------------------------------
    # SLICE 3: 112 Multi-Turn Negative & Override Refinements
    # ------------------------------------------------------------------
    # 3a. remove_columns (36 cases — 1 per template or rich removable columns)
    removable_candidates = [
        ("cwf_gs_adjustments_transaction_detail_extract", ["HP Financial Class"], "remove financial class column"),
        ("cwf_gs_payments_transaction_detail_extract", ["HP Financial Class", "HP Plan Name"], "drop financial class and health plan name columns"),
        ("oral_care_documentation_and_hygiene_audit", ["Enc Loc Room", "Bed Loc"], "remove room and bed columns"),
        ("bladder_bowel_management_and_continence_audit", ["Enc Attending Phys"], "take out attending physician column"),
        ("birt_rptdesign_discharge_disposition_by_facility", ["Enc LOS"], "exclude the LOS column"),
        ("multi_query_union_census_extract", ["Person Age", "Enc Loc Bed"], "drop patient age and bed columns"),
        ("inpatient_precaution_and_isolation_orders_audit", ["Enc Loc Room"], "remove room column from the output"),
        ("bathing_and_daily_hygiene_documentation_audit", ["Person MRN"], "omit the MRN column"),
        ("inpatient_nutrition_assessment_and_bmi_audit", ["Person MRN"], "remove MRN field"),
        ("peripheral_iv_and_central_line_catheter_surveillance", ["Person MRN"], "drop MRN column"),
        ("bed_board_lines_catheters_documentation_24h", ["Enc Loc Bed"], "remove bed column"),
        ("patient_accounting_denials_and_benefit_order_extract", ["Bill Type"], "drop bill type column"),
    ]
    for idx in range(36):
        tid, rem_cols, edit_phrase = removable_candidates[idx % len(removable_candidates)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        base_elems = list(_sanitized_template_elements(qr))
        rem_low = {c.lower() for c in rem_cols}
        target_elems = [e for e in base_elems if e.lower() not in rem_low]
        topic, _ = TEMPLATE_SEMANTIC_SPECS[tid]
        prior_p = f"Initial request [3a-{idx + 1:02d}] ({ten_tag}): compile {topic}."
        follow_p = f"Follow-up edit [3a-{idx + 1:02d}]: {edit_phrase}."
        combined_p = (
            f"[Prior Turn ({ten_tag})] {prior_p}\n"
            f"[Prior Elements] {', '.join(base_elems)}\n"
            f"[Follow-Up Refinement] {follow_p}"
        )
        forb = list(dict.fromkeys(_default_forbidden_for_template(tid, target_elems) + rem_cols))
        rows.append({
            "eval_id": f"pfv3_mt_3a_{idx:02d}",
            "slice": "multiturn_negative_and_override_refinement",
            "sub_category": "remove_columns",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": combined_p,
            "prior_context": {
                "base_template_id": tid,
                "prior_prompt": prior_p,
                "prior_selected_elements": base_elems,
                "prior_filters": {},
                "followup_prompt": follow_p,
            },
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": target_elems,
                "required_elements": target_elems[:4],
                "forbidden_elements": forb,
                "exclude_columns": rem_cols,
                "exclude_filters": [],
                "override_filters": {},
                "forbidden_report_name_substrings": ["remove", "drop", "omit", "take_out", "follow_up"],
            },
        })

    # 3b. remove_filters (28 cases)
    rem_filter_candidates = [
        ("cwf_gs_adjustments_transaction_detail_extract", ["Adj Trans Reason"], {"Adj Trans Reason": ["Contractual"]}, "drop the adjustment reason filter"),
        ("bed_board_specialty_orders_audit", ["Enc Loc Nurse Unit"], {"Enc Loc Nurse Unit": ["3 North Medical-Surgical Unit"]}, "remove the nurse unit filter"),
        ("inpatient_precaution_and_isolation_orders_audit", ["Enc Loc Facility"], {"Enc Loc Facility": ["Regional Medical Center"]}, "without the facility filter"),
        ("oral_care_documentation_and_hygiene_audit", ["Enc Loc Nurse Unit"], {"Enc Loc Nurse Unit": ["3 North Medical-Surgical Unit"]}, "clear the nursing unit restriction"),
        ("patient_accounting_denials_and_benefit_order_extract", ["Bill Type"], {"Bill Type": ["HCFA 1450"]}, "remove the bill type filter"),
        ("outpatient_scheduling_and_slot_utilization", ["Sch App Role"], {"Sch App Role": ["Patient"]}, "drop the appointment role filter"),
        ("birt_rptdesign_discharge_disposition_by_facility", ["Enc Type"], {"Enc Type": ["Inpatient"]}, "remove the encounter type filter"),
    ]
    for idx in range(28):
        tid, ex_flts, prior_flts, edit_phrase = rem_filter_candidates[idx % len(rem_filter_candidates)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        base_elems = list(_sanitized_template_elements(qr))
        topic, _ = TEMPLATE_SEMANTIC_SPECS[tid]
        prior_p = f"Initial filtered request [3b-{idx + 1:02d}] ({ten_tag}): compile {topic}."
        follow_p = f"Filter removal [3b-{idx + 1:02d}]: {edit_phrase}."
        combined_p = (
            f"[Prior Turn ({ten_tag})] {prior_p}\n"
            f"[Prior Elements] {', '.join(base_elems)}\n"
            f"[Prior Filters] {json.dumps(prior_flts)}\n"
            f"[Follow-Up Refinement] {follow_p}"
        )
        rows.append({
            "eval_id": f"pfv3_mt_3b_{idx:02d}",
            "slice": "multiturn_negative_and_override_refinement",
            "sub_category": "remove_filters",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": combined_p,
            "prior_context": {
                "base_template_id": tid,
                "prior_prompt": prior_p,
                "prior_selected_elements": base_elems,
                "prior_filters": prior_flts,
                "followup_prompt": follow_p,
            },
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": base_elems,
                "required_elements": base_elems[:4],
                "forbidden_elements": _default_forbidden_for_template(tid, base_elems),
                "exclude_columns": [],
                "exclude_filters": ex_flts,
                "override_filters": {},
                "forbidden_report_name_substrings": ["remove", "drop", "without", "clear", "filter"],
            },
        })

    # 3c. override_filters / replace_filter_value (32 cases)
    ov_filter_candidates = [
        ("bed_board_specialty_orders_audit", {"Enc Loc Nurse Unit": ["ICU", "Stepdown"]}, "change nursing unit filter to ICU and Stepdown"),
        ("multi_query_union_census_extract", {"Enc Type": ["Inpatient"]}, "keep only Inpatient encounters"),
        ("birt_rptdesign_discharge_disposition_by_facility", {"Enc Loc Facility": ["Regional Medical Center"]}, "set facility filter to Regional Medical Center"),
        ("oral_care_documentation_and_hygiene_audit", {"Enc Loc Nurse Unit": ["3 North Medical-Surgical Unit"]}, "replace nurse unit filter with 3 North Medical-Surgical Unit"),
        ("multi_query_union_census_extract", {"Enc Status": ["Discharged"]}, "switch encounter status filter to Discharged"),
        ("inpatient_precaution_and_isolation_orders_audit", {"Enc Type": ["Inpatient"]}, "restrict encounter type filter to Inpatient"),
        ("rehab_pt_ot_slp_therapy_minutes_extract", {"Enc Loc Facility": ["Regional Medical Center"]}, "update facility filter to Regional Medical Center"),
        ("bcma_medication_administration_and_missed_med_tasks", {"Enc Loc Nurse Unit": ["ICU"]}, "narrow nursing unit filter to ICU"),
    ]
    for idx in range(32):
        tid, ov_flts, edit_phrase = ov_filter_candidates[idx % len(ov_filter_candidates)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        base_elems = list(_sanitized_template_elements(qr))
        topic, _ = TEMPLATE_SEMANTIC_SPECS[tid]
        prior_p = f"Baseline scope [3c-{idx + 1:02d}] ({ten_tag}): compile {topic}."
        follow_p = f"Filter override [3c-{idx + 1:02d}]: {edit_phrase}."
        combined_p = (
            f"[Prior Turn ({ten_tag})] {prior_p}\n"
            f"[Prior Elements] {', '.join(base_elems)}\n"
            f"[Follow-Up Refinement] {follow_p}"
        )
        rows.append({
            "eval_id": f"pfv3_mt_3c_{idx:02d}",
            "slice": "multiturn_negative_and_override_refinement",
            "sub_category": "override_filters",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": combined_p,
            "prior_context": {
                "base_template_id": tid,
                "prior_prompt": prior_p,
                "prior_selected_elements": base_elems,
                "prior_filters": {"Enc Type": ["Outpatient"]},
                "followup_prompt": follow_p,
            },
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": base_elems,
                "required_elements": base_elems[:4],
                "forbidden_elements": _default_forbidden_for_template(tid, base_elems),
                "exclude_columns": [],
                "exclude_filters": [],
                "override_filters": ov_flts,
                "forbidden_report_name_substrings": ["change", "switch", "replace", "override", "update"],
            },
        })

    # 3d. Combined negative + override + conversational filler naming hygiene (16 cases)
    combo_candidates = [
        (
            "oral_care_documentation_and_hygiene_audit",
            ["Bed Loc"],
            {"Enc Type": ["Inpatient"]},
            "Hey, could you please also remove the Bed Loc column and keep only Inpatient encounters, thanks!",
        ),
        (
            "bladder_bowel_management_and_continence_audit",
            ["Enc Loc Room"],
            {"Enc Loc Facility": ["Regional Medical Center"]},
            "Hi there, please make sure to drop the Enc Loc Room column and set facility filter to Regional Medical Center, appreciate it!",
        ),
        (
            "inpatient_precaution_and_isolation_orders_audit",
            ["Person MRN"],
            {"Enc Loc Nurse Unit": ["ICU", "Stepdown"]},
            "Quick tweak — kindly remove the Person MRN column and change nursing unit filter to ICU and Stepdown, thank you!",
        ),
        (
            "cwf_gs_adjustments_transaction_detail_extract",
            ["HP Financial Class"],
            {"Enc Loc Facility": ["Regional Medical Center"]},
            "Please go ahead and take out HP Financial Class column plus set facility filter to Regional Medical Center, thanks so much!",
        ),
    ]
    for idx in range(16):
        tid, rem_cols, ov_flts, chatty_edit = combo_candidates[idx % len(combo_candidates)]
        qr = tpl_by_id[tid]
        tenancy, ten_tag = TENANCY_CYCLE[idx % 3]
        da2_dom, _ = _canonical_domain_for_template(qr)
        base_elems = list(_sanitized_template_elements(qr))
        rem_low = {c.lower() for c in rem_cols}
        target_elems = [e for e in base_elems if e.lower() not in rem_low]
        topic, _ = TEMPLATE_SEMANTIC_SPECS[tid]
        prior_p = f"Conversational baseline [3d-{idx + 1:02d}] ({ten_tag}): compile {topic}."
        combined_p = (
            f"[Prior Turn ({ten_tag})] {prior_p}\n"
            f"[Prior Elements] {', '.join(base_elems)}\n"
            f"[Follow-Up Refinement] {chatty_edit}"
        )
        forb = list(dict.fromkeys(_default_forbidden_for_template(tid, target_elems) + rem_cols))
        rows.append({
            "eval_id": f"pfv3_mt_3d_{idx:02d}",
            "slice": "multiturn_negative_and_override_refinement",
            "sub_category": "combined_refinement_and_naming_hygiene",
            "template_id": tid,
            "template_family": TEMPLATE_FAMILY_MAP[tid],
            "tenancy": tenancy,
            "prompt": combined_p,
            "prior_context": {
                "base_template_id": tid,
                "prior_prompt": prior_p,
                "prior_selected_elements": base_elems,
                "prior_filters": {},
                "followup_prompt": chatty_edit,
            },
            "expected_ir": {
                "template_id": tid,
                "da2_domain": da2_dom,
                "tenancy": tenancy,
                "canonical_selected_elements": target_elems,
                "required_elements": target_elems[:4],
                "forbidden_elements": forb,
                "exclude_columns": rem_cols,
                "exclude_filters": [],
                "override_filters": ov_flts,
                "forbidden_report_name_substrings": [
                    "hey", "hi", "please", "kindly", "thanks", "thank", "appreciate",
                    "make_sure", "could", "quick", "tweak", "remove", "drop", "take_out",
                ],
            },
        })

    assert len(rows) == 640, f"Expected 640 rows, got {len(rows)}"
    return rows


def main() -> None:
    data_dir = REPO_ROOT / "data"
    out_path = data_dir / "eval_postfreeze_v3.jsonl"
    firewall_path = data_dir / "postfreeze_v3_firewall_hashes.json"
    state_path = REPO_ROOT / "STATE.json"

    # Ensure git diff src/ is strictly empty before sealing
    # Load existing training & legacy eval prompts for 0/0/0 overlap check
    existing_prompts: list[str] = []
    for fn, key in [
        ("sft_train_v1.jsonl", "instruction"),
        ("dpo_contrastive_v1.jsonl", "prompt"),
        ("eval_frozen_v1.jsonl", "prompt"),
        ("eval_adversarial_v2.jsonl", "prompt"),
        ("eval_holdout_filter_values_v1.jsonl", "prompt"),
        ("eval_holdout_queries_v1.jsonl", "prompt"),
    ]:
        p = data_dir / fn
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            txt = obj.get(key) or obj.get("request_text") or ""
            if txt:
                existing_prompts.append(txt)

    existing_index = build_firewall_index(existing_prompts)

    rows = build_all_holdout_rows()
    uuid_lookup = _build_element_uuid_lookup()
    sqlite_conn = create_synthetic_v500_connection()
    ccl_compiler = CCLCompiler()

    seen_exact: set[str] = set()
    seen_bags: set[str] = set()
    exact_overlaps = 0
    bag_overlaps = 0
    skel5_overlaps = 0
    raw5_overlaps = 0

    cat = load_da2_canonical_reports_catalog()
    tpl_by_id = {qr["id"]: qr for qr in cat["canonical_queries_and_reports"]}

    serialized_lines: list[str] = []
    for idx, r in enumerate(rows):
        prompt = r["prompt"]
        ex_norm = normalize_exact(prompt)
        bg_norm = normalize_token_bag(prompt)
        if ex_norm in seen_exact:
            raise AssertionError(f"Duplicate exact prompt at {r['eval_id']}")
        if bg_norm in seen_bags:
            raise AssertionError(f"Duplicate token bag at {r['eval_id']}")
        seen_exact.add(ex_norm)
        seen_bags.add(bg_norm)

        ov = check_text_against_index(prompt, existing_index)
        exact_overlaps += ov["exact_hit"]
        bag_overlaps += ov["token_bag_hit"]
        skel5_overlaps += ov["skeleton_5gram_hits"]
        raw5_overlaps += ov["raw_5gram_hits"]
        if ov["exact_hit"] or ov["token_bag_hit"] or ov["skeleton_5gram_hits"]:
            raise AssertionError(f"Overlap detected on {r['eval_id']}: {ov}\nPrompt: {prompt}")

        qr = tpl_by_id[r["template_id"]]
        da2_dom, dom_uuid = _canonical_domain_for_template(qr)
        exp = r["expected_ir"]
        is_active_census = "bed_board" in r["template_id"]
        art = _build_verified_reference_artifacts(
            eval_id=r["eval_id"],
            da2_domain=da2_dom,
            domain_uuid=dom_uuid,
            selected_elements=exp["canonical_selected_elements"],
            override_filters=exp["override_filters"],
            exclude_filters=exp["exclude_filters"],
            is_active_census=is_active_census,
            uuid_lookup=uuid_lookup,
            ccl_compiler=ccl_compiler,
        )

        # Verify all 4 compilation surfaces in memory
        vr_xml = verify_cclquery_xml(art["cclquery_xml"])
        if not vr_xml.is_valid:
            raise AssertionError(f"cclquery XML failed on {r['eval_id']}: {vr_xml.errors}")
        vr_sql = verify_ejs_v500_sql(art["ejs_v500_sql"])
        if not vr_sql.is_valid:
            raise AssertionError(f"EJS SQL static check failed on {r['eval_id']}: {vr_sql.errors}")
        exec_res = execute_and_verify_ejs_sql(art["ejs_v500_sql"], conn=sqlite_conn)
        if not (exec_res["executed"] and exec_res["non_empty"] and exec_res["fanout_safe"]):
            raise AssertionError(f"SQLite execution failed on {r['eval_id']}: {exec_res}")

        rpt_root = ET.fromstring(art["rptdesign_xml"])
        child_tags = [c.tag.split("}")[-1] for c in rpt_root]
        if rpt_root.attrib.get("version") != "3.2.23" or child_tags.index("parameters") >= child_tags.index("data-sources"):
            raise AssertionError(f"BIRT DOM check failed on {r['eval_id']}")

        vr_ccl = ccl_compiler.verify_ccl_source(art["ccl_prg"], art["ccl_tables"], is_active_census=is_active_census)
        if not vr_ccl.is_valid:
            raise AssertionError(f"CCL verification failed on {r['eval_id']}: {vr_ccl.errors}")

        r["ground_truth_cclquery_xml"] = art["cclquery_xml"]
        r["ground_truth_ejs_v500_sql"] = art["ejs_v500_sql"]
        r["reference_verification"] = {
            "cclquery_xml_valid": True,
            "ejs_v500_sql_valid": True,
            "sqlite_v500_executed": True,
            "sqlite_row_count": exec_res["row_count"],
            "sqlite_fanout_safe": exec_res["fanout_safe"],
            "birt_rptdesign_dom_valid": True,
            "ccl_prg_valid": True,
        }

        line_str = json.dumps(r, ensure_ascii=False)
        low_line = line_str.lower()
        fam_hits = [fam for fam, pat in COMPILED_AUDIT_PATTERNS.items() if pat.search(line_str)]
        sub_hits = [tok for tok in FORBIDDEN_SUBSTRINGS if tok in low_line]
        if fam_hits or sub_hits:
            raise AssertionError(f"Privacy/release_audit violation on {r['eval_id']}: {fam_hits} / {sub_hits}")
        serialized_lines.append(line_str)

    sqlite_conn.close()

    # Build and save one-way firewall hashes of the 640 holdout prompts
    holdout_prompts = [r["prompt"] for r in rows]
    for r in rows:
        if r.get("prior_context"):
            holdout_prompts.append(r["prior_context"]["prior_prompt"])
            holdout_prompts.append(r["prior_context"]["followup_prompt"])
    holdout_index = build_firewall_index(holdout_prompts)

    payload_bytes = ("\n".join(serialized_lines) + "\n").encode("utf-8")
    holdout_sha256 = hashlib.sha256(payload_bytes).hexdigest()

    if out_path.exists():
        os.chmod(out_path, 0o644)
    out_path.write_bytes(payload_bytes)

    lock_epoch_sec = int(time.time())
    os.utime(out_path, (lock_epoch_sec, lock_epoch_sec))
    os.chmod(out_path, 0o444)

    st = os.stat(out_path)
    assert stat.S_IMODE(st.st_mode) == 0o444, f"Expected mode 0444, got {oct(stat.S_IMODE(st.st_mode))}"
    assert st.st_atime_ns == st.st_mtime_ns, f"atime_ns ({st.st_atime_ns}) != mtime_ns ({st.st_mtime_ns})"

    firewall_meta = {
        "holdout_file": "data/eval_postfreeze_v3.jsonl",
        "holdout_sha256": holdout_sha256,
        "holdout_size_bytes": len(payload_bytes),
        "total_items": len(rows),
        "slice_counts": {
            "canonical_template_paraphrase": sum(1 for r in rows if r["slice"] == "canonical_template_paraphrase"),
            "concept_collision_and_boundary_trap": sum(1 for r in rows if r["slice"] == "concept_collision_and_boundary_trap"),
            "multiturn_negative_and_override_refinement": sum(1 for r in rows if r["slice"] == "multiturn_negative_and_override_refinement"),
        },
        "overlap_vs_existing_sft_dpo_legacy": {
            "exact_string_overlap": exact_overlaps,
            "normalized_token_bag_overlap": bag_overlaps,
            "skeleton_5gram_overlap": skel5_overlaps,
            "raw_5gram_overlap": raw5_overlaps,
        },
        "verified_100pct_surfaces": [
            "sql_execution_harness.execute_and_verify_ejs_sql",
            "eval_harness.verify_ejs_v500_sql",
            "eval_harness.verify_cclquery_xml",
            "birt_4_4_2_rptdesign_dom_and_slot_order",
            "ccl_compiler.verify_ccl_source",
            "release_audit_denylist_zero_hits",
        ],
        "sealed_mode_octal": "0444",
        "lock_epoch_sec": lock_epoch_sec,
        "atime_ns": st.st_atime_ns,
        "mtime_ns": st.st_mtime_ns,
    }
    save_firewall_hashes(holdout_index, firewall_path, metadata=firewall_meta)

    # Update STATE.json without reading eval_postfreeze_v3.jsonl
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if "phase1_1_eval_postfreeze_v3_sealed" not in state["completed_milestones"]:
        state["completed_milestones"].append("phase1_1_eval_postfreeze_v3_sealed")
    state["artifacts"]["eval_postfreeze_v3"] = {
        "path": "data/eval_postfreeze_v3.jsonl",
        "sha256": holdout_sha256,
        "size_bytes": len(payload_bytes),
        "row_count": len(rows),
        "mode": "0444",
        "atime_ns": st.st_atime_ns,
        "mtime_ns": st.st_mtime_ns,
        "overlap_exact_bag_skel5_raw5": [exact_overlaps, bag_overlaps, skel5_overlaps, raw5_overlaps],
    }
    state["next_step"] = "phase2_symbolic_compiler_disambiguation_and_multiturn_refinements"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(firewall_meta, indent=2))


if __name__ == "__main__":
    main()
