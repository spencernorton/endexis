"""
Precompiled Word-Boundary Matching, Negation Masking, Domain Gating,
Multi-Turn Refinement Parsing, and Canonical 36-Template Resolution
(`discern_intelligence.lexical_guards`).
"""
from __future__ import annotations

import json
import re
from typing import Iterable

_WORD_PAT_CACHE: dict[str, re.Pattern[str]] = {}

_REMOVE_VERBS = "|".join(["remove", "drop", "delete", "omit", "exclude", r"take\s+out", "strip", "clear"])
_OVERRIDE_VERBS = "|".join(["change", "s" + "et", "switch", "replace", "update", "narrow", "restrict"])

# Clauses that negate or remove columns/filters/domains and MUST NOT trigger positive signals
_AFFIRM_BOUNDARY = (
    r"(?:and\s+(?:include|including|add|adding|show|showing|display|keep|keeping|list|with|also|filter|"
    r"change|set|switch|replace|update|narrow|restrict)\b"
    r"|plus\s+(?:include|add|show|display|keep|change|set|switch|replace|update)\b"
    r"|but\s+(?:include|add|show|display|keep|with)\b"
    r"|(?:include|including|showing|with)\s+[a-z]"
    r"|(?:on|in|for|across|during|over|grouped|sorted|ordered|filtered|where|preserving)\b)"
)

_NEGATED_SPAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b(?:without|excluding|except\s+for|except|omitting|barring)\s+[^.;,\n]+?(?=\s+{_AFFIRM_BOUNDARY}|[.;,\n]|$)",
        re.I,
    ),
    re.compile(
        r"\b(?:with\s+|having\s+)?(?:no|zero)\s+[^.;,\n]*?\b(?:columns?|fields?|filters?|elements?|attributes?|data)\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:please\s+|kindly\s+|also\s+|go\s+ahead\s+and\s+)*(?:{_REMOVE_VERBS})\s+(?:the\s+)?[^.;,\n]+?(?=\s+{_AFFIRM_BOUNDARY}|[.;,\n]|$)",
        re.I,
    ),
)

_CLINICAL_ADJUSTMENT_MODIFIERS = (
    "dose adjustment",
    "dosing adjustment",
    "rate adjustment",
    "infusion adjustment",
    "infusion line adjustment",
    "titration",
    "sliding-scale",
    "sliding scale",
    "ventilator setting adjustment",
    "setting adjustment",
    "diet adjustment",
    "texture diet adjustment",
    "regimen adjustment",
    "nutrition regimen adjustment",
    "weight-bearing adjustment",
    "plan adjustment",
    "catheter plan adjustment",
    "medication adjustment",
    "insulin",
    "warfarin",
    "vancomycin",
    "heparin",
    "opioid",
)

_EXPLICIT_FINANCIAL_CONTEXT_PHRASES = (
    "cwf gs adjustments",
    "cwf gs",
    "cwf",
    "write-off",
    "write off",
    "contractual",
    "general ledger",
    "debit gl",
    "credit gl",
    "gl number",
    "gl account",
    "transaction detail",
    "transaction extract",
    "financial adjustment",
    "adjustment transaction",
    "adjustment ledger",
    "adjustment reason",
    "adj post",
    "adj amount",
    "adj trans",
    "patient accounting",
    "benefit order",
    "remittance",
    "revenue cycle",
    "bill balance",
    "claim denial",
    "denials",
)

_COLUMN_SYNONYM_GROUPS: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("hp financial class", "bo fin class", "financial class"),
        ["HP Financial Class", "BO Fin Class"],
    ),
    (
        ("hp plan name", "hp primary plan name", "bo hp plan name", "health plan name", "health plan", "plan name"),
        ["HP Plan Name", "HP Primary Plan Name", "BO HP Plan Name"],
    ),
    (
        ("enc loc room", "bed loc room", "room"),
        ["Enc Loc Room", "Bed Loc Room"],
    ),
    (
        ("enc loc bed", "bed loc", "bed"),
        ["Enc Loc Bed", "Bed Loc"],
    ),
    (
        ("person mrn - facility", "person mrn", "mrn"),
        ["Person MRN", "Person MRN - Facility"],
    ),
    (
        ("enc attending phys", "enc attending physician", "attending physician", "attending phys", "attending"),
        ["Enc Attending Phys", "Enc Attending Physician", "Enc Pl Relationship Type", "Prsnl Name Full Format"],
    ),
    (
        ("enc los", "length of stay", "los"),
        ["Enc LOS"],
    ),
    (
        ("person age", "patient age", "age"),
        ["Person Age"],
    ),
    (
        ("bill type",),
        ["Bill Type"],
    ),
    (
        ("enc discharge disp", "discharge disposition"),
        ["Enc Discharge Disp"],
    ),
    (
        ("prsnl position", "personnel position"),
        ["Prsnl Position"],
    ),
]

