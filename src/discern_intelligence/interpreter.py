"""
Cerner Millennium V500 Data Model & Discern Analytics 2.0 (DA2) Interpreter.

Maps natural-language requests into exact V500 tables, columns, data types,
Code Sets (CODE_SET + CDF_MEANING), HealtheIntent ODS v1 availability,
DA2 DA_ELEMENT fields, and tenancy-specific execution rules.
"""
from __future__ import annotations

import re
from typing import Any

from .forms_mapper import FormsAndOutputResolver
from .knowledge import (
    load_codeset_catalog,
    load_da2_elements,
    load_da2_view_alias_catalog,
    load_event_set_and_crosswalks,
    load_tenancy_taxonomy,
    load_v500_tables,
)
from .lexical_guards import (
    extract_affirmative_text,
    has_word_phrase,
    is_financial_adjustment_request,
    is_financial_charge_or_revenue_request,
    is_financial_payment_request,
    is_personnel_position_request,
    resolve_canonical_template_id,
)
from .schemas import ColumnMatch, InterpretationResult, RefinementSpec

# High-signal semantic concept rules mapping clinical/financial concepts to exact V500 + DA2 targets
CONCEPT_SIGNALS: list[dict[str, Any]] = [
    {
        "phrases": ["mrn", "medical record number", "patient id", "person alias"],
        "v500": [("PERSON_ALIAS", "ALIAS"), ("PERSON", "PERSON_ID")],
        "da2": ["Person MRN", "Person Id"],
        "code_sets": [4],
    },
    {
        "phrases": ["fin", "financial number", "encounter number", "visit number", "account number"],
        "v500": [("ENCNTR_ALIAS", "ALIAS"), ("ENCOUNTER", "ENCNTR_ID")],
        "da2": ["Enc Encounter Number", "Enc Id", "Account Number"],
        "code_sets": [319],
    },
    {
        "phrases": ["patient name", "full name", "demographics", "date of birth", "dob", "birth", "age", "gender", "sex", "race"],
        "v500": [("PERSON", "NAME_FULL_FORMATTED"), ("PERSON", "BIRTH_DT_TM"), ("PERSON", "SEX_CD"), ("PERSON", "RACE_CD")],
        "da2": ["Person Full Name", "Person Birth Dt Tm", "Person Gender", "Person Race"],
        "code_sets": [57],
    },
    {
        "phrases": ["census", "admit", "admitted", "admission", "admissions", "inpatient", "inpatients", "disch", "discharge", "discharged", "discharges", "length of stay", "los", "active encounter", "active encounters"],
        "v500": [
            ("ENCOUNTER", "ENCNTR_ID"),
            ("ENCOUNTER", "REG_DT_TM"),
            ("ENCOUNTER", "INPATIENT_ADMIT_DT_TM"),
            ("ENCOUNTER", "DISCH_DT_TM"),
            ("ENCOUNTER", "ENCNTR_TYPE_CD"),
            ("ENCOUNTER", "ENCNTR_STATUS_CD"),
            ("ENCOUNTER", "DISCH_DISPOSITION_CD"),
            ("ENCOUNTER", "ACTIVE_IND"),
        ],
        "da2": ["Enc Id", "Enc Registration Dt/Tm", "Enc Inpatient Admit Dt/Tm", "Enc Discharge Dt/Tm", "Enc Type", "Enc Status", "Enc Discharge Disp", "Enc Active Ind"],
        "code_sets": [71, 69, 261, 19],
    },
    {
        "phrases": ["facility", "facilities", "nurse unit", "nursing unit", "unit", "units", "room", "rooms", "bed", "beds", "location", "locations", "clinic", "clinics"],
        "v500": [
            ("ENCOUNTER", "LOC_FACILITY_CD"),
            ("ENCOUNTER", "LOC_NURSE_UNIT_CD"),
            ("ENCOUNTER", "LOC_ROOM_CD"),
            ("ENCOUNTER", "LOC_BED_CD"),
            ("ENCNTR_LOC_HIST", "LOC_NURSE_UNIT_CD"),
        ],
        "da2": ["Enc Loc Facility", "Enc Loc Nurse Unit", "Enc Loc Room", "Enc Loc Bed"],
        "code_sets": [220],
    },
    {
        "phrases": ["attending", "admitting", "physician", "physicians", "provider", "providers", "doctor", "doctors", "prsnl", "clinician", "clinicians", "therapist", "therapists"],
        "v500": [
            ("ENCNTR_PRSNL_RELTN", "PRSNL_PERSON_ID"),
            ("ENCNTR_PRSNL_RELTN", "ENCNTR_PRSNL_R_CD"),
            ("PRSNL", "NAME_FULL_FORMATTED"),
        ],
        "da2": ["Enc Attending Phys", "Enc Pl Relationship Type", "Prsnl Name Full Format"],
        "code_sets": [333],
    },
    {
        "gate": "personnel_position",
        "phrases": ["prsnl position", "personnel position", "staff position", "provider position", "position_cd", "code set 88", "cwc personnel", "credentialing role"],
        "v500": [("PRSNL", "POSITION_CD")],
        "da2": ["Prsnl Position"],
        "code_sets": [88],
    },
    {
        "phrases": [
            "clinical event", "clinical events", "result", "results", "vital", "vitals", "vital signs",
            "assessment", "assessments", "powerform", "powerforms", "flowsheet", "flowsheets",
            "iview", "fim", "care tool", "irf-pai", "therapy minutes", "treatment minutes", "dta", "observation",
            "cssrs", "c-ssrs", "columbia", "suicide", "suicidal", "phq-9", "phq9", "morse", "braden",
            "responses", "form completed", "completed form",
            "oral care", "oral hygiene", "bladder/bowel", "bowel/bladder", "bladder scan", "bladder management",
            "bowel management", "bowel movement", "bowel elimination", "toileting", "incontinence", "continence",
            "bathing", "bath type", "daily hygiene", "nutrition assessment", "nutritional screening",
            "bmi", "body mass index", "central line", "central iv", "peripheral iv",
            "pharmacy intervention", "pharmacy interventions", "clinical intervention", "clinical interventions",
        ],
        "v500": [
            ("CLINICAL_EVENT", "EVENT_ID"),
            ("CLINICAL_EVENT", "EVENT_CD"),
            ("CLINICAL_EVENT", "RESULT_VAL"),
            ("CLINICAL_EVENT", "RESULT_UNITS_CD"),
            ("CLINICAL_EVENT", "RESULT_STATUS_CD"),
            ("CLINICAL_EVENT", "EVENT_END_DT_TM"),
            ("CLINICAL_EVENT", "VALID_UNTIL_DT_TM"),
            ("CLINICAL_EVENT", "PERFORMED_PRSNL_ID"),
            ("CLINICAL_EVENT", "EVENT_CLASS_CD"),
            ("CLINICAL_EVENT", "CLINICAL_EVENT_ID"),
        ],
        "da2": [
            "CE Event Id", "CE Event", "CE Result Value", "CE Result Units",
            "CE Result Status", "CE Event End Dt Tm", "CE Valid Until Dt Tm", "CE Performed Prsnl Name",
            "CE Event Class", "CE Clinical Event Id",
        ],
        "code_sets": [8, 53, 54, 72],
    },
    {
        "phrases": ["powerform", "dcp_forms", "section", "coded result", "form status"],
        "v500": [
            ("DCP_FORMS_REF", "DESCRIPTION"),
            ("DCP_FORMS_ACTIVITY", "DCP_FORMS_ACTIVITY_ID"),
            ("DCP_FORMS_ACTIVITY", "FORM_DT_TM"),
            ("DCP_FORMS_ACTIVITY", "FORM_STATUS_CD"),
            ("DCP_FORMS_ACTIVITY_COMP", "PARENT_ENTITY_ID"),
            ("CE_CODED_RESULT", "NOMENCLATURE_ID"),
        ],
        "da2": ["CE Event", "CE Result Value", "CE Result Status", "CE Valid Until Dt Tm"],
        "code_sets": [8, 72],
    },
    {
        "phrases": ["intake", "output", "i&o", "fluid balance", "urine", "io_volume"],
        "v500": [
            ("CE_INTAKE_OUTPUT_RESULT", "IO_VOLUME"),
            ("CE_INTAKE_OUTPUT_RESULT", "IO_TYPE_FLAG"),
            ("CE_INTAKE_OUTPUT_RESULT", "REFERENCE_EVENT_CD"),
            ("CE_INTAKE_OUTPUT_RESULT", "IO_END_DT_TM"),
        ],
        "da2": ["CE Event", "CE Result Value", "CE Result Units", "CE Event End Dt Tm", "CE Valid Until Dt Tm"],
        "code_sets": [72, 54],
    },
    {
        "phrases": [
            "note", "powernote", "dynamic documentation", "discharge summary", "blob", "narrative", "scd_story",
            "consult note", "initial consult", "progress note", "physician note", "provider note", "history and physical",
            "oppe", "fppe",
        ],
        "v500": [
            ("CLINICAL_EVENT", "EVENT_ID"),
            ("CLINICAL_EVENT", "EVENT_CD"),
            ("CLINICAL_EVENT", "EVENT_TITLE_TEXT"),
            ("CLINICAL_EVENT", "PERFORMED_PRSNL_ID"),
            ("CLINICAL_EVENT", "PERFORMED_DT_TM"),
            ("CLINICAL_EVENT", "VERIFIED_PRSNL_ID"),
            ("CLINICAL_EVENT", "VERIFIED_DT_TM"),
            ("CLINICAL_EVENT", "RESULT_STATUS_CD"),
            ("CLINICAL_EVENT", "VALID_UNTIL_DT_TM"),
            ("SCD_STORY", "SCD_STORY_ID"),
            ("SCD_STORY", "TITLE"),
            ("CE_BLOB_RESULT", "FORMAT_CD"),
            ("CE_BLOB", "BLOB_CONTENTS"),
            ("CE_BLOB", "COMPRESSION_CD"),
        ],
        "da2": [
            "CE Event Id", "CE Event", "CE Event Title", "CE Performed Prsnl Name", "CE Performed Dt/Tm",
            "CE Verified Prsnl Name", "CE Verified Dt/Tm", "CE Result Status", "CE Valid Until Dt Tm",
            "CE Event Id", "CE Event Title", "CE Event", "CE Result Value", "CE Result Status",
        ],
        "code_sets": [8, 53, 72],
    },
    {
        "phrases": [
            "order", "orders", "medication order", "medication orders", "opioid", "opioids",
            "benzo", "benzos", "benzodiazepine", "benzodiazepines", "lab order", "lab orders", "order detail", "order details",
            "oe_format", "indication", "indications", "precaution", "precautions", "isolation",
            "diet order", "diet orders", "diet report", "consult order", "consult orders",
            "1:1 observation", "video monitoring",
        ],
        "v500": [
            ("ORDERS", "ORDER_ID"),
            ("ORDERS", "CATALOG_CD"),
            ("ORDERS", "ORDER_MNEMONIC"),
            ("ORDERS", "ORDER_STATUS_CD"),
            ("ORDERS", "ORIG_ORDER_DT_TM"),
            ("ORDERS", "CLINICAL_DISPLAY_LINE"),
            ("ORDER_DETAIL", "OE_FIELD_MEANING"),
            ("ORDER_DETAIL", "OE_FIELD_DISPLAY_VALUE"),
        ],
        "da2": ["Ord Order Id", "Ord Mnemonic", "Ord Status", "Ord Detail Display", "Ord Orig Order Dt/Tm"],
        "code_sets": [200, 6000, 6003, 6004],
    },
    {
        "phrases": [
            "mar", "bcma", "medication administration", "medication administrations", "med admin",
            "barcode", "missed dose", "missed doses", "late dose", "late doses",
            "late medication", "administered", "dosage",
        ],
        "v500": [
            ("MED_ADMIN_EVENT", "MED_ADMIN_EVENT_ID"),
            ("MED_ADMIN_EVENT", "EVENT_TYPE_CD"),
            ("MED_ADMIN_EVENT", "BEG_DT_TM"),
            ("MED_ADMIN_EVENT", "SCHEDULED_DT_TM"),
            ("MED_ADMIN_EVENT", "POSITIVE_PATIENT_IDENT_IND"),
            ("MED_ADMIN_EVENT", "POSITIVE_MED_IDENT_IND"),
            ("CE_MED_RESULT", "ADMIN_DOSAGE"),
            ("CE_MED_RESULT", "DOSAGE_UNIT_CD"),
            ("CE_MED_RESULT", "ADMIN_ROUTE_CD"),
        ],
        "da2": ["Med Adm Event Id", "Med Adm Dt/Tm", "Med Adm MedScan Ind", "Med Adm PtScan Ind", "CE Result Value", "CE Valid Until Dt Tm"],
        "code_sets": [4002, 54, 8],
    },
    {
        "phrases": ["task", "tasks", "overdue", "task_activity", "not done", "prn", "scheduled task", "scheduled tasks"],
        "v500": [
            ("TASK_ACTIVITY", "TASK_ID"),
            ("TASK_ACTIVITY", "TASK_STATUS_CD"),
            ("TASK_ACTIVITY", "TASK_STATUS_REASON_CD"),
            ("TASK_ACTIVITY", "TASK_CLASS_CD"),
            ("TASK_ACTIVITY", "SCHEDULED_DT_TM"),
        ],
        "da2": ["Task Atv Id", "Task Atv Status", "Task AtvStatus Reason", "Task Atv Class", "Task Atv Scheduled Dt/Tm"],
        "code_sets": [79],
    },
    {
        "phrases": [
            "appointment", "appointments", "appt", "appts", "scheduling",
            "no show", "no-show", "cancellation", "check in", "check-in", "check out", "check-out",
            "sch_appt", "slot", "slots", "outpatient schedule", "clinic schedule", "therapy schedule",
        ],
        "v500": [
            ("SCH_APPT", "SCH_APPT_ID"),
            ("SCH_APPT", "BEG_DT_TM"),
            ("SCH_APPT", "END_DT_TM"),
            ("SCH_APPT", "DURATION"),
            ("SCH_APPT", "SCH_STATE_CD"),
            ("SCH_EVENT", "APPT_TYPE_CD"),
            ("SCH_EVENT_ACTION", "SCH_ACTION_CD"),
            ("SCH_EVENT_ACTION", "SCH_REASON_CD"),
            ("SCH_EVENT_ACTION", "ACTION_DT_TM"),
        ],
        "da2": ["Sch App Id", "Sch App Beg Dt/Tm", "Sch App End Dt/Tm", "Sch App Duration", "Sch App State", "Sch Ev App Type", "Sch Ev Act Action", "Sch Evt Act Reason", "Sch App Beg Dt/Tm"],
        "code_sets": [14232, 14233],
    },
    {
        "gate": "financial",
        "phrases": ["charge", "charges", "cpt", "cdm", "revenue", "billed", "gross price", "extended price", "gl", "general ledger", "benefit order", "bo_hp_reltn", "denial", "denials"],
        "v500": [
            ("CHARGE", "CHARGE_ITEM_ID"),
            ("CHARGE", "CHARGE_DESCRIPTION"),
            ("CHARGE", "SERVICE_DT_TM"),
            ("CHARGE", "ITEM_QUANTITY"),
            ("CHARGE", "ITEM_EXTENDED_PRICE"),
            ("CHARGE_MOD", "FIELD6"),
            ("PFT_ENCNTR", "PFT_ENCNTR_ID"),
            ("PFT_ENCNTR", "BALANCE"),
            ("BO_HP_RELTN", "TOTAL_CHARGE_AMT"),
            ("BO_HP_RELTN", "TOTAL_PAY_AMT"),
            ("BO_HP_RELTN", "TOTAL_ADJ_AMT"),
            ("HEALTH_PLAN", "PLAN_NAME"),
        ],
        "da2": [
            "Chg Charge Item Id", "Chg Description", "Chg Service Dt Tm", "Chg Quantity",
            "BO Total Charges", "BO Total Payments", "BO Total Adjustments", "HP Plan Name", "HP Financial Class",
        ],
        "code_sets": [],
    },
    {
        "phrases": ["health plan", "health plans", "insurance", "payer", "payor", "subscriber", "plan type", "financial class"],
        "v500": [
            ("HEALTH_PLAN", "PLAN_NAME"),
            ("HEALTH_PLAN", "PLAN_TYPE_CD"),
            ("ENCNTR_PLAN_RELTN", "PRIORITY_SEQ"),
            ("ENCNTR_PLAN_RELTN", "VERIFY_STATUS_CD"),
            ("ENCOUNTER", "FINANCIAL_CLASS_CD"),
        ],
        "da2": [
            "HP Plan Name", "HP Plan Type", "HP E R Priority Seq", "HP E R Verify Status", "HP Financial Class",
        ],
        "code_sets": [354, 367, 27137],
    },
    {
        "phrases": ["diagnosis", "diagnoses", "icd", "icd-10", "icd10", "principal diagnosis", "admitting diagnosis", "final diagnosis", "nomenclature", "drg", "poa", "present on admission"],
        "v500": [
            ("DIAGNOSIS", "DIAGNOSIS_ID"),
            ("DIAGNOSIS", "DIAG_TYPE_CD"),
            ("DIAGNOSIS", "NOMENCLATURE_ID"),
            ("NOMENCLATURE", "SOURCE_IDENTIFIER"),
            ("NOMENCLATURE", "SOURCE_STRING"),
        ],
        "da2": ["Diag ICD Code", "Diag Type"],
        "code_sets": [17, 400],
    },
    {
        "phrases": [
            "event set", "event_set", "md_d_event_set_hierarchy", "md_d_event_set_search",
            "v500_event_set_canon", "v500_event_set_explode", "v500_event_set_code",
            "all ocf event sets", "lvl10_code", "bottom_level_event_set_code",
        ],
        "v500": [
            ("V500_EVENT_SET_CODE", "EVENT_SET_CD"),
            ("V500_EVENT_SET_CODE", "EVENT_SET_NAME"),
            ("V500_EVENT_SET_CODE", "EVENT_SET_CD_DISP"),
            ("V500_EVENT_SET_CODE", "EVENT_SET_CD_DEFINITION"),
            ("V500_EVENT_SET_CANON", "PARENT_EVENT_SET_CD"),
            ("V500_EVENT_SET_CANON", "EVENT_SET_CD"),
            ("V500_EVENT_SET_EXPLODE", "EVENT_SET_CD"),
            ("V500_EVENT_SET_EXPLODE", "EVENT_CD"),
        ],
        "da2": ["CE Event", "CE Event Id"],
        "code_sets": [72, 93],
    },
    {
        "phrases": ["language", "pid-15", "press ganey", "interpreter", "primary language", "code set 36"],
        "v500": [
            ("PERSON", "LANGUAGE_CD"),
            ("PERSON_PATIENT", "LANGUAGE_CD"),
        ],
        "da2": ["Person Language", "Person Full Name"],
        "code_sets": [36],
    },
    {
        "phrases": ["controlled substance", "csa_schedule", "dea schedule", "mltm", "therapeutic class", "drugcat", "anti-infective", "coagulation"],
        "v500": [
            ("MLTM_NDC_MAIN_DRUG_CODE", "CSA_SCHEDULE"),
            ("ORDERS", "HNA_ORDER_MNEMONIC"),
            ("ORDERS", "ORDERED_AS_MNEMONIC"),
            ("MED_ADMIN_EVENT", "MED_ADMIN_EVENT_ID"),
            ("CE_MED_RESULT", "ADMIN_DOSAGE"),
        ],
        "da2": ["Mltm Legal Status", "Order Mnemonic", "Order AS Mnemonic", "Med Adm Id", "CE Med Admin Dose"],
        "code_sets": [200, 54, 4001],
    },
    {
        "phrases": ["hcahps", "ub-04", "fl-15", "fl-17", "point of origin", "admit source", "discharge status", "recurring sequence", "recur_seq"],
        "v500": [
            ("ENCOUNTER", "ADMIT_SRC_CD"),
            ("ENCOUNTER", "DISCH_DISPOSITION_CD"),
            ("PFT_ENCNTR", "RECUR_SEQ"),
        ],
        "da2": ["Enc Admit Source", "Enc Discharge Disp", "F Enc Recur Seq"],
        "code_sets": [2, 19],
    },
    {
        "phrases": ["formulary", "item definition", "med product", "med cost", "awp", "acquisition cost", "bill item", "price schedule", "ndc"],
        "v500": [
            ("ITEM_DEFINITION", "ITEM_ID"),
            ("ITEM_DEFINITION", "ITEM_TYPE_CD"),
            ("MED_PRODUCT", "MED_PRODUCT_ID"),
            ("MED_PRODUCT", "FORMULARY_STATUS_CD"),
            ("MED_COST_HX", "COST"),
            ("MED_COST_HX", "COST_TYPE_CD"),
            ("BILL_ITEM", "BILL_ITEM_ID"),
            ("BILL_ITEM", "EXT_DESCRIPTION"),
            ("BILL_ITEM_MODIFIER", "KEY6"),
            ("PRICE_SCHED", "PRICE_SCHED_DESC"),
        ],
        "da2": [
            "ID Item Id", "ID Item Type", "Med Prod Id", "Med Prod Formulary Status",
            "Med C Cost", "Med C Cost Type", "B I Bill Item Id", "B I Ext Description",
            "BIM Key6", "P S Desc",
        ],
        "code_sets": [4512, 4050, 14002],
    },
    {
        "phrases": ["esi log", "esi_log", "contributor system", "hl7 interface", "interface error", "oen", "msh_msg_type"],
        "v500": [
            ("CONTRIBUTOR_SYSTEM", "CONTRIBUTOR_SYSTEM_CD"),
            ("CONTRIBUTOR_SYSTEM", "CONTRIBUTOR_SOURCE_CD"),
            ("ESI_LOG", "ESI_LOG_ID"),
            ("ESI_LOG", "MSH_MSG_TYPE"),
            ("ESI_LOG", "MSH_MSG_TRIGGER"),
            ("ESI_LOG", "ERROR_TEXT"),
        ],
        "da2": [
            "Cont Sys Contributor Sys", "Cont Sys Contributor Source", "ESI Log Id",
            "ESI Log MSH Msg Type", "ESI Log MSH Msg Trigger", "ESI Log Error Txt",
        ],
        "code_sets": [89, 73],
    },
    {
        "phrases": ["credential", "license", "npi", "dea", "personnel group", "provider group", "coding activity", "gl account"],
        "v500": [
            ("CREDENTIAL", "CREDENTIAL_ID"),
            ("CREDENTIAL", "CREDENTIAL_CD"),
            ("CREDENTIAL", "ID_NUMBER"),
            ("CREDENTIAL", "RENEWAL_DT_TM"),
            ("PRSNL_ALIAS", "ALIAS"),
            ("PRSNL_GROUP", "PRSNL_GROUP_NAME"),
            ("CODING", "CODING_STATUS_CD"),
            ("GL_TRANS_LOG", "ACCT_NAME"),
        ],
        "da2": [
            "Cred Id", "Cred Credential", "Cred Id Number", "Cred Renewal Dt/Tm",
            "Pl Alias", "Pl Grp Name", "Cod Act Status", "GL Account Name",
        ],
        "code_sets": [320, 88],
    },
]


