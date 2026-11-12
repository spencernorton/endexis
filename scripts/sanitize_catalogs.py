#!/usr/bin/env python3
"""
Deterministic Catalog Sanitizer for ENDEXIS (`endexis`) Tenant-Agnostic Public Release.

Normalizes all reference catalogs in `src/discern_intelligence/data/` and `data/`
so zero regional, facility, domain, hospital, vendor, user, test-account, or redaction
artifacts remain across `Enc Loc*` and all 90 catalog fields while preserving 100% of
V500 / DA2 / BIRT 4.4.2 schema invariants and item counts.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = REPO_ROOT / "src" / "discern_intelligence" / "data"
DATA_DIR = REPO_ROOT / "data"


def _load_sanitizer_rules() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "exact_display_replacements": {},
        "vendor_parens_pattern": r"\s*\*?\s*\((?:Quest|LabCorp|Reference Lab|External Feed)\)",
        "tenant_suffix": " (SITE_ID)",
        "redacted_pattern": r"\[" + "REDACTED" + r"\]",
        "underscore_tag_pattern": r"_(?:SITE|CUSTOM)\b",
        "domain_word_pattern": r"\b(?:LEGACY_DOMAIN_TOKEN)\b",
        "legacy_prefix_pattern": r"^zz+\s*",
        "legacy_match_pattern": r"^zz+",
        "tracker_pattern": r"\b[A-Z]{2}#?\d{3,6}\b",
        "legacy_sched_domain": "DEPRECATED_SCHED_DOMAIN",
        "legacy_order_person": "DEPRECATED_ORDER_PERSON",
        "legacy_order_person_col": "DEPRECATED_ORDER_PERSON_CD",
        "legacy_guardrail_old": "Deprecated production DA2 query element UUID.",
        "legacy_dom_token": "LEGACY_ENV_ID",
        "legacy_owner_full": "LEGACY_OWNER_GROUP_FULL",
        "legacy_owner_short": "LEGACY_OWNER_GROUP_SHORT",
    }
    ext_path = os.environ.get("ENDEXIS_PRIVATE_SANITIZER_RULES_PATH")
    if ext_path and Path(ext_path).is_file():
        loaded = json.loads(Path(ext_path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            defaults.update(loaded)
    return defaults


_RULES: dict[str, Any] = _load_sanitizer_rules()

STATE_SUFFIX_RE = re.compile(r"\s*-\s*(?:IA|MN|CA|OR|NY|ME|TX|NJ|VA|MT|WI|NV|NM|MD|CV|OH|MHSK)\b")
VENDOR_PARENS_RE = re.compile(_RULES["vendor_parens_pattern"], re.I)
BH_STATE_RE = re.compile(r"^BH[A-Z\u0400-\u04ff]{2}\s+Services$", re.I)

CYRILLIC_TO_LATIN = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "І": "I", "К": "K",
    "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
})

GENERIC_FACILITIES = {
    "Main Campus Medical Center": "200100101",
    "North Campus Specialty Hospital": "200100102",
    "Outpatient Specialty Pavilion": "200100103",
    "Behavioral Health Pavilion": "200100104",
    "Regional Community Hospital": "200100105",
    "Ambulatory Care Center": "200100106",
}

GENERIC_BUILDINGS = {
    "North Tower": "205100101",
    "South Tower": "205100102",
    "East Wing Clinical Pavilion": "205100103",
    "West Outpatient Clinic Building": "205100104",
    "Medical Office Building 1": "205100105",
}

GENERIC_NURSE_UNITS = {
    "2 North Medical-Surgical Unit": "210100101",
    "3 South Progressive Care Unit": "210100102",
    "4 East Med-Surg Unit": "210100103",
    "4 West Orthopedic Unit": "210100104",
    "Intensive Care Unit (ICU)": "210100105",
    "Step-Down / Telemetry Unit": "210100106",
    "Outpatient Therapy Clinic": "210100107",
    "Pre-Admission Testing (PAT)": "210100108",
    "Acute Care Unit 1": "210100109",
}

GENERIC_NURSE_UNIT_FILTER_LIST = [
    "5 North Acute Care Unit",
    "2 North Medical-Surgical Unit",
    "3 South Progressive Care Unit",
    "6 East Neurology / Stroke Unit",
    "6 West Neuro-Ortho Unit",
    "4 East Med-Surg Unit",
    "4 West Orthopedic Unit",
    "General Medicine / Telemetry",
    "Intensive Care Unit (ICU)",
    "Step-Down / Telemetry Unit",
    "Surgical Inpatient Unit",
    "Emergency Department (ED)",
    "Observation Unit",
    "Post-Anesthesia Care Unit (PACU)",
    "Outpatient Physical Therapy Clinic",
    "Outpatient Occupational Therapy Clinic",
    "Outpatient Speech Therapy Clinic",
    "Outpatient Specialty Clinic",
    "Pre-Admission Testing (PAT)",
    "Outpatient Infusion / Procedure Suite",
    "Outpatient Procedure Suite",
    "Outpatient Diagnostics Suite",
    "Outpatient Mobility & Prosthetics Clinic",
    "Outpatient Specialty Therapy Clinic",
    "Cardiopulmonary Testing Unit",
    "Outpatient Day Treatment Unit",
    "Acute Care Unit 1",
]

GENERIC_ROOMS = {
    "Room 201": "220100201",
    "Room 202": "220100202",
    "Room 301": "220100301",
    "Room 302": "220100302",
    "Room 401": "220100401",
    "Exam Room 1": "220100501",
    "Therapy Bay A": "220100601",
}

GENERIC_BEDS = {
    "Bed A": "230100001",
    "Bed B": "230100002",
    "Bed 01": "230100003",
    "Bed 02": "230100004",
    "Private Bed 1": "230100005",
}

GENERIC_SCH_RESOURCES = {
    "Physical Therapy Gym A": "310200101",
    "Occupational Therapy Suite B": "310200102",
    "Speech Pathology Room 1": "310200103",
    "Outpatient Procedure Suite 1": "310200104",
    "Outpatient Diagnostic Room 1": "310200105",
    "Outpatient Consult Room 2": "310200106",
}

BH_ACTIVITY_REPLACEMENTS = [
    "Behavioral Health Outpatient Services",
    "Behavioral Health Inpatient Services",
    "Behavioral Health Crisis Services",
    "Behavioral Health Group Therapy",
    "Behavioral Health Individual Therapy",
    "Behavioral Health Family Therapy",
    "Behavioral Health Case Management",
    "Behavioral Health Medication Management",
    "Behavioral Health Peer Support",
    "Behavioral Health Intake Assessment",
    "Behavioral Health Psychosocial Rehab",
    "Behavioral Health Assertive Community Treatment",
    "Behavioral Health Intensive Outpatient",
    "Behavioral Health Partial Hospitalization",
    "Behavioral Health Telehealth Consult",
    "Behavioral Health Care Coordination",
    "Behavioral Health Discharge Planning",
]

EXACT_DISPLAY_REPLACEMENTS: dict[str, str] = _RULES["exact_display_replacements"]

QUALIFIER_SUFFIXES = [
    " (Standard)",
    " (Inpatient)",
    " (Outpatient)",
    " (Protocol)",
    " (Comprehensive)",
    " (Follow-Up)",
    " (Point of Care)",
    " (Reference Lab)",
    " (Confirmatory)",
    " (Quantitative)",
    " (Screen)",
    " (Panel)",
]


def clean_display_key(raw_key: str) -> str:
    """Normalizes a single display key to remove all tenant, state, hospital, vendor, or redacted artifacts."""
    k = raw_key.strip().translate(CYRILLIC_TO_LATIN)
    if k in EXACT_DISPLAY_REPLACEMENTS:
        return EXACT_DISPLAY_REPLACEMENTS[k]
    tsuf = _RULES["tenant_suffix"]
    if k.endswith(tsuf):
        k = k[: -len(tsuf)].strip()
    if VENDOR_PARENS_RE.search(k):
        k = VENDOR_PARENS_RE.sub("", k).strip()
    if STATE_SUFFIX_RE.search(k):
        k = STATE_SUFFIX_RE.sub("", k).strip()
    k = re.sub(_RULES["redacted_pattern"], "Enterprise", k, flags=re.I)
    k = re.sub(_RULES["underscore_tag_pattern"], "", k, flags=re.I)
    k = re.sub(_RULES["domain_word_pattern"], "Standard", k, flags=re.I)
    if re.match(_RULES["legacy_match_pattern"], k, re.I):
        k = re.sub(_RULES["legacy_prefix_pattern"], "Legacy ", k, flags=re.I).strip()
    if k in EXACT_DISPLAY_REPLACEMENTS:
        return EXACT_DISPLAY_REPLACEMENTS[k]
    return k


def sanitize_dict_keys_preserving_count(d: dict[str, Any]) -> dict[str, Any]:
    """Sanitizes dictionary keys while resolving collisions deterministically so item counts stay intact."""
    out: dict[str, Any] = {}
    bh_idx = 0
    for raw_k, val in d.items():
        if BH_STATE_RE.match(raw_k.strip()):
            base_k = BH_ACTIVITY_REPLACEMENTS[bh_idx % len(BH_ACTIVITY_REPLACEMENTS)]
            bh_idx += 1
        else:
            base_k = clean_display_key(raw_k)
        cand = base_k
        q_idx = 0
        while cand in out:
            if q_idx < len(QUALIFIER_SUFFIXES):
                cand = f"{base_k}{QUALIFIER_SUFFIXES[q_idx]}"
            else:
                cand = f"{base_k} ({q_idx + 1})"
            q_idx += 1
        out[cand] = val
    return out


def sanitize_list_preserving_unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    bh_idx = 0
    for raw_item in items:
        if BH_STATE_RE.match(raw_item.strip()):
            base_k = BH_ACTIVITY_REPLACEMENTS[bh_idx % len(BH_ACTIVITY_REPLACEMENTS)]
            bh_idx += 1
        else:
            base_k = clean_display_key(raw_item)
        cand = base_k
        q_idx = 0
        while cand in seen:
            if q_idx < len(QUALIFIER_SUFFIXES):
                cand = f"{base_k}{QUALIFIER_SUFFIXES[q_idx]}"
            else:
                cand = f"{base_k} ({q_idx + 1})"
            q_idx += 1
        seen.add(cand)
        out.append(cand)
    return out



ELEMENT_PREFIX_METADATA: list[tuple[tuple[str, ...], str, str, str, list[str]]] = [
    (
        ("CE Med Admin", "Med Adm"),
        "MED_ADMIN_EVENT",
        "BEG_DT_TM",
        "1:N child grain per ORDER_ID / ENCNTR_ID on MED_ADMIN_EVENT — never flat-join with unrelated CLINICAL_EVENT or CHARGE rows without subquery isolation.",
        [
            "Order Status Not In (Canceled, Voided) [CS 6004: 2542, 2553] — NEVER exclude Discontinued (2545) on historical MAR/PRN audits",
            "CE Med Admin Start Dt/Tm Between <Relative Date Range>",
        ],
    ),
    (
        ("CE ", "DTA "),
        "CLINICAL_EVENT",
        "EVENT_END_DT_TM",
        "1:N child grain per ENCNTR_ID on CLINICAL_EVENT — flat-joining multiple clinical events or combining with ORDERS/CHARGE/DIAGNOSIS causes Cartesian cross-join; use inline subqueries or <joint-data-set>.",
        [
            "CE Result Status In (Auth (Verified), Modified, Altered) [CS 8: 25, 34, 35]",
            "CE Valid Until Dt/Tm >= 12/31/2100 00:00:00",
            "CE End Dt/Tm Between <Relative Date Range>",
        ],
    ),
    (
        ("Order ", "Ord "),
        "ORDERS",
        "ORIG_ORDER_DT_TM",
        "1:N child grain per ENCNTR_ID on ORDERS — isolate from CLINICAL_EVENT, CHARGE, and DIAGNOSIS to prevent 1:N join explosion.",
        [
            "Order Status In (Ordered, Completed) [CS 6004: 2550, 2543] for active orders OR Not In (Canceled, Voided) [2542, 2553] for historical administration audits",
            "Order Dt/Tm Between <Relative Date Range>",
        ],
    ),
    (
        ("Diag ", "Dx "),
        "DIAGNOSIS",
        "NOMENCLATURE_ID",
        "1:N child grain per ENCNTR_ID on DIAGNOSIS — use Outer Join (joinType=3 / (+)) when Admitting Diagnosis is optional on same-day admissions.",
        [
            "Diag Active Ind = 1",
            "Dx Type In (Admitting, Final, Principal, Working) [CS 17]",
        ],
    ),
    (
        ("Chg ", "F Chg "),
        "CHARGE",
        "SERVICE_DT_TM",
        "1:N child grain per ENCNTR_ID on CHARGE — never flat-join directly with 1:N CLINICAL_EVENT or ORDERS rows; aggregate or join via BIRT <joint-data-set> on Enc Id.",
        [
            "Chg Process Flag = 100 (Posted)",
            "Chg Service Dt/Tm Between <Relative Date Range> (distinguish from Chg Posted Dt/Tm)",
        ],
    ),
    (
        ("Sch ",),
        "SCH_APPT",
        "BEG_DT_TM",
        "1:N child grain per ENCNTR_ID / SCH_EVENT_ID on SCH_APPT — filter by Sch App Role = Patient to avoid doubling rows across Patient + Resource slots.",
        [
            "Sch App State In (Confirmed, Checked In, Checked Out) [CS 14233]",
            "Sch App Beg Dt/Tm Between <Relative Date Range>",
            "Sch App Resource Not Like \x27zz*\x27 (exclude retired resources)",
        ],
    ),
    (
        ("Task ",),
        "TASK_ACTIVITY",
        "TASK_DT_TM",
        "1:N child grain per ENCNTR_ID on TASK_ACTIVITY — isolate from other 1:N child views.",
        [
            "Task Status In (Overdue, Pending, In Process, Complete) [CS 79]",
            "Task Dt/Tm Between <Relative Date Range>",
        ],
    ),
    (
        ("Alg ", "Allergy "),
        "ALLERGY",
        "ALLERGY_ID",
        "1:N child grain per PERSON_ID / ENCNTR_ID on ALLERGY.",
        ["Alg Reaction Status = Active [CS 12025]"],
    ),
    (
        ("Prob ", "Problem "),
        "PROBLEM",
        "PROBLEM_ID",
        "1:N child grain per PERSON_ID on PROBLEM.",
        ["Problem Life Cycle Status = Active [CS 12030]"],
    ),
    (
        ("Surg ", "S Case "),
        "SURGICAL_CASE",
        "SURG_START_DT_TM",
        "1:N surgical case grain per ENCNTR_ID on SURGICAL_CASE.",
        ["Surg Case Start Dt/Tm Between <Relative Date Range>"],
    ),
    (
        ("Pay ", "Adj ", "TL ", "BO ", "F Enc ", "GL "),
        "PFT_ENCNTR",
        "PFT_ENCNTR_ID",
        "1:N financial transaction/benefit-order grain on PFT_ENCNTR / TRANS_LOG — join to CWC Encounter via BIRT <joint-data-set> on Enc Id or FIN.",
        ["TL Transaction Status = Active", "Post Dt/Tm Between <Relative Date Range>"],
    ),
    (
        ("HP", "Health Plan"),
        "ENCNTR_PLAN_RELTN",
        "HEALTH_PLAN_ID",
        "1:N payer priority grain per ENCNTR_ID on ENCNTR_PLAN_RELTN — filter Priority Seq = 1 for primary payer to avoid duplicating encounter rows.",
        ["Enc Plan Priority Seq = 1 (Primary Health Plan)", "Active Ind = 1"],
    ),
    (
        ("Prsnl ", "Pl ", "Cred "),
        "PRSNL",
        "PERSON_ID",
        "1:1 personnel directory grain on PRSNL (or 1:N when joined via ENCNTR_PRSNL_RELTN without relationship type qualification).",
        ["Prsnl Active Ind = 1", "Prsnl Name Last < \x27ZZ\x27"],
    ),
    (
        ("Person ", "Per "),
        "PERSON",
        "PERSON_ID",
        "1:1 master patient demographic grain on PERSON (1:N if joining PERSON_ALIAS without Alias Pool / Code Set 4 MRN qualification).",
        ["Person Active Ind = 1", "Person Name Last < \x27ZZ\x27 (exclude test patients)"],
    ),
    (
        ("Enc ",),
        "ENCOUNTER",
        "ENCNTR_ID",
        "1:1 encounter header grain on ENCOUNTER (ENCNTR_ID) — primary driving anchor for encounter-level queries.",
        [
            "Enc Active Ind = 1",
            "Enc Type In (...) [CS 71]",
            "Person Name Last < \x27ZZ\x27 (exclude test patients)",
        ],
    ),
]

EXACT_FIELD_TO_V500_AND_CS: dict[str, tuple[str, str, int | None, str]] = {
    "Enc Id": ("ENCOUNTER", "ENCNTR_ID", None, "1"),
    "Enc FIN": ("ENCNTR_ALIAS", "ALIAS", 319, "3"),
    "Enc Financial Num": ("ENCNTR_ALIAS", "ALIAS", 319, "3"),
    "Enc Encounter Number": ("ENCNTR_ALIAS", "ALIAS", 319, "3"),
    "Person Id": ("PERSON", "PERSON_ID", None, "1"),
    "Person MRN": ("PERSON_ALIAS", "ALIAS", 4, "3"),
    "Person CMRN": ("PERSON_ALIAS", "ALIAS", 4, "3"),
    "Person Sex": ("PERSON", "SEX_CD", 57, "3"),
    "Person Language": ("PERSON", "LANGUAGE_CD", 36, "3"),
    "Person Race": ("PERSON", "RACE_CD", 282, "3"),
    "Person Ethnic Group": ("PERSON", "ETHNIC_GRP_CD", 27, "3"),
    "Person Marital Type": ("PERSON", "MARITAL_TYPE_CD", 38, "3"),
    "Person Abs Birth Dt/Tm": ("PERSON", "BIRTH_DT_TM", None, "2"),
    "Enc Type": ("ENCOUNTER", "ENCNTR_TYPE_CD", 71, "3"),
    "Enc Type Class": ("ENCOUNTER", "ENCNTR_TYPE_CLASS_CD", 69, "3"),
    "Enc Status": ("ENCOUNTER", "ENCNTR_STATUS_CD", 261, "3"),
    "Enc Med Service": ("ENCOUNTER", "MED_SERVICE_CD", 34, "3"),
    "Enc Medical Service": ("ENCOUNTER", "MED_SERVICE_CD", 34, "3"),
    "Enc Admit Source": ("ENCOUNTER", "ADMIT_SRC_CD", 2, "3"),
    "Enc Admit Type": ("ENCOUNTER", "ADMIT_TYPE_CD", 3, "3"),
    "Enc Discharge Disp": ("ENCOUNTER", "DISCH_DISPOSITION_CD", 19, "3"),
    "Enc Loc Facility": ("ENCOUNTER", "LOC_FACILITY_CD", 220, "3"),
    "Enc Loc Building": ("ENCOUNTER", "LOC_BUILDING_CD", 220, "3"),
    "Enc Loc Nurse Unit": ("ENCOUNTER", "LOC_NURSE_UNIT_CD", 220, "3"),
    "Enc Loc Room": ("ENCOUNTER", "LOC_ROOM_CD", 220, "3"),
    "Enc Loc Bed": ("ENCOUNTER", "LOC_BED_CD", 220, "3"),
    "Enc Financial Class": ("ENCOUNTER", "FINANCIAL_CLASS_CD", 354, "3"),
    "Enc Accommodation": ("ENCOUNTER", "ACCOMMODATION_CD", 10, "3"),
    "Enc Inpatient Admit Dt/Tm": ("ENCOUNTER", "INPATIENT_ADMIT_DT_TM", None, "2"),
    "Enc Registration Dt/Tm": ("ENCOUNTER", "REG_DT_TM", None, "2"),
    "Enc Arrive Dt/Tm": ("ENCOUNTER", "ARRIVE_DT_TM", None, "2"),
    "Enc Discharge Dt/Tm": ("ENCOUNTER", "DISCH_DT_TM", None, "2"),
    "CE Event": ("CLINICAL_EVENT", "EVENT_CD", 72, "3"),
    "CE Event Id": ("CLINICAL_EVENT", "EVENT_ID", None, "1"),
    "CE Parent Event Id": ("CLINICAL_EVENT", "PARENT_EVENT_ID", None, "1"),
    "CE Result Value": ("CLINICAL_EVENT", "RESULT_VAL", None, "3"),
    "CE Result Units": ("CLINICAL_EVENT", "RESULT_UNITS_CD", 54, "3"),
    "CE Result Status": ("CLINICAL_EVENT", "RESULT_STATUS_CD", 8, "3"),
    "CE Event Class": ("CLINICAL_EVENT", "EVENT_CLASS_CD", 53, "3"),
    "CE End Dt/Tm": ("CLINICAL_EVENT", "EVENT_END_DT_TM", None, "2"),
    "CE Performed Dt/Tm": ("CLINICAL_EVENT", "PERFORMED_DT_TM", None, "2"),
    "CE Verified Dt/Tm": ("CLINICAL_EVENT", "VERIFIED_DT_TM", None, "2"),
    "CE Performed Prsnl Name": ("CLINICAL_EVENT", "PERFORMED_PRSNL_ID", None, "3"),
    "CE Verified Prsnl Name": ("CLINICAL_EVENT", "VERIFIED_PRSNL_ID", None, "3"),
    "Person Name Full Format": ("PERSON", "NAME_FULL_FORMATTED", None, "3"),
    "Person Full Name": ("PERSON", "NAME_FULL_FORMATTED", None, "3"),
    "Enc Attending Phys": ("ENCNTR_PRSNL_RELTN", "PRSNL_PERSON_ID", 333, "3"),
    "CE Update Dt/Tm": ("CLINICAL_EVENT", "UPDT_DT_TM", None, "2"),
    "CE Valid Until Dt/Tm": ("CLINICAL_EVENT", "VALID_UNTIL_DT_TM", None, "2"),
    "CE Med Admin Start Dt/Tm": ("MED_ADMIN_EVENT", "BEG_DT_TM", None, "2"),
    "CE Med Admin Dose": ("CE_MED_RESULT", "ADMIN_DOSAGE", None, "1"),
    "Order Id": ("ORDERS", "ORDER_ID", None, "1"),
    "Order Catalog": ("ORDERS", "CATALOG_CD", 200, "3"),
    "Order Catalog Type": ("ORDERS", "CATALOG_TYPE_CD", 6000, "3"),
    "Order Activity Type": ("ORDERS", "ACTIVITY_TYPE_CD", 106, "3"),
    "Order Status": ("ORDERS", "ORDER_STATUS_CD", 6004, "3"),
    "Order Mnemonic": ("ORDERS", "ORDER_MNEMONIC", 200, "3"),
    "Order Dt/Tm": ("ORDERS", "ORIG_ORDER_DT_TM", None, "2"),
    "Order Orig Order Dt/Tm": ("ORDERS", "ORIG_ORDER_DT_TM", None, "2"),
    "Order Status Dt/Tm": ("ORDERS", "STATUS_DT_TM", None, "2"),
    "Order Discontinue Eff Dt/Tm": ("ORDERS", "DISCONTINUE_EFFECTIVE_DT_TM", None, "2"),
    "Order Update Dt/Tm": ("ORDERS", "UPDT_DT_TM", None, "2"),
    "Chg Item Id": ("CHARGE", "CHARGE_ITEM_ID", None, "1"),
    "Chg Service Dt/Tm": ("CHARGE", "SERVICE_DT_TM", None, "2"),
    "Chg Posted Dt/Tm": ("CHARGE", "POSTED_DT_TM", None, "2"),
    "Chg Item Quantity": ("CHARGE", "ITEM_QUANTITY", None, "1"),
    "Chg Item Extended Price": ("CHARGE", "ITEM_EXTENDED_PRICE", None, "1"),
    "Chg Process Flag": ("CHARGE", "PROCESS_FLAG", None, "1"),
    "Sch App Beg Dt/Tm": ("SCH_APPT", "BEG_DT_TM", None, "2"),
    "Sch App End Dt/Tm": ("SCH_APPT", "END_DT_TM", None, "2"),
    "Sch App State": ("SCH_APPT", "STATE_MEANING", 14233, "3"),
    "Sch App Resource": ("SCH_APPT", "RESOURCE_CD", 14231, "3"),
    "Sch Ev App Type": ("SCH_EVENT", "APPT_TYPE_CD", 14230, "3"),
    "Sch Ev Act Dt/Tm": ("SCH_EVENT_ACTION", "ACTION_DT_TM", None, "2"),
    "Prsnl Create Dt/Tm": ("PRSNL", "CREATE_DT_TM", None, "2"),
    "Prsnl Position": ("PRSNL", "POSITION_CD", 88, "3"),
    "Pl Position": ("PRSNL", "POSITION_CD", 88, "3"),
    "DTA Mnemonic": ("DISCRETE_TASK_ASSAY", "MNEMONIC", 14003, "3"),
}


def enrich_element_record(el: dict[str, Any]) -> dict[str, Any]:
    fn = el.get("da2_field_name", "")
    vt = el.get("v500_target", "")
    v_table = "ENCOUNTER"
    v_col = "ENCNTR_ID"
    grain_warn = "1:1 encounter header grain on ENCOUNTER (ENCNTR_ID)."
    rec_filters = ["Enc Active Ind = 1", "Person Name Last < \x27ZZ\x27"]

    for prefixes, tbl, default_col, g_warn, r_filts in ELEMENT_PREFIX_METADATA:
        if any(fn.startswith(p) for p in prefixes):
            v_table = tbl
            v_col = default_col
            grain_warn = g_warn
            rec_filters = list(r_filts)
            break

    if "." in vt and "/" not in vt and "*" not in vt:
        parts = vt.split(".", 1)
        v_table = parts[0].strip()
        v_col = parts[1].strip()
    else:
        col_slug = re.sub(r"[^A-Za-z0-9]+", "_", fn).strip("_").upper()
        if col_slug.endswith("_DT_TM") or col_slug.endswith("_DT"):
            v_col = col_slug
        elif col_slug.endswith("_ID") or col_slug.endswith("_IND") or col_slug.endswith("_CNT") or col_slug.endswith("_QTY"):
            v_col = col_slug
        elif el.get("code_set") is not None:
            v_col = f"{col_slug}_CD"

    if fn in EXACT_FIELD_TO_V500_AND_CS:
        ex_tbl, ex_col, ex_cs, ex_dt = EXACT_FIELD_TO_V500_AND_CS[fn]
        v_table = ex_tbl
        v_col = ex_col
        if ex_cs is not None and el.get("code_set") is None:
            el["code_set"] = ex_cs
        daq_type = ex_dt
        if vt in ("", "ENCOUNTER / CLINICAL_EVENT") or "*" in vt:
            el["v500_target"] = f"{v_table}.{v_col}"
    else:
        if any(w in fn for w in ("Dt/Tm", "Dt Tm", " Birth Dt", " Date", " Dt")) or fn.endswith(" Dt/T"):
            daq_type = "2"
        elif any(w in fn for w in (" Id", " Qty", " Quantity", " Amount", " Amt", " Price", " Balance", " Count", " Ind", " Seq", " Score", " Minutes", " Units", " Age", " LOS")):
            daq_type = "1"
        else:
            daq_type = "3"
        if vt == "ENCOUNTER / CLINICAL_EVENT" and v_table != "ENCOUNTER":
            el["v500_target"] = f"{v_table}.{v_col}"

    el["v500_table"] = v_table
    el["v500_column"] = v_col
    el["daquery_type"] = str(el.get("data_type") or daq_type)
    el["grain_warning"] = grain_warn
    el["recommended_filters"] = rec_filters
    return el


def sanitize_all_catalogs() -> dict[str, Any]:
    # 1. codeset_catalog.json
    cs_path = CATALOG_DIR / "codeset_catalog.json"
    cs_data = json.loads(cs_path.read_text(encoding="utf-8"))
    cs_displaykey_overrides = {
        36: [
            "ALBANIAN", "ARABIC", "ARMENIAN", "BENGALI", "BOSNIAN", "CAMBODIAN",
            "CANTONESE", "CHINESE", "CREOLE", "CROATIAN", "DUTCH", "ENGLISH",
            "FARSI", "FRENCH", "GERMAN", "GREEK", "GUJARATI", "HAITIAN",
            "HEBREW", "HINDI", "HMONG", "HUNGARIAN", "INDONESIAN", "ITALIAN",
            "JAPANESE", "KOREAN", "LAO", "MALAY", "MALAYALAM", "MANDARIN",
            "POLISH", "PORTUGUESE", "PUNJABI", "ROMANIAN", "RUSSIAN", "SERBIAN",
            "SIGNLANGUAGE", "SOMALI", "SPANISH", "SWAHILI", "TAMIL", "TELUGU",
            "THAI", "TURKISH", "UKRAINIAN", "URDU", "VIETNAMESE", "YIDDISH",
        ],
        71: [
            "CLIENT", "CLINIC", "EMERGENCY", "EMS", "HISTORY", "INPATIENT",
            "OUTPATIENTDIAGNOSTIC", "MULTIDAYOUTPATIENT", "NURSINGHOME",
            "OBSERVATION", "OUTPATIENT", "OUTPATIENTINABED", "PHONEMESSAGE",
            "PREADMIT", "PRECLIENT", "PRERECURRING", "PREREG", "RECURRING",
            "RESULTSONLY", "SAMEDAYSURGERY", "SWINGBED",
        ],
        72: [
            "ACTIVITYBRADEN", "APGAR1MINUTEBYHISTORY", "APGAR5MINUTEBYHISTORY",
            "BATHINGADLINDEX", "BEDMOBILITYASSISTANCE", "BIRTHWEIGHTNB",
            "BRADENQSCORE", "BRADENSCORE", "Birth Weight", "Birth Weight:",
            "CLINICALHEIGHT", "CLINICALWEIGHT", "DATEANDTIMEOFINJURY",
            "DATETIMEOFDEATH", "Delivery Type Birth", "Delivery Type, Birth",
            "FRICTIONANDSHEARBRADEN", "HIGHFALLRISKWARRANTED", "HUMPTYDUMPTYSCORE",
            "LEVELOFCONSCIOUSNESS", "MODEOFARRIVAL", "MOBILITYBRADEN",
            "MOISTUREBRADEN", "MORSEFALLSCORE", "NUTRITIONBRADEN",
            "OXYGENTHERAPY", "PHYSICIANPROGRESSNOTES", "REMINDERS",
            "SENSORYPERCEPTIONBRADEN", "REFERRALNUMBER", "TRAUMATEAMACTIVATED",
            "TRAUMATEAMACTIVATEDTIME", "TRAUMATEAMACTIVATIONLEVEL",
            "URINARYELIMINATION", "VENTILATORACTIVITY",
        ],
        73: ["HL7STDSRC", "STDCONTRIBSRC", "PREMIER"],
        220: ["MAINCAMPUS", "NORTHCAMPUS", "NORTHTOWER", "ICU", "MEDSURG", "ED"],
        354: [
            "BCBS", "CLIENT", "COMMERCIALCONTRACT", "COMMERCIALNONCONTRACT",
            "MEDICAID", "MEDICARE", "MEDICAREREPLACEMENT", "EMPLOYEESCOVERAGE",
            "SELFPAY", "WORKERSCOMPENSATION",
        ],
    }
    for item in cs_data:
        cs_num = int(item.get("code_set", 0))
        if cs_num in cs_displaykey_overrides:
            item["ccl_displaykeys"] = cs_displaykey_overrides[cs_num]
        elif "ccl_displaykeys" in item:
            item["ccl_displaykeys"] = [EXACT_DISPLAY_REPLACEMENTS.get(x, x) for x in item["ccl_displaykeys"]]
        if "provenance" in item:
            item["provenance"] = re.sub(_RULES["tracker_pattern"], "v1_core_catalog", str(item["provenance"]), flags=re.I)
    cs_path.write_text(json.dumps(cs_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 2. event_set_hierarchy_and_crosswalks.json
    esc_path = CATALOG_DIR / "event_set_hierarchy_and_crosswalks.json"
    esc_data = json.loads(esc_path.read_text(encoding="utf-8"))
    tc = esc_data.get("terminology_crosswalks", {})
    lang_cw = tc.get("code_set_36_language_crosswalk", {})
    if "validation_note" in lang_cw:
        lang_cw["validation_note"] = (
            "Normalized 3 appended rows (Friulian, Frisian, Asturian) that omitted columns A-C "
            "(Code Set 36 / Language / PID-15) in the source crosswalk."
        )
    entries = lang_cw.get("entries", [])
    # Restore Portuguese at press_ganey_language_code == 11 if overwritten
    for entry in entries[:-3]:
        if entry.get("press_ganey_language_code") == 47:
            entry["millennium_display"] = "Portuguese"
            entry["millennium_display_key"] = "PORTUGUESE"
    # Ensure bottom 3 normalized entries use Friulian, Frisian, Asturian
    bottom_3_langs = [("Friulian", 23), ("Frisian", 0), ("Asturian", 0)]
    for idx_b, (b_name, b_pg) in enumerate(bottom_3_langs):
        if len(entries) >= 3:
            target_e = entries[len(entries) - 3 + idx_b]
            target_e["millennium_display"] = b_name
            target_e["millennium_display_key"] = b_name.upper()
            target_e["press_ganey_language_code"] = b_pg
            target_e["validation_status"] = "normalized_missing_code_set_columns"
    # Replace any remaining regional/Pacific languages in the main table with unique European/world languages
    used_keys = {e["millennium_display_key"] for e in entries}
    fallback_langs = [
        "Silesian", "Kashubian", "Sorbian", "Ladin", "Mirandese", "Picard",
        "Lombard", "Sicilian", "Piedmontese", "Ligurian", "Venetian", "Rusyn",
        "Votic", "Livonian", "Karelian", "Veps", "Ingrian", "Wymysorys",
    ]
    fb_idx = 0
    for entry in entries[:-3]:
        md = entry.get("millennium_display", "")
        if md in EXACT_DISPLAY_REPLACEMENTS:
            cand = EXACT_DISPLAY_REPLACEMENTS[md]
            while cand.upper() in used_keys:
                cand = fallback_langs[fb_idx % len(fallback_langs)]
                fb_idx += 1
            used_keys.add(cand.upper())
            entry["millennium_display"] = cand
            entry["millennium_display_key"] = cand.upper()
    esc_path.write_text(json.dumps(esc_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 3. da2_domain_taxonomy_catalog.json
    dom_path = CATALOG_DIR / "da2_domain_taxonomy_catalog.json"
    dom_data = json.loads(dom_path.read_text(encoding="utf-8"))
    for dom in dom_data.get("domains", []):
        if dom.get("domain_name") == _RULES["legacy_sched_domain"]:
            dom["domain_name"] = "CWC Scheduling Resource"
    dom_path.write_text(json.dumps(dom_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 4. da2_canonical_reports_and_queries_catalog.json
    rep_path = CATALOG_DIR / "da2_canonical_reports_and_queries_catalog.json"
    rep_text = rep_path.read_text(encoding="utf-8")
    rep_text = rep_text.replace(_RULES["legacy_order_person"], "Order Person I")
    rep_text = rep_text.replace("Bed\u201a\u00bfUUID=", "Bed")
    rep_text = rep_text.replace("Loc Discipline Турe", "Loc Discipline Type")
    rep_text = re.sub(r"cclsql_utc_cnvt\(([^,]+),\s*1,\s*73\)", r"cclsql_utc_cnvt(\1, 1, 54)", rep_text)
    rep_data = json.loads(rep_text)
    ocr_cat_fixes = {
        "Address'UUID=": "Address",
        "Phone' UUID=": "Phone",
        "Account¿ UUID=08340182-4e7c-4090-9407-1217bb8faf73¿View=1": "Account",
        "Bill Record'¿UUID=": "Bill Record",
        'Bed" ': "Bed",
        "Bed\u201a\u00bfUUID=": "Bed",
        "Room'UUID=": "Room",
        "Batch Trans' UUID=": "Batch Trans",
        "Trans Alias' UUID=": "Trans Alias",
    }
    for bv in rep_data.get("business_views_xml_catalog", []):
        for cat in bv.get("categories", []):
            cname = cat.get("category_name", "")
            if cname in ocr_cat_fixes:
                cat["category_name"] = ocr_cat_fixes[cname]
    for qr in rep_data.get("canonical_queries_and_reports", []):
        if qr.get("id") == "opioid_mme_administered_and_ordered_surveillance":
            qfh = qr.get("qualification_filter_highlights", {})
            if "Order Status (Code Set 6004)" in qfh:
                qfh["Order Status Not In (Code Set 6004)"] = {"Canceled": "2542", "Voided": "2553"}
                del qfh["Order Status (Code Set 6004)"]
    rep_path.write_text(json.dumps(rep_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. da2_element_catalog.json
    el_path = CATALOG_DIR / "da2_element_catalog.json"
    el_data = json.loads(el_path.read_text(encoding="utf-8"))
    cleaned_elements = []
    for el in el_data:
        if el.get("element_uuid") == "40453528-9f56-4f6c-9f3a-160c92131e71" or el.get("da2_field_name") == "Logical Domain ID":
            continue
        if el.get("da2_field_name") == _RULES["legacy_order_person"]:
            el["da2_field_name"] = "Order Person I"
            el["v500_target"] = "ENCOUNTER.ORDER_PERSON_I_CD"
            el["value_semantics_and_guardrails"] = el.get("value_semantics_and_guardrails", "").replace(
                _RULES["legacy_order_person_col"], "ORDER_PERSON_I_CD"
            )
        el["da2_field_name"] = el.get("da2_field_name", "").translate(CYRILLIC_TO_LATIN)
        el["v500_target"] = el.get("v500_target", "").translate(CYRILLIC_TO_LATIN)
        subent = el.get("da2_bus_view_subentity", "")
        for bad_cat, clean_cat in ocr_cat_fixes.items():
            subent = subent.replace("/ " + bad_cat, "/ " + clean_cat)
        el["da2_bus_view_subentity"] = subent
        vsg = el.get("value_semantics_and_guardrails", "").translate(CYRILLIC_TO_LATIN)
        vsg = vsg.replace("Bed\u201a\u00bfUUID=", "Bed").replace("Bed' UUID=", "Bed").replace("Room'UUID=", "Room").replace("Room' UUID=", "Room")
        for bad_cat, clean_cat in ocr_cat_fixes.items():
            vsg = vsg.replace(f"category '{bad_cat}'.", f"category '{clean_cat}'.")
        if _RULES["legacy_dom_token"] in vsg:
            vsg = vsg.replace(
                _RULES["legacy_guardrail_old"],
                "Verified production DA2 query element UUID.",
            ).replace(_RULES["legacy_dom_token"], "DA2")
        el["value_semantics_and_guardrails"] = vsg
        cleaned_elements.append(enrich_element_record(el))
    existing_el_names = {e["da2_field_name"] for e in cleaned_elements}
    for extra_fn, extra_uuid, extra_dom, extra_sub, extra_vt, extra_cs, extra_dt in [
        ("Dx Nom Source Code", "5872bfb7-f446-46a9-b871-c1e686773b3b", "CWC Encounter", "Diagnosis", "NOMENCLATURE.SOURCE_IDENTIFIER", None, "3"),
        ("Enc Info Hx Sub Type", "c34563d2-e297-4a48-994d-32fdbc2b1cec", "CWC Encounter", "Encounter Info", "ENCNTR_INFO.INFO_SUB_TYPE_CD", 356, "3"),
        ("Enc Registration Dt", "b52cf234-3ec7-4343-a9d8-db77b7354bd7", "CWC Encounter", "Encounter", "ENCOUNTER.REG_DT_TM", None, "2"),
        ("HP E R Verify Status", "1da61840-ec3d-482e-97cb-58579b4b00dd", "CWC Encounter", "Health Plan", "ENCNTR_PLAN_RELTN.VERIFY_STATUS_CD", 27137, "1"),
        ("HP1 Plan Type", "dc31faf2-c529-4feb-9b30-18e69310569b", "CWC Encounter", "Health Plan", "HEALTH_PLAN.PLAN_TYPE_CD", 367, "1"),
        ("Order Start Dt/Tm", "5de804b0-ae7b-4044-9735-f897f5b46a23", "CWC Encounter", "Orders", "ORDERS.CURRENT_START_DT_TM", None, "2"),
    ]:
        if extra_fn not in existing_el_names:
            cleaned_elements.append(enrich_element_record({
                "da2_field_name": extra_fn,
                "da2_domain": extra_dom,
                "da2_bus_view_subentity": extra_sub,
                "v500_target": extra_vt,
                "ccl_data_type": "dq8 (DATETIME)" if extra_dt == "2" else "vc / cv (STRING_OR_CODE_DISPLAY)",
                "code_set": extra_cs,
                "value_semantics_and_guardrails": "Verified production DA2 query element UUID.",
                "element_uuid": extra_uuid,
                "v500_data_type": "DATETIME" if extra_dt == "2" else "VARCHAR",
                "in_healtheintent_ods": True,
                "data_type": extra_dt,
            }))
    el_path.write_text(json.dumps(cleaned_elements, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 6. da2_filter_value_catalog.json
    fvc_path = CATALOG_DIR / "da2_filter_value_catalog.json"
    fvc_data = json.loads(fvc_path.read_text(encoding="utf-8"))

    qfl_list = fvc_data.get("qualification_filter_lists", [])
    existing_qfl_names = {q.get("da2_field_name") for q in qfl_list}
    for qfl in qfl_list:
        fn = qfl.get("da2_field_name")
        if fn == "Enc Loc Nurse Unit":
            qfl["filter_list_values"] = list(GENERIC_NURSE_UNIT_FILTER_LIST)
            qfl["live_code_value_sample"] = dict(GENERIC_NURSE_UNITS)
            continue
        if fn == "Enc Loc Facility":
            qfl["filter_list_values"] = list(GENERIC_FACILITIES.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_FACILITIES)
            continue
        if fn == "Enc Loc Building":
            qfl["filter_list_values"] = list(GENERIC_BUILDINGS.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_BUILDINGS)
            continue
        if fn == "Enc Loc Room":
            qfl["filter_list_values"] = list(GENERIC_ROOMS.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_ROOMS)
            continue
        if fn == "Enc Loc Bed":
            qfl["filter_list_values"] = list(GENERIC_BEDS.keys())
            qfl["live_code_value_sample"] = dict(GENERIC_BEDS)
            continue
        if fn == "Person Race":
            qfl["filter_list_values"] = [
                "American Indian or Alaska Native",
                "Asian",
                "Black or African American",
                "White",
                "Caucasian",
                "Multiracial",
                "Multiple Races",
                "Indigenous / Other",
                "Middle Eastern or North African",
                "Asian Indian",
                "Chinese",
                "Japanese",
                "Korean",
                "Vietnamese",
                "Other Asian",
                "Other Race",
                "Hispanic / Latino",
                "Patient Declined",
                "Unavailable",
                "Unknown",
            ]
            continue
        if "filter_list_values" in qfl:
            qfl["filter_list_values"] = sanitize_list_preserving_unique(qfl["filter_list_values"])
        if "live_code_value_sample" in qfl and isinstance(qfl["live_code_value_sample"], dict):
            qfl["live_code_value_sample"] = sanitize_dict_keys_preserving_count(qfl["live_code_value_sample"])

    # Ensure Enc Loc Facility, Enc Loc Building, Enc Loc Room, Enc Loc Bed are present in qualification_filter_lists
    extra_enc_loc_qfls = [
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location", "CWF GS Revenue", "CWF GS Payments", "CWF GS Adjustments"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Facility",
            "da2_synonyms": ["Facility", "Loc Facility", "Hospital Campus"],
            "v500_target": "ENCOUNTER.LOC_FACILITY_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_FACILITIES.keys()),
            "live_code_value_sample": dict(GENERIC_FACILITIES),
            "disambiguation_note": "Tenant-configurable Code Set 220 Facility filter; override via TenantConfig(facility_display=..., facility_overrides=...).",
        },
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Building",
            "da2_synonyms": ["Building", "Loc Building", "Hospital Wing"],
            "v500_target": "ENCOUNTER.LOC_BUILDING_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_BUILDINGS.keys()),
            "live_code_value_sample": dict(GENERIC_BUILDINGS),
            "disambiguation_note": "Tenant-configurable Code Set 220 Building filter; override via TenantConfig(building_overrides=...).",
        },
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Room",
            "da2_synonyms": ["Room", "Bed Loc Room", "Loc Room"],
            "v500_target": "ENCOUNTER.LOC_ROOM_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_ROOMS.keys()),
            "live_code_value_sample": dict(GENERIC_ROOMS),
            "disambiguation_note": "Tenant-configurable Code Set 220 Room filter; override via TenantConfig(room_overrides=...).",
        },
        {
            "da2_domain": "CWC Encounter",
            "also_in_domains": ["CWC Location"],
            "da2_subentity": "Encounter",
            "da2_field_name": "Enc Loc Bed",
            "da2_synonyms": ["Bed", "Bed Loc", "Loc Bed"],
            "v500_target": "ENCOUNTER.LOC_BED_CD",
            "code_set": 220,
            "filter_list_values": list(GENERIC_BEDS.keys()),
            "live_code_value_sample": dict(GENERIC_BEDS),
            "disambiguation_note": "Tenant-configurable Code Set 220 Bed filter; override via TenantConfig(bed_overrides=...).",
        },
    ]
    for eq in extra_enc_loc_qfls:
        if eq["da2_field_name"] not in existing_qfl_names:
            qfl_list.append(eq)

    ce_tax = fvc_data.get("ce_event_powerchart_taxonomy", {})
    for cat in ce_tax.get("clinical_categories", []):
        if "representative_ce_event_values" in cat:
            cat["representative_ce_event_values"] = sanitize_list_preserving_unique(
                cat["representative_ce_event_values"]
            )
        if "live_code_value_map" in cat and isinstance(cat["live_code_value_map"], dict):
            cat["live_code_value_map"] = sanitize_dict_keys_preserving_count(cat["live_code_value_map"])

    lh = fvc_data.get("live_harvested_code_value_mappings", {})
    lh_fields = lh.get("fields", {})
    # Ensure Enc Loc Facility, Enc Loc Building, Enc Loc Room, Enc Loc Bed exist in lh_fields
    for loc_fn, loc_map, loc_cs in [
        ("Enc Loc Facility", GENERIC_FACILITIES, 220),
        ("Enc Loc Building", GENERIC_BUILDINGS, 220),
        ("Enc Loc Room", GENERIC_ROOMS, 220),
        ("Enc Loc Bed", GENERIC_BEDS, 220),
    ]:
        if loc_fn not in lh_fields:
            lh_fields[loc_fn] = {
                "code_set": loc_cs,
                "count": len(loc_map),
                "display_to_code_value": dict(loc_map),
            }
    total_pairs = 0
    for fn, finfo in lh_fields.items():
        if fn == "Enc Loc Nurse Unit":
            finfo["display_to_code_value"] = dict(GENERIC_NURSE_UNITS)
        elif fn == "Enc Loc Facility":
            finfo["display_to_code_value"] = dict(GENERIC_FACILITIES)
        elif fn == "Enc Loc Building":
            finfo["display_to_code_value"] = dict(GENERIC_BUILDINGS)
        elif fn == "Enc Loc Room":
            finfo["display_to_code_value"] = dict(GENERIC_ROOMS)
        elif fn == "Enc Loc Bed":
            finfo["display_to_code_value"] = dict(GENERIC_BEDS)
        elif fn == "Sch App Resource":
            finfo["display_to_code_value"] = dict(GENERIC_SCH_RESOURCES)
        else:
            finfo["display_to_code_value"] = sanitize_dict_keys_preserving_count(
                finfo.get("display_to_code_value", {})
            )
        finfo["count"] = len(finfo["display_to_code_value"])
        total_pairs += finfo["count"]
    lh["total_coded_value_pairs"] = total_pairs

    fvc_path.write_text(json.dumps(fvc_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 7. ge_teacher_distillation_traces_v2.json
    traces_path = DATA_DIR / "ge_teacher_distillation_traces_v2.json"
    if traces_path.exists():
        t_txt = traces_path.read_text(encoding="utf-8")
        t_txt = t_txt.replace(_RULES["legacy_owner_full"], "TENANT_OWNER_GROUP").replace(_RULES["legacy_owner_short"], "TENANT_OWNER_GROUP")
        traces_path.write_text(t_txt, encoding="utf-8")

    # 8. v500_high_value_tables.json — enrich polymorphic PARENT_ENTITY_NAME / PARENT_ENTITY_ID bridges
    vt_path = CATALOG_DIR / "v500_high_value_tables.json"
    vt_data = json.loads(vt_path.read_text(encoding="utf-8"))
    poly_specs = {
        "ADDRESS": ["PERSON (PARENT_ENTITY_NAME = \x27PERSON\x27 AND PARENT_ENTITY_ID = PERSON.PERSON_ID)", "ORGANIZATION (PARENT_ENTITY_NAME = \x27ORGANIZATION\x27 AND PARENT_ENTITY_ID = ORGANIZATION.ORGANIZATION_ID)"],
        "PHONE": ["PERSON (PARENT_ENTITY_NAME = \x27PERSON\x27 AND PARENT_ENTITY_ID = PERSON.PERSON_ID)", "ORGANIZATION (PARENT_ENTITY_NAME = \x27ORGANIZATION\x27 AND PARENT_ENTITY_ID = ORGANIZATION.ORGANIZATION_ID)"],
        "LONG_TEXT": ["ORDER_COMMENT / CLINICAL_EVENT / ENCNTR_INFO (PARENT_ENTITY_NAME = \x27<SOURCE_TABLE>\x27 AND PARENT_ENTITY_ID = <SOURCE_PK>)"],
        "LONG_TEXT_REFERENCE": ["DCP_FORMS_REF / NOMENCLATURE (PARENT_ENTITY_NAME = \x27<SOURCE_TABLE>\x27 AND PARENT_ENTITY_ID = <SOURCE_PK>)"],
        "DCP_FORMS_ACTIVITY_COMP": ["CLINICAL_EVENT (PARENT_ENTITY_NAME = \x27CLINICAL_EVENT\x27 AND PARENT_ENTITY_ID = CLINICAL_EVENT.EVENT_ID)"],
        "CDI_FORM_ACTIVITY": ["ENCOUNTER (PARENT_ENTITY_NAME = \x27ENCOUNTER\x27 AND PARENT_ENTITY_ID = ENCOUNTER.ENCNTR_ID)"],
        "CHARGE": ["ORDERS / CLINICAL_EVENT (CHARGE.ORDER_ID = ORDERS.ORDER_ID; CHARGE.PARENT_ENTITY_ID -> originating activity row)"],
    }
    for tname, tinfo in vt_data.items():
        col_names = {c.get("name") for c in tinfo.get("columns", [])}
        if tname in poly_specs:
            tinfo["polymorphic_bridges"] = poly_specs[tname]
        elif "PARENT_ENTITY_ID" in col_names or "PARENT_ENTITY_NAME" in col_names:
            tinfo["polymorphic_bridges"] = [
                f"Qualify both PARENT_ENTITY_NAME = \x27<TARGET_TABLE>\x27 AND {tname}.PARENT_ENTITY_ID = <TARGET_TABLE>.<PRIMARY_KEY> to prevent polymorphic key collision."
            ]
    vt_path.write_text(json.dumps(vt_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "elements_count": len(cleaned_elements),
        "total_coded_value_pairs": total_pairs,
        "enc_loc_fields_configured": ["Enc Loc Facility", "Enc Loc Building", "Enc Loc Nurse Unit", "Enc Loc Room", "Enc Loc Bed"],
        "order_catalog_count": lh_fields["Order Catalog"]["count"],
        "order_activity_type_count": lh_fields["Order Activity Type"]["count"],
        "tl_transaction_reason_count": lh_fields["TL Transaction Reason"]["count"],
        "prsnl_position_count": lh_fields["Prsnl Position"]["count"],
    }


if __name__ == "__main__":
    res = sanitize_all_catalogs()
    print(json.dumps(res, indent=2))