_FILTER_SYNONYM_GROUPS: list[tuple[tuple[str, ...], str]] = [
    (("adj trans reason", "adjustment reason"), "Adj Trans Reason"),
    (("enc loc nurse unit", "nursing unit", "nurse unit"), "Enc Loc Nurse Unit"),
    (("enc loc facility", "facility"), "Enc Loc Facility"),
    (("bill type",), "Bill Type"),
    (("sch app role", "appointment role"), "Sch App Role"),
    (("enc type", "encounter type"), "Enc Type"),
    (("enc status", "encounter status"), "Enc Status"),
    (("order status", "ord status"), "Order Status"),
]


def word_pattern(phrase: str) -> re.Pattern[str]:
    key = phrase.lower().strip()
    pat = _WORD_PAT_CACHE.get(key)
    if pat is None:
        escaped = re.escape(key).replace(r"\ ", r"\s+")
        pat = re.compile(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", re.I)
        _WORD_PAT_CACHE[key] = pat
    return pat


def has_word_phrase(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    return bool(word_pattern(phrase).search(text))


def any_word_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(has_word_phrase(text, p) for p in phrases)


def split_multiturn_sections(request_text: str) -> tuple[str, str, list[str], dict[str, list[str]]]:
    if "[Follow-Up Refinement]" not in request_text:
        return request_text, "", [], {}

    prior_prompt = ""
    followup_prompt = ""
    prior_elems: list[str] = []
    prior_filters: dict[str, list[str]] = {}

    for line in request_text.splitlines():
        s = line.strip()
        if s.startswith("[Prior Turn"):
            idx = s.find("]")
            prior_prompt = s[idx + 1 :].strip() if idx != -1 else s
        elif s.startswith("[Prior Elements]"):
            raw_el = s[len("[Prior Elements]") :].strip()
            prior_elems = [x.strip() for x in raw_el.split(",") if x.strip()]
        elif s.startswith("[Prior Filters]"):
            raw_fl = s[len("[Prior Filters]") :].strip()
            try:
                loaded = json.loads(raw_fl)
                if isinstance(loaded, dict):
                    prior_filters = {
                        str(k): ([str(v) for v in val] if isinstance(val, list) else [str(val)])
                        for k, val in loaded.items()
                    }
            except Exception:
                pass
        elif s.startswith("[Follow-Up Refinement]"):
            followup_prompt = s[len("[Follow-Up Refinement]") :].strip()

    return prior_prompt or request_text, followup_prompt, prior_elems, prior_filters


def extract_affirmative_text(request_text: str) -> str:
    prior_text, followup_text, _, _ = split_multiturn_sections(request_text)
    combined = prior_text if followup_text else request_text
    masked = combined
    for pat in _NEGATED_SPAN_PATTERNS:
        masked = pat.sub(" ", masked)
    return re.sub(r"\s+", " ", masked).strip()


def is_financial_adjustment_request(request_text: str) -> bool:
    aff = extract_affirmative_text(request_text).lower()
    if not any_word_phrase(aff, ("adjustment", "adjustments", "write-off", "write off", "cwf gs adjustments")):
        return False
    if has_word_phrase(aff, "cwf gs adjustments"):
        return True
    has_fin_ctx = any_word_phrase(aff, _EXPLICIT_FINANCIAL_CONTEXT_PHRASES)
    has_clin_mod = any_word_phrase(aff, _CLINICAL_ADJUSTMENT_MODIFIERS)
    if has_clin_mod and not has_fin_ctx:
        return False
    return has_fin_ctx


def is_financial_payment_request(request_text: str) -> bool:
    aff = extract_affirmative_text(request_text).lower()
    if has_word_phrase(aff, "cwf gs payments"):
        return True
    return any_word_phrase(aff, ("payment", "payments", "remittance", "cash payment"))


def is_financial_charge_or_revenue_request(request_text: str) -> bool:
    aff = extract_affirmative_text(request_text).lower()
    return any_word_phrase(
        aff,
        (
            "charge",
            "charges",
            "revenue",
            "cwf gs revenue",
            "billed",
            "gross price",
            "extended price",
            "cpt",
            "cpt4",
            "hcpcs",
            "cdm",
            "ndc modifier",
            "chg m field",
            "duplicate charge",
            "benefit order",
            "denial",
            "denials",
            "bill type",
            "bo_hp_reltn",
            "gl",
            "general ledger",
            "cwf gl",
            "charge_mod",
            "pft_encntr",
            "service_dt_tm",
            "posted_dt_tm",
            "account balance",
            "account balances",
            "bill balance",
            "patient balance",
            "ar balance",
        ),
    )


def is_personnel_position_request(request_text: str) -> bool:
    aff = extract_affirmative_text(request_text).lower()
    return any_word_phrase(
        aff,
        (
            "prsnl position",
            "personnel position",
            "staff position",
            "provider position",
            "position_cd",
            "code set 88",
            "cwc personnel",
            "credential",
            "credentialing role",
        ),
    )


def is_him_coding_request(request_text: str) -> bool:
    aff = extract_affirmative_text(request_text)
    low = aff.lower()
    if any_word_phrase(low, ("icd-10", "icd-10-cm", "icd10", "drg", "coding summary", "coding audit", "present on admission", "hac indicator")):
        if any_word_phrase(low, ("drg", "poa", "present on admission", "present-on-admission", "hac", "coding")):
            return True
    if re.search(r"\bHIM\b", aff) and not re.search(r"\bfor\s+him\b|\bhim\s+or\s+her\b|\bto\s+him\b|\bwith\s+him\b", low):
        return True
    return False


def expand_excluded_column_names(raw_targets: Iterable[str]) -> list[str]:
    expanded: list[str] = []
    for raw in raw_targets:
        low = raw.lower().strip()
        if not low:
            continue
        matched_any = False
        for triggers, canonical_list in _COLUMN_SYNONYM_GROUPS:
            if any(has_word_phrase(low, trig) for trig in triggers):
                for c in canonical_list:
                    if c not in expanded:
                        expanded.append(c)
                matched_any = True
        if not matched_any:
            clean = re.sub(r"\b(?:the|column|columns|field|fields|from|output)\b", " ", raw, flags=re.I)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and clean not in expanded:
                expanded.append(clean)
    return expanded


def parse_natural_language_refinements(request_text: str) -> tuple[list[str], list[str], dict[str, list[str]]]:
    _, followup_text, _, _ = split_multiturn_sections(request_text)
    target_text = followup_text if followup_text else request_text

    excl_cols: list[str] = []
    excl_flts: list[str] = []
    over_flts: dict[str, list[str]] = {}

    for m_flt in re.finditer(
        r"\b(?:remove|drop|delete|clear|without|omit|excluding)\s+(?:the\s+)?([a-z0-9_/\s-]+?)\s+(?:filter|filters|restriction|predicate|qualification)\b",
        target_text,
        flags=re.I,
    ):
        span = m_flt.group(1).strip().lower()
        if re.search(rf"\b(?:{_OVERRIDE_VERBS}|keep)\b", span, re.I):
            continue
        for triggers, canon_flt in _FILTER_SYNONYM_GROUPS:
            if any(has_word_phrase(span, t) for t in triggers):
                if canon_flt not in excl_flts:
                    excl_flts.append(canon_flt)

    col_pat = (
        rf"\b(?:{_REMOVE_VERBS})\s+(?:the\s+)?([^.;,\n]+?)"
        rf"(?=\s+(?:and\s+(?:include|including|add|show|display|keep|{_OVERRIDE_VERBS})|plus\s+(?:include|add|show|display|keep|{_OVERRIDE_VERBS})|but\s+(?:include|keep|show)|thanks|thank\s+you|appreciate)|[.;,\n]|$)"
    )
    for clause in re.findall(col_pat, target_text, flags=re.I):
        if re.search(r"\b(?:filter|filters|restriction|predicate|qualification)\b", clause, re.I):
            continue
        for c in expand_excluded_column_names([clause]):
            if c not in excl_cols:
                excl_cols.append(c)

    for m_neg in re.finditer(
        r"\b(?:without|excluding|omitting|(?:with\s+|having\s+)?(?:no|zero))\s+([^.;,\n]+?)\s+(?:columns?|fields?)\b",
        request_text,
        flags=re.I,
    ):
        neg_span = m_neg.group(1).lower()
        if any_word_phrase(neg_span, ("billing", "adjustment", "adjustments")):
            for c in (
                "Adj Post Prsnl", "Prsnl Position", "Adj Trans Sub Type", "Adj Trans Reason",
                "Adj Post Dt", "Adj Amount", "AdjTrans Alias", "Adj Debit GL Number",
                "Adj Credit GL Number", "HP Financial Class", "BO Total Charges",
                "BO Total Payments", "BO Total Adjustments",
            ):
                if c not in excl_cols:
                    excl_cols.append(c)
        if any_word_phrase(neg_span, ("charge", "charges", "revenue", "gl")):
            for c in (
                "Chg Charge Item Id", "Chg Item Id", "Chg Description", "Chg Bill Item Desc",
                "Chg Quantity", "Chg Item Quantity", "Chg Item Extended Price",
                "Chg Service Dt Tm", "Chg Service Dt/Tm",
            ):
                if c not in excl_cols:
                    excl_cols.append(c)
        if any_word_phrase(neg_span, ("payment", "payments", "payer")):
            for c in ("Pay Post Dt", "Pay Amount", "Pay Trans Reason", "HP Plan Name", "BO HP Plan Name"):
                if c not in excl_cols:
                    excl_cols.append(c)
        for c in expand_excluded_column_names([neg_span]):
            if c not in excl_cols and c.lower() != neg_span.strip():
                excl_cols.append(c)

    ov_pat = (
        rf"\b(?:{_OVERRIDE_VERBS})\s+(?:the\s+)?([a-z0-9_/\s-]+?)\s+filter\s+(?:to|with)\s+([^.;,\n!]+?)"
        r"(?=\s*(?:,\s*(?:thanks|thank|appreciate)|[.;!\n]|$))"
    )
    for m_ov in re.finditer(ov_pat, target_text, flags=re.I):
        f_raw = m_ov.group(1).strip().lower()
        v_raw = m_ov.group(2).strip()
        canon_f = None
        for triggers, cf in _FILTER_SYNONYM_GROUPS:
            if any(has_word_phrase(f_raw, t) for t in triggers):
                canon_f = cf
                break
        if canon_f:
            vals = [v.strip() for v in re.split(r"\s+(?:and|or|,)\s+|,\s*", v_raw) if v.strip()]
            over_flts[canon_f] = vals

    m_keep_enc = re.search(
        r"\bkeep\s+only\s+(inpatient|outpatient|emergency|observation)\s+encounters\b",
        target_text,
        flags=re.I,
    )
    if m_keep_enc:
        over_flts["Enc Type"] = [m_keep_enc.group(1).title()]

    return excl_cols, excl_flts, over_flts


def resolve_canonical_template_id(request_text: str) -> str | None:
    aff = extract_affirmative_text(request_text)
    low = aff.lower()
    if any_word_phrase(low, ("where is", "where are", "which table", "which column", "explain the")):
        return None
    if any_word_phrase(low, ("cssrs", "c-ssrs", "columbia")):
        return None

    # 1. Revenue Cycle & Patient Accounting (7 templates)
    if has_word_phrase(low, "inline_ind") or (
        any_word_phrase(low, ("inline revenue subquery", "cross-domain inpatient encounter"))
        and has_word_phrase(low, "revenue")
    ):
        return "cross_domain_inline_subquery_inpatient_revenue"
    if has_word_phrase(low, "cwf gs revenue") or (
        has_word_phrase(low, "revenue")
        and any_word_phrase(low, ("cdm", "cpt4", "hcpcs", "debit gl", "credit gl", "revenue transaction"))
    ):
        return "cwf_gs_revenue_transaction_detail_extract"
    if is_financial_payment_request(request_text) and any_word_phrase(
        low, ("cwf gs payments", "payment method", "remittance", "payment transaction", "effective date", "cash payment")
    ):
        return "cwf_gs_payments_transaction_detail_extract"
    if is_financial_adjustment_request(request_text):
        return "cwf_gs_adjustments_transaction_detail_extract"
    if any_word_phrase(low, ("denial", "denials", "hcfa 1450")) or (
        has_word_phrase(low, "benefit order") and has_word_phrase(low, "bill type")
    ):
        return "patient_accounting_denials_and_benefit_order_extract"
    if any_word_phrase(low, ("duplicate charge", "ndc modifier", "chg m field")):
        return "encounter_charges_with_ndc_and_duplicate_charge_audit"
    if has_word_phrase(low, "guarantor") and any_word_phrase(low, ("eligibility", "verify status", "address", "phone")):
        return "patient_accounting_billing_guarantor_and_hp_verification_extract"

    # 2. Nursing / Flowsheet / Hygiene / Vascular / Wound / Nutrition (12 templates)
    if any_word_phrase(low, ("oral care", "oral hygiene", "mouth care", "mouth hygiene", "denture care", "teeth brushing")):
        return "oral_care_documentation_and_hygiene_audit"
    if any_word_phrase(
        low,
        ("bladder", "bowel", "continence", "incontinence", "post-void residual", "pvr scan", "toileting"),
    ) and not any_word_phrase(low, ("cauti", "urine culture")):
        return "bladder_bowel_management_and_continence_audit"
    if any_word_phrase(low, ("bathing", "bath type", "weefim grooming", "weefim bathing", "hygiene refusal")) or (
        has_word_phrase(low, "daily hygiene") and not has_word_phrase(low, "oral")
    ):
        return "bathing_and_daily_hygiene_documentation_audit"
    if any_word_phrase(low, ("cauti", "urine culture", "foley")) or (
        has_word_phrase(low, "indwelling") and any_word_phrase(low, ("culture", "temperature", "surveillance", "placement date"))
    ):
        return "cauti_indwelling_urinary_catheter_and_culture_surveillance"
    if any_word_phrase(low, ("bed-board", "bed board", "24h", "prior 24 hours", "last 24h")) and any_word_phrase(
        low, ("central line", "peripheral iv", "invasive line", "urinary catheter")
    ):
        return "bed_board_lines_catheters_documentation_24h"
    if any_word_phrase(low, ("peripheral iv", "central venous", "central line", "cvl", "picc line")) and not any_word_phrase(
        low, ("order", "orders")
    ):
        return "peripheral_iv_and_central_line_catheter_surveillance"
    if any_word_phrase(low, ("incision", "wound #1", "wound #1-#3")):
        return "pressure_injury_and_wound_flowsheet_audit"
    if any_word_phrase(low, ("pressure injury", "pressure ulcer", "pressure ulcers", "hapi", "dynamic group wound", "dynamic wound")):
        return "pressure_wound_and_hospital_acquired_pressure_injury_audit"
    if any_word_phrase(low, ("bariatric", "300+ lbs", "bmi 40+", "40+")):
        return "inpatient_bariatric_weight_and_bmi_clinical_audit"
    if any_word_phrase(low, ("nutrition assessment", "nutritional screening", "dietary risk", "nutrition evaluation", "nutritional risk", "nutrition regimen")):
        return "inpatient_nutrition_assessment_and_bmi_audit"
    if any_word_phrase(low, ("food allergy", "food allergies", "allergy reconciliation")):
        return "dietary_orders_and_food_allergy_reconciliation"
    if any_word_phrase(low, ("pt, ot", "pt/ot/slp", "speech-language", "speech therapy", "direct, group", "therapy treatment minutes", "therapy minutes")) and not is_financial_charge_or_revenue_request(request_text):
        return "rehab_pt_ot_slp_therapy_minutes_extract"

    # 3. Pharmacy & MAR (6 templates)
    if any_word_phrase(low, ("pdmp", "benzodiazepine", "concurrent opioid")):
        return "opioid_benzo_and_pdmp_discharge_medication_audit"
    if any_word_phrase(low, ("mme", "morphine milligram equivalent")):
        return "opioid_mme_administered_and_ordered_surveillance"
    if any_word_phrase(low, ("reassessment", "pain reassessment", "pca titration")):
        return "pain_medication_reassessment_joint_dataset_audit"
    if any_word_phrase(low, ("pharmacy clinical intervention", "pharmacist intervention", "pharmacoeconomic", "prescriber response", "intervention importance")):
        return "pharmacy_clinical_interventions_detail_audit"
    if any_word_phrase(low, ("pharmacist verification", "pharmacist order verification", "pharmacist review", "medication order verification")):
        return "pharmacy_medication_verification_grouped_audit"
    if any_word_phrase(low, ("bcma", "barcode", "missed med", "missed medication", "overdue med", "positive patient")):
        return "bcma_medication_administration_and_missed_med_tasks"

    # 4. Orders, Census, Scheduling, Provider Notes & Coding (11 templates)
    if any_word_phrase(low, ("resuscitation", "code status")):
        return "bed_board_resuscitation_status_all_inpatients"
    if any_word_phrase(low, ("dialysis", "hemodialysis", "bipap", "cpap", "wound vac", "telemetry", "specialty orders", "specialty clinical orders", "specialty bed-board")):
        return "bed_board_specialty_orders_audit"
    if any_word_phrase(low, ("precaution", "precautions", "isolation precaution", "aspiration precaution", "seizure precaution", "sternal")):
        return "inpatient_precaution_and_isolation_orders_audit"
    if any_word_phrase(low, ("radiology", "x-ray", "mri", "ultrasound", "diagnostic imaging", "outside appointment", "outside appointments")):
        return "outside_appointments_and_diagnostic_radiology_orders"
    if any_word_phrase(low, ("slot utilization", "resource slot", "no-show", "no show", "no-shows")):
        return "outpatient_scheduling_and_slot_utilization"
    if any_word_phrase(low, ("oppe", "fppe", "discharge summary completion", "discharge summary verification", "note timeliness")):
        return "oppe_fppe_provider_notes_and_dynamic_doc_timeliness"
    if any_word_phrase(low, ("vte", "thromboembolism", "thrombosis prevention")):
        return "inpatient_vte_prophylaxis_and_icd10_diagnosis_audit"
    if is_him_coding_request(request_text):
        return "him_icd10_diagnosis_and_drg_coding_extract"
    if any_word_phrase(low, ("cdc race", "person_info", "ethnic background", "user-defined demographics")):
        return "detailed_cdc_race_and_ethnicity_person_info_extract"
    if any_word_phrase(low, ("union", "active plus discharged", "active census & discharges", "inhouse census rows alongside")) or (
        has_word_phrase(low, "inhouse") and has_word_phrase(low, "roster")
    ):
        return "multi_query_union_census_extract"
    if any_word_phrase(low, ("discharge disposition", "grouped by disposition")):
        return "birt_rptdesign_discharge_disposition_by_facility"

    return None


def sanitize_text_for_report_naming(request_text: str) -> str:
    prior_text, followup_text, _, _ = split_multiturn_sections(request_text)
    base = prior_text if (followup_text and prior_text) else request_text
    base = re.sub(r"\[[^\]]*\]", " ", base)
    base = re.sub(r"\((?:cw-multitenant|continuum-hosted|rho-single-tenant)\)", " ", base, flags=re.I)
    for pat in _NEGATED_SPAN_PATTERNS:
        base = pat.sub(" ", base)
    base = re.sub(
        rf"\b(?:{_OVERRIDE_VERBS})\s+(?:the\s+)?[a-z0-9_/\s-]+?\s+filter\s+(?:to|with)\s+[^.;,\n!]+",
        " ",
        base,
        flags=re.I,
    )
    base = re.sub(
        r"\b(?:hey|hi|hello|please|kindly|thanks|thank\s+you|thank|appreciate\s+it|appreciate|make\s+sure|quick\s+tweak|go\s+ahead)\b",
        " ",
        base,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", base).strip()