class MillenniumInterpreter:
    """Interprets natural-language analytics requests into Millennium V500 & DA2 items."""

    def __init__(self) -> None:
        self._tables = load_v500_tables()
        self._codesets = load_codeset_catalog()
        self._da2_elements = load_da2_elements()
        self._da2_by_name = {e["da2_field_name"]: e for e in self._da2_elements}
        self._tenancy = load_tenancy_taxonomy()
        self._da2_view_catalog = load_da2_view_alias_catalog()
        self._event_set_crosswalks = load_event_set_and_crosswalks()
        self._forms_resolver = FormsAndOutputResolver()

    def infer_tenancy(self, request_text: str, explicit_tenancy: str | None = None) -> tuple[str, str, list[str]]:
        if explicit_tenancy:
            norm = explicit_tenancy.lower().strip()
            if "continuum" in norm:
                return (
                    "continuum",
                    "Explicitly set to Continuum (multi-tenant shared domain for post-acute, rehab, LTC, and behavioral health).",
                    ["DA2 Domain Query Builder (CWC / CWF)", "BIRT 4.4.2 .rptdesign (single or multi-dataset join)"],
                )
            if "community" in norm or "cwc" in norm:
                return (
                    "communityworks",
                    "Explicitly set to CommunityWorks (multi-tenant shared domain for community & critical-access hospitals).",
                    ["DA2 Domain Query Builder (CWC / CWF)", "BIRT 4.4.2 .rptdesign (single or multi-dataset join)"],
                )
            if "standalone" in norm or "rho" in norm or "single" in norm:
                return (
                    "standalone_rho",
                    "Explicitly set to Standalone / Remote Hosted (RHO) single-tenant Millennium domain.",
                    ["Custom CCL (.prg / DVDev / Discern Explorer)", "DA2 Custom/Extended Domains", "BIRT 4.4.2 .rptdesign", "HealtheIntent millennium-ods/v1 SQL"],
                )

        low = request_text.lower()
        if any(w in low for w in ("continuum", "rehab", "irf", "fim", "care tool", "ltc", "skilled nursing", "therapy minutes", "pt/ot", "physical therapy", "occupational therapy", "speech therapy")):
            return (
                "continuum",
                "Inferred Continuum tenancy from post-acute / rehabilitation / therapy workflow vocabulary. Custom CCL upload is restricted on the shared parent domain; queries target CWC/CWF DA2 domains + BIRT.",
                ["DA2 Domain Query Builder (CWC / CWF)", "BIRT 4.4.2 .rptdesign (single or multi-dataset join)"],
            )
        if any(w in low for w in ("communityworks", "critical access", "cah", "cwc", "cwf")):
            return (
                "communityworks",
                "Inferred CommunityWorks tenancy from community-hospital / CWC-CWF vocabulary. Custom CCL upload is restricted on the shared parent domain.",
                ["DA2 Domain Query Builder (CWC / CWF)", "BIRT 4.4.2 .rptdesign (single or multi-dataset join)"],
            )
        if any(w in low for w in ("dvdev", "create program", ".prg", "uar_get_code", "standalone", "rho", "healtheintent", "ods")):
            return (
                "standalone_rho",
                "Inferred Standalone / RHO single-tenant domain from custom CCL / DVDev / ODS vocabulary.",
                ["Custom CCL (.prg / DVDev / Discern Explorer)", "DA2 Custom/Extended Domains", "BIRT 4.4.2 .rptdesign", "HealtheIntent millennium-ods/v1 SQL"],
            )
        return (
            "continuum",
            "Defaulted to multi-tenant governance (Continuum / CommunityWorks compatible) so generated DA2 & BIRT artifacts are safe for shared domains while also compiling equivalent Standalone CCL.",
            ["DA2 Domain Query Builder (CWC / CWF)", "BIRT 4.4.2 .rptdesign (single or multi-dataset join)"],
        )

    def interpret(
        self,
        request_text: str,
        tenancy: str | None = None,
        top_k_columns: int = 28,
        strict_precision: bool = False,
        refinement: RefinementSpec | dict[str, Any] | None = None,
    ) -> InterpretationResult:
        aff_text = extract_affirmative_text(request_text)
        low = aff_text.lower()
        tokens = set(re.findall(r"[a-z0-9_]+", low))
        tenancy_id, tenancy_reason, legal_surfaces = self.infer_tenancy(aff_text, tenancy)
        eff_ref = RefinementSpec.from_request_or_dict(request_text=request_text, raw=refinement)
        matched_tpl_id = resolve_canonical_template_id(request_text)
        has_fin_adj = is_financial_adjustment_request(request_text)
        has_fin_pay = is_financial_payment_request(request_text)
        has_fin_chg = is_financial_charge_or_revenue_request(request_text)
        has_any_fin = has_fin_adj or has_fin_pay or has_fin_chg
        has_prsnl_pos = is_personnel_position_request(request_text)

        # Always include core encounter/person anchors when interpreting clinical/financial requests
        candidate_pairs: dict[tuple[str, str], tuple[float, str | None]] = {
            ("ENCOUNTER", "ENCNTR_ID"): (4.0, "Enc Id"),
            ("ENCNTR_ALIAS", "ALIAS"): (3.8, "Enc Encounter Number"),
            ("PERSON", "PERSON_ID"): (3.5, "Person Id"),
            ("PERSON", "NAME_FULL_FORMATTED"): (3.5, "Person Full Name"),
        }
        triggered_cs: set[int] = {319, 4}

        for sig in CONCEPT_SIGNALS:
            gate = sig.get("gate")
            if gate == "personnel_position" and not has_prsnl_pos:
                continue
            if gate == "financial" and not has_any_fin:
                continue
            matched_phrase = False
            for phr in sig["phrases"]:
                if has_word_phrase(low, phr):
                    matched_phrase = True
                    break
            if matched_phrase:
                da2_list = sig["da2"]
                for idx, (tbl, col) in enumerate(sig["v500"]):
                    da2_name = da2_list[idx] if idx < len(da2_list) else (da2_list[0] if da2_list else None)
                    prev_score, prev_da2 = candidate_pairs.get((tbl, col), (0.0, None))
                    candidate_pairs[(tbl, col)] = (max(prev_score, 8.5 - idx * 0.08), prev_da2 or da2_name)
                for cs_num in sig.get("code_sets", []):
                    triggered_cs.add(cs_num)

        # Scan DA2 XCW_* views, DA2 aliases, and CWC_* EJS aliases (kept separate from raw V500 tables)
        matched_da2_views: list[dict[str, Any]] = []
        for v_entry in self._da2_view_catalog.get("da2_views", []) + self._da2_view_catalog.get("relational_path_variants", []):
            v_name_low = v_entry["da2_view_name"].lower()
            v_entry_low = v_entry["da2_view_entry"].lower()
            v_alias_low = v_entry["da2_alias"].lower()
            cwc_low = "cwc_" + v_name_low[4:]
            if v_entry_low in low or v_name_low in tokens or v_alias_low in tokens or cwc_low in tokens:
                matched_da2_views.append(v_entry)
                raw_t = v_entry["underlying_v500_table"]
                join_c = v_entry["v500_join_column"]
                if raw_t in self._tables:
                    prev_s, prev_d = candidate_pairs.get((raw_t, join_c), (0.0, None))
                    candidate_pairs[(raw_t, join_c)] = (max(prev_s, 9.2), prev_d)
                    for pk_col in v_entry.get("v500_primary_keys", []):
                        prev_pk_s, prev_pk_d = candidate_pairs.get((raw_t, pk_col), (0.0, None))
                        candidate_pairs[(raw_t, pk_col)] = (max(prev_pk_s, 9.0), prev_pk_d)

        # Also scan V500 table & column names / definitions directly for exact technical token hits
        for tname, tinfo in self._tables.items():
            if not has_any_fin and tname in ("CHARGE", "CHARGE_MOD", "PFT_ENCNTR", "BENEFIT_ORDER", "BO_HP_RELTN", "TRANS_LOG", "GL_TRANS_LOG"):
                if f"{tname.lower()}." not in low:
                    continue
            t_low = tname.lower()
            t_hit = has_word_phrase(low, t_low) or t_low in tokens
            for cinfo in tinfo.get("columns", []):
                cname = cinfo["name"]
                c_low = cname.lower()
                score = 0.0
                if f"{t_low}.{c_low}" in low:
                    score += 12.0
                elif c_low in tokens and len(c_low) > 4:
                    score += 6.0
                elif t_hit and any(tok in (cinfo.get("definition") or "").lower() for tok in tokens if len(tok) > 3):
                    score += 4.2
                if score > 0:
                    prev_s, prev_d = candidate_pairs.get((tname, cname), (0.0, None))
                    if score > prev_s:
                        candidate_pairs[(tname, cname)] = (score, prev_d)
                    if cinfo.get("code_set"):
                        triggered_cs.add(int(cinfo["code_set"]))

        self._apply_temporal_and_identifier_disambiguation(low, candidate_pairs, triggered_cs, strict_precision=strict_precision, request_text=request_text)

        # Build ColumnMatch objects
        matched_cols: list[ColumnMatch] = []
        for (tbl, col), (score, da2_hint) in candidate_pairs.items():
            tinfo = self._tables.get(tbl)
            if not tinfo:
                continue
            cinfo = next((c for c in tinfo.get("columns", []) if c["name"] == col), None)
            if not cinfo:
                continue

            da2_rec = self._da2_by_name.get(da2_hint) if da2_hint else None
            if not da2_rec and tbl == "PERSON" and col == "NAME_FULL_FORMATTED" and "guarantor" not in low:
                da2_rec = self._da2_by_name.get("Person Name Full Format")
            if not da2_rec:
                # Look up by exact v500_target table.column prefix first, falling back to table prefix only if no da2_hint
                allow_cwf_adj_pay = has_any_fin
                da2_rec = next(
                    (
                        e for e in self._da2_elements
                        if (
                            (e["v500_target"].startswith(f"{tbl}.{col}") or (not da2_hint and e["v500_target"].startswith(tbl)))
                            and ("guarantor" in low or e["da2_field_name"] != "Acct Guarantor Name")
                            and (allow_cwf_adj_pay or e.get("da2_domain") not in ("CWF GS Adjustments", "CWF GS Payments", "CWF GS Revenue"))
                        )
                    ),
                    None,
                )

            default_alias_cs = 319 if (tbl == "ENCNTR_ALIAS" and col == "ALIAS") else (4 if (tbl == "PERSON_ALIAS" and col == "ALIAS") else None)
            cs_num = cinfo.get("code_set") or (da2_rec.get("code_set") if da2_rec else None) or default_alias_cs
            cs_rec = self._codesets.get(int(cs_num)) if cs_num else None
            if cs_num:
                triggered_cs.add(int(cs_num))

            matched_cols.append(
                ColumnMatch(
                    table=tbl,
                    column=col,
                    v500_type=cinfo.get("v500_type", "VARCHAR"),
                    ccl_type=cinfo.get("ccl_type", "vc (STRING)"),
                    nullable=bool(cinfo.get("nullable", True)),
                    code_set=int(cs_num) if cs_num else None,
                    code_set_name=cs_rec["name"] if cs_rec else None,
                    cdf_meanings=(cs_rec.get("ccl_meanings", []) or cs_rec.get("ccl_displaykeys", []))[:10] if cs_rec else [],
                    in_healtheintent_ods=bool(cinfo.get("in_healtheintent_ods", False)),
                    definition=cinfo.get("definition", ""),
                    da2_field_name=da2_rec["da2_field_name"] if da2_rec else da2_hint,
                    da2_domain=da2_rec["da2_domain"] if da2_rec else "CWC Encounter",
                    da2_subentity=da2_rec["da2_bus_view_subentity"] if da2_rec else tbl.title(),
                    element_uuid=da2_rec.get("element_uuid") if da2_rec else None,
                    value_semantics=da2_rec["value_semantics_and_guardrails"] if da2_rec else cinfo.get("definition", ""),
                    score=round(score, 2),
                )
            )

        # Enforce clinical-vs-billing domain isolation and RefinementSpec column exclusions
        excl_low = {x.lower() for x in eff_ref.exclude_columns}
        excl_v500: set[tuple[str, str]] = set()
        if "hp financial class" in excl_low or "bo fin class" in excl_low:
            excl_v500.add(("ENCOUNTER", "FINANCIAL_CLASS_CD"))
        if "hp plan name" in excl_low:
            excl_v500.update({("HEALTH_PLAN", "PLAN_NAME"), ("HEALTH_PLAN", "PLAN_TYPE_CD")})
        if "enc loc room" in excl_low:
            excl_v500.add(("ENCOUNTER", "LOC_ROOM_CD"))
        if "enc loc bed" in excl_low or "bed loc" in excl_low:
            excl_v500.add(("ENCOUNTER", "LOC_BED_CD"))
        if "person mrn" in excl_low:
            excl_v500.add(("PERSON_ALIAS", "ALIAS"))
        if "enc attending phys" in excl_low:
            excl_v500.update({
                ("ENCNTR_PRSNL_RELTN", "PRSNL_PERSON_ID"),
                ("ENCNTR_PRSNL_RELTN", "ENCNTR_PRSNL_R_CD"),
                ("PRSNL", "NAME_FULL_FORMATTED"),
            })
        if "enc discharge disp" in excl_low:
            excl_v500.add(("ENCOUNTER", "DISCH_DISPOSITION_CD"))
        if "prsnl position" in excl_low or not has_prsnl_pos:
            excl_v500.add(("PRSNL", "POSITION_CD"))
            if not has_prsnl_pos:
                triggered_cs.discard(88)

        filtered_cols: list[ColumnMatch] = []
        for cm in matched_cols:
            if (cm.table, cm.column) in excl_v500:
                continue
            fn = (cm.da2_field_name or "").strip()
            if fn.lower() in excl_low:
                continue
            if not has_prsnl_pos and fn == "Prsnl Position":
                continue
            if not has_any_fin and (
                fn.startswith(("Adj ", "AdjTrans", "Pay ", "PayTrans", "Chg ", "BO "))
                or ((cm.da2_domain or "").startswith("CWF ") and not fn.startswith("HP "))
            ):
                continue
            filtered_cols.append(cm)

        matched_cols = filtered_cols
        matched_cols.sort(key=lambda c: c.score, reverse=True)
        matched_cols = matched_cols[:top_k_columns]

        forms_res = self._forms_resolver.resolve(request_text, top_k=3)

        # Assemble Code Set metadata
        cs_list = []
        for cs_id in sorted(triggered_cs):
            if cs_id in self._codesets:
                cs_entry = self._codesets[cs_id]
                cs_list.append({
                    "code_set": cs_id,
                    "name": cs_entry["name"],
                    "cdf_meanings": cs_entry.get("ccl_meanings", [])[:12],
                    "display_keys": cs_entry.get("ccl_displaykeys", [])[:10],
                })

        # Determine join graph and guardrails
        tables_present = {c.table for c in matched_cols}
        join_graph = self._build_join_graph(tables_present)
        guardrails, strategy = self._evaluate_guardrails(low, tables_present, tenancy_id, matched_da2_views)

        return InterpretationResult(
            request_text=request_text,
            inferred_tenancy=tenancy_id,
            tenancy_rationale=tenancy_reason,
            legal_execution_surfaces=legal_surfaces,
            recommended_strategy=strategy,
            matched_columns=matched_cols,
            matched_forms_and_outputs=forms_res,
            matched_code_sets=cs_list,
            join_graph=join_graph,
            guardrails_triggered=guardrails,
            matched_da2_views=matched_da2_views,
            refinement=eff_ref,
            matched_template_id=matched_tpl_id,
        )

    def _build_join_graph(self, tables_present: set[str]) -> list[str]:
        edges = ["ENCOUNTER.PERSON_ID = PERSON.PERSON_ID"]
        if "ENCNTR_ALIAS" in tables_present:
            edges.append("ENCNTR_ALIAS.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND ENCNTR_ALIAS.ENCNTR_ALIAS_TYPE_CD = uar_get_code_by('MEANING', 319, 'FIN NBR') AND ENCNTR_ALIAS.ACTIVE_IND = 1")
        if "PERSON_ALIAS" in tables_present:
            edges.append("PERSON_ALIAS.PERSON_ID = PERSON.PERSON_ID AND PERSON_ALIAS.PERSON_ALIAS_TYPE_CD = uar_get_code_by('MEANING', 4, 'MRN') AND PERSON_ALIAS.ACTIVE_IND = 1")
        if "ENCNTR_LOC_HIST" in tables_present:
            edges.append("ENCNTR_LOC_HIST.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND ENCNTR_LOC_HIST.ACTIVE_IND = 1")
        if "ENCNTR_PRSNL_RELTN" in tables_present:
            edges.append("ENCNTR_PRSNL_RELTN.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND ENCNTR_PRSNL_RELTN.ACTIVE_IND = 1 -> PRSNL.PERSON_ID = ENCNTR_PRSNL_RELTN.PRSNL_PERSON_ID")
        if "CLINICAL_EVENT" in tables_present:
            edges.append("CLINICAL_EVENT.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND CLINICAL_EVENT.VALID_UNTIL_DT_TM >= cnvtdatetime('31-DEC-2100 00:00:00') AND CLINICAL_EVENT.RESULT_STATUS_CD IN (AUTH, MODIFIED, ALTERED)")
        if "DCP_FORMS_ACTIVITY" in tables_present:
            edges.append("DCP_FORMS_ACTIVITY.ENCNTR_ID = ENCOUNTER.ENCNTR_ID -> DCP_FORMS_ACTIVITY_COMP.DCP_FORMS_ACTIVITY_ID = DCP_FORMS_ACTIVITY.DCP_FORMS_ACTIVITY_ID -> CLINICAL_EVENT.EVENT_ID = DCP_FORMS_ACTIVITY_COMP.PARENT_ENTITY_ID")
        if "CE_INTAKE_OUTPUT_RESULT" in tables_present:
            edges.append("CE_INTAKE_OUTPUT_RESULT.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND CE_INTAKE_OUTPUT_RESULT.VALID_UNTIL_DT_TM >= cnvtdatetime('31-DEC-2100 00:00:00')")
        if "CE_BLOB" in tables_present or "CE_BLOB_RESULT" in tables_present:
            edges.append("CLINICAL_EVENT.EVENT_ID = CE_BLOB_RESULT.EVENT_ID -> CE_BLOB.EVENT_ID = CE_BLOB_RESULT.EVENT_ID (decompress via uar_ocf_uncompress if COMPRESSION_CD = OCFCOMP)")
        if "ORDERS" in tables_present:
            edges.append("ORDERS.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND ORDERS.ACTIVE_IND = 1")
        if "ORDER_DETAIL" in tables_present:
            edges.append("ORDER_DETAIL.ORDER_ID = ORDERS.ORDER_ID")
        if "MED_ADMIN_EVENT" in tables_present or "CE_MED_RESULT" in tables_present:
            edges.append("MED_ADMIN_EVENT.EVENT_ID = CLINICAL_EVENT.EVENT_ID -> CE_MED_RESULT.EVENT_ID = CLINICAL_EVENT.EVENT_ID")
        if "TASK_ACTIVITY" in tables_present:
            edges.append("TASK_ACTIVITY.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND TASK_ACTIVITY.ACTIVE_IND = 1")
        if "SCH_APPT" in tables_present:
            edges.append("SCH_APPT.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND SCH_APPT.ROLE_MEANING = 'PATIENT' -> SCH_EVENT.SCH_EVENT_ID = SCH_APPT.SCH_EVENT_ID -> SCH_EVENT_ACTION.SCH_EVENT_ID = SCH_EVENT.SCH_EVENT_ID")
        if "CHARGE" in tables_present:
            edges.append("CHARGE.ENCNTR_ID = ENCOUNTER.ENCNTR_ID AND CHARGE.ACTIVE_IND = 1 -> CHARGE_MOD.CHARGE_ITEM_ID = CHARGE.CHARGE_ITEM_ID")
        if "PFT_ENCNTR" in tables_present or "BO_HP_RELTN" in tables_present:
            edges.append("PFT_ENCNTR.ENCNTR_ID = ENCOUNTER.ENCNTR_ID -> BENEFIT_ORDER.PFT_ENCNTR_ID = PFT_ENCNTR.PFT_ENCNTR_ID -> BO_HP_RELTN.BENEFIT_ORDER_ID = BENEFIT_ORDER.BENEFIT_ORDER_ID")
        return edges

    def _evaluate_guardrails(
        self, low: str, tables_present: set[str], tenancy_id: str, matched_da2_views: list[dict[str, Any]] | None = None
    ) -> tuple[list[str], str]:
        guardrails: list[str] = []
        if matched_da2_views:
            summaries = [
                f"{v['da2_view_entry']} (alias={v['da2_alias']}, da2_join_key={v['effective_da2_join_key']} -> raw V500 {v['underlying_v500_table']}.{v['v500_join_column']})"
                for v in matched_da2_views[:5]
            ]
            guardrails.append(
                "DA2_VIEW_ALIAS_RESOLUTION: Keep DA2 view/alias names (XCW_* / CWC_*) distinct from raw V500 table names: " + "; ".join(summaries)
            )
        if tables_present & {"V500_EVENT_SET_CANON", "V500_EVENT_SET_CODE", "V500_EVENT_SET_EXPLODE"} or "event_set" in low or "event set" in low:
            guardrails.append(
                "EVENT_SET_HIERARCHY_TRAVERSAL: Expand V500_EVENT_SET_CANON (levels 1..10 rooted at V500_EVENT_SET_CODE.EVENT_SET_CD_DEFINITION = 'ALL OCF EVENT SETS') and join V500_EVENT_SET_EXPLODE on BOTTOM_LEVEL_EVENT_SET_CODE = EVENT_SET_CD to resolve leaf EVENT_CD (Code Set 72). Ensure orphan-event queries include all 24 columns (including BOTTOM_LEVEL_EVENT_SET_CODE and BOTTOM_LEVEL_EVENT_SET_DISPLAY)."
            )
        if tenancy_id in {"continuum", "communityworks"}:
            guardrails.append(
                "TENANCY_FENCE: Target environment is a multi-tenant shared Millennium domain; custom CCL (.prg) upload is restricted. Primary deployable artifact must be a DA2 Domain Query + BIRT 4.4.2 .rptdesign."
            )
        if "CLINICAL_EVENT" in tables_present or "CE_MED_RESULT" in tables_present or "CE_BLOB" in tables_present:
            guardrails.append(
                "CE_VERSION_DEDUP (CLINICAL_EVENT_VALID_UNTIL_AND_AUTH_STATUS): CLINICAL_EVENT is row-versioned. Mandatory filter: CE Valid Until Dt/Tm >= 12/31/2100 00:00:00 AND CE Result Status in (Auth (Verified), Modified, Altered) (Code Set 8) to prevent superseded/In-Error rows from double-counting."
            )
            guardrails.append(
                "CE_EVENT_FILTER_LIST_AND_DTA_RESOLUTION: 'CE Event' (CLINICAL_EVENT.EVENT_CD, Code Set 72) contains 10,000+ PowerChart values. In DA2 Qualifications, use 'Modify Filter List...' -> 'Filter values:' substring search, or resolve via Event Set Hierarchy (Code Set 93 -> V500_EVENT_SET_EXPLODE) and Discrete Task Assay ('DTA Mnemonic', Code Set 14003)."
            )
        if tables_present & {"PERSON_ALIAS", "ENCNTR_ALIAS", "ENCNTR_PRSNL_RELTN", "ADDRESS", "PHONE", "CHARGE_MOD"}:
            guardrails.append(
                "NO_JOIN_REQUIRED_SCALAR_FUNCTIONS (ALIAS_ACTIVE_EFFECTIVE_DEDUP): When only primary encounter/person/charge values are needed in CWC Encounter, prefer DA2 inline scalar Functions ('Person MRN', 'Hm Addr *', 'Hm Phn Number', 'Enc Financial Num', 'Enc Attending Phys', 'Enc Attending Phys NPI', 'Enc Admitting Phys', 'Enc Case Manager', 'F Chg CPT', 'F Chg HCPCS', 'F Chg Rev Code') over 1-to-many child sub-entities to avoid row multiplication."
            )
        if ("active" in low and "census" in low) or any(w in low for w in ("active inpatient", "current inpatient", "midnight census", "currently admitted", "inhouse")):
            guardrails.append(
                "ACTIVE_CENSUS_NULL_DISCH (ACTIVE_CENSUS_NULL_DISCH_GUARD): Never filter active census on Enc Disch Dt Tm >= <date>; active patients have Enc Disch Dt Tm IS NULL. Enforce Enc Admit Dt Tm <= cutoff AND Enc Disch Dt Tm IS NULL AND Enc Active Ind = 1, and uncheck DA2's auto-checked 'is not null' box on optional date prompts."
            )

        # Check for cross-domain or multi-child-list fan-out
        has_clinical_child = bool(tables_present & {"CLINICAL_EVENT", "ORDERS", "TASK_ACTIVITY", "MED_ADMIN_EVENT", "SCH_APPT", "ENCNTR_PRSNL_RELTN"})
        has_financial = bool(tables_present & {"CHARGE", "PFT_ENCNTR", "BO_HP_RELTN"})
        is_provider_note_query = any(
            w in low for w in ("consult note", "initial consult", "progress note", "provider note", "physician note", "oppe", "fppe")
        )
        is_mar_query = (bool(re.search(r"\bmar\b", low)) or any(w in low for w in ("bcma", "med_admin", "medication administration", "administered", "prn dose", "doses given", "mme")))
        if is_mar_query:
            guardrails.append(
                "MAR_DISCONTINUED_ORDER_GUARD: For medication administration / PRN historical audits, filter Order Status Not In (Canceled, Voided) [CS 6004: 2542, 2553] — NEVER filter Not In (Discontinued) [2545], which silently erases historical doses administered under later-discontinued orders."
            )
        if "DIAGNOSIS" in tables_present and any(w in low for w in ("admit", "census", "inpatient", "same-day", "missing", "optional")):
            guardrails.append(
                "ADMITTING_DIAGNOSIS_OUTER_JOIN_GUARD: Place DIAGNOSIS / NOMENCLATURE on an OUTER JOIN (DA2 joinType='3' / Oracle (+) / CCL OUTERJOIN) so same-day admissions without a coded diagnosis are not dropped."
            )
        guardrails.append(
            "TEMPORAL_ANCHOR_DISAMBIGUATION: Use CLINICAL_EVENT.EVENT_END_DT_TM (CE End Dt/Tm) for clinical observation time (never UPDT_DT_TM), MED_ADMIN_EVENT.BEG_DT_TM (CE Med Admin Start Dt/Tm) for administration time vs ORDERS.ORIG_ORDER_DT_TM (Order Dt/Tm) for order placement, ENCOUNTER.INPATIENT_ADMIT_DT_TM vs REG_DT_TM vs ARRIVE_DT_TM, and CHARGE.SERVICE_DT_TM vs POSTED_DT_TM."
        )
        guardrails.append(
            "TEST_PATIENT_AND_RETIRED_RESOURCE_EXCLUSION: Exclude test patients (Person Name Last < 'ZZ') and retired 'zz*'-prefixed scheduling resources/elements."
        )

        child_groups = 0
        if "CLINICAL_EVENT" in tables_present and not (is_mar_query and tables_present <= {"ENCOUNTER", "PERSON", "ENCNTR_ALIAS", "PERSON_ALIAS", "CLINICAL_EVENT", "MED_ADMIN_EVENT", "CE_MED_RESULT", "ORDERS"}):
            child_groups += 1
        if ("ORDERS" in tables_present or "MED_ADMIN_EVENT" in tables_present) and not is_mar_query:
            child_groups += 1
        elif is_mar_query and ("DIAGNOSIS" in tables_present or "CHARGE" in tables_present or "SCH_APPT" in tables_present):
            child_groups += 1
        for t in ("CHARGE", "DIAGNOSIS", "ENCNTR_PRSNL_RELTN", "SCH_APPT", "TASK_ACTIVITY"):
            if t in tables_present and not (is_provider_note_query and t == "ENCNTR_PRSNL_RELTN"):
                child_groups += 1
        multi_children = child_groups

        if has_clinical_child and has_financial:
            strategy = "two_dataset_birt_join"
            guardrails.append(
                "CROSS_DOMAIN_BIRT_JOIN: Request combines clinical (CWC Encounter) and financial/charge (CWF / Charges) entities. Emit two separate DA2 ODA datasets joined inside BIRT 4.4.2 via a <joint-data-set> on Enc Id (ENCNTR_ID) to prevent Cartesian fan-out."
            )
        if multi_children >= 2:
            strategy = "two_dataset_birt_join"
            guardrails.append(
                "CHILD_LIST_FANOUT_GUARD (MULTI_CHILD_1_TO_N_CARTESIAN_FANOUT_GUARD): Selecting multiple 1-to-many sub-entities side-by-side in a single flat DA2 query causes a Cartesian cross-join. Split into linked BIRT datasets joined on Enc Id."
            )
        else:
            strategy = "single_domain_oda"

        guardrails.append(
            "DA2_ROW_CAP_GUARD: Enable 'Fetch All Rows From Data Source' on every dataset in BIRT to prevent DA2's silent 25,000-row truncation."
        )
        return guardrails, strategy

    def _apply_temporal_and_identifier_disambiguation(
        self,
        low: str,
        candidate_pairs: dict[tuple[str, str], tuple[float, str | None]],
        triggered_cs: set[int],
        strict_precision: bool = False,
        request_text: str = "",
    ) -> None:
        is_mar = (bool(re.search(r"\bmar\b", low)) or any(w in low for w in ("bcma", "med_admin", "medication administration", "administered", "prn dose", "doses given", "mme")))
        if is_mar:
            candidate_pairs[("MED_ADMIN_EVENT", "BEG_DT_TM")] = (9.9, "CE Med Admin Start Dt/Tm")
            candidate_pairs[("CE_MED_RESULT", "ADMIN_DOSAGE")] = (9.8, "CE Med Admin Dosage")
            candidate_pairs[("ORDERS", "ORDER_STATUS_CD")] = (9.7, "Order Status")
            triggered_cs.add(6004)
            if not any(w in low for w in ("order date", "ordered date", "orig_order_dt_tm", "when ordered", "order placement")):
                if strict_precision and ("ORDERS", "ORIG_ORDER_DT_TM") in candidate_pairs:
                    del candidate_pairs[("ORDERS", "ORIG_ORDER_DT_TM")]
                elif ("ORDERS", "ORIG_ORDER_DT_TM") in candidate_pairs:
                    s_val, d_val = candidate_pairs[("ORDERS", "ORIG_ORDER_DT_TM")]
                    candidate_pairs[("ORDERS", "ORIG_ORDER_DT_TM")] = (max(1.0, s_val - 3.0), d_val)
            if strict_precision and not any(w in low for w in ("appointment", "clinic", "slot")):
                for k_tbl, k_col in list(candidate_pairs.keys()):
                    if k_tbl in ("SCH_APPT", "SCH_EVENT", "SCH_EVENT_ACTION", "TASK_ACTIVITY"):
                        del candidate_pairs[(k_tbl, k_col)]

        if any(t == "CLINICAL_EVENT" for t, _ in list(candidate_pairs.keys())):
            candidate_pairs[("CLINICAL_EVENT", "EVENT_END_DT_TM")] = (9.8, "CE End Dt/Tm")
            candidate_pairs[("CLINICAL_EVENT", "RESULT_STATUS_CD")] = (9.6, "CE Result Status")
            if any(w in low for w in ("verified date", "verified_dt_tm", "verification time", "signed date")):
                candidate_pairs[("CLINICAL_EVENT", "VERIFIED_DT_TM")] = (9.6, "CE Verified Dt/Tm")
            elif strict_precision:
                candidate_pairs.pop(("CLINICAL_EVENT", "VERIFIED_DT_TM"), None)
            if not any(w in low for w in ("performed_dt_tm", "performed date", "performed time")) and strict_precision:
                candidate_pairs.pop(("CLINICAL_EVENT", "PERFORMED_DT_TM"), None)

        if "inpatient admit" in low or "inpatient admission" in low or "inpatient_admit_dt_tm" in low:
            candidate_pairs[("ENCOUNTER", "INPATIENT_ADMIT_DT_TM")] = (9.8, "Enc Inpatient Admit Dt/Tm")
            if strict_precision and "reg_dt_tm" not in low and "registration" not in low:
                candidate_pairs.pop(("ENCOUNTER", "REG_DT_TM"), None)
            if strict_precision and not any(w in low for w in ("arrive", "arrival", "triage", "arrive_dt_tm")):
                candidate_pairs.pop(("ENCOUNTER", "ARRIVE_DT_TM"), None)
        elif any(w in low for w in ("arrive", "arrival", "triage", "arrive_dt_tm")):
            candidate_pairs[("ENCOUNTER", "ARRIVE_DT_TM")] = (9.8, "Enc Arrive Dt/Tm")
        elif any(w in low for w in ("registration", "reg_dt_tm", "registered")):
            candidate_pairs[("ENCOUNTER", "REG_DT_TM")] = (9.8, "Enc Registration Dt/Tm")

        if any(t == "DIAGNOSIS" for t, _ in list(candidate_pairs.keys())):
            candidate_pairs[("DIAGNOSIS", "DIAGNOSIS_DISPLAY")] = (9.6, "Diagnosis Display")

        if is_financial_adjustment_request(request_text or low):
            candidate_pairs[("PFT_ENCNTR", "ADJUSTMENT_BALANCE")] = (9.8, "Adj Amount")
            candidate_pairs[("TRANS_LOG", "TOTAL_TRANS_AMOUNT")] = (9.7, "Adj Amount")
            candidate_pairs[("TRANS_LOG", "POST_DT_TM")] = (9.7, "Adj Post Dt")
            candidate_pairs[("TRANS_LOG", "TRANS_REASON_CD")] = (9.5, "Adj Trans Reason")
        if is_financial_payment_request(request_text or low):
            candidate_pairs[("PFT_ENCNTR", "APPLIED_PAYMENT_BALANCE")] = (9.8, "Pay Amount")
            candidate_pairs[("TRANS_LOG", "TOTAL_TRANS_AMOUNT")] = (9.7, "Pay Amount")
            candidate_pairs[("TRANS_LOG", "POST_DT_TM")] = (9.7, "Pay Post Dt")
            candidate_pairs[("TRANS_LOG", "TRANS_REASON_CD")] = (9.5, "Pay Trans Reason")
        if any(w in low for w in ("general ledger", "cwf gl", "gl account")):
            candidate_pairs[("GL_TRANS_LOG", "ACCT_NAME")] = (9.8, "GL Account Name")
            candidate_pairs[("TRANS_LOG", "TOTAL_TRANS_AMOUNT")] = (9.7, "GL Trans Amount")
            candidate_pairs[("TRANS_LOG", "POST_DT_TM")] = (9.7, "GL Post Dt")
        if any(t == "SCH_APPT" for t, _ in list(candidate_pairs.keys())) and strict_precision:
            candidate_pairs.pop(("PRSNL", "CREATE_DT_TM"), None)
            if "balance" not in low and "payment" not in low and "adjustment" not in low:
                candidate_pairs.pop(("PFT_ENCNTR", "BALANCE"), None)

        if any(t == "CHARGE" for t, _ in list(candidate_pairs.keys())):
            if any(w in low for w in ("posted date", "posted_dt_tm", "posting date", "chg posted")):
                candidate_pairs[("CHARGE", "POSTED_DT_TM")] = (9.7, "Chg Posted Dt/Tm")
            else:
                candidate_pairs[("CHARGE", "SERVICE_DT_TM")] = (9.7, "Chg Service Dt/Tm")

        # Never use housekeeping UPDT_DT_TM for clinical/operational filtering unless explicitly requested
        if "updt_dt_tm" not in low and "update dt" not in low:
            for tbl_u in ("CLINICAL_EVENT", "ORDERS", "ENCOUNTER", "CHARGE", "DIAGNOSIS"):
                candidate_pairs.pop((tbl_u, "UPDT_DT_TM"), None)

        # Identifier & Alias disambiguation (MRN CS 4 vs FIN CS 319)
        if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low) for w in ("mrn", "medical record number", "cmrn", "person_alias")):
            candidate_pairs[("PERSON_ALIAS", "ALIAS")] = (9.7, "Person MRN")
            triggered_cs.add(4)
        elif strict_precision:
            candidate_pairs.pop(("PERSON_ALIAS", "ALIAS"), None)
            candidate_pairs.pop(("PERSON_ALIAS", "PERSON_ALIAS_TYPE_CD"), None)

        if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low) for w in ("fin", "financial number", "account number", "encounter number", "encntr_alias")):
            candidate_pairs[("ENCNTR_ALIAS", "ALIAS")] = (9.7, "Enc FIN")
            triggered_cs.add(319)
        elif strict_precision:
            candidate_pairs.pop(("ENCNTR_ALIAS", "ALIAS"), None)
            candidate_pairs.pop(("ENCNTR_ALIAS", "ENCNTR_ALIAS_TYPE_CD"), None)
