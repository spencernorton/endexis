#!/usr/bin/env python3
"""
Builds and verifies the V1 Multi-Task Supervised Fine-Tuning (SFT), Contrastive DPO,
and Frozen Evaluation datasets for Discern Intelligence V1 (including provenance-tagged
historical reference extensions).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from discern_intelligence import (  # noqa: E402
    CCLCompiler,
    DA2ReportGenerator,
    FormsAndOutputResolver,
    MillenniumInterpreter,
)
from discern_intelligence.knowledge import (  # noqa: E402
    load_codeset_catalog,
    load_da2_elements,
    load_da2_domain_taxonomy,
    load_da2_filter_value_catalog,
    load_da2_canonical_reports_catalog,
    load_healtheintent_edw_catalog,
    load_da2_view_alias_catalog,
    load_event_set_and_crosswalks,
    load_forms_catalog,
    load_som_summary,
    load_v500_tables,
)

OUT_DIR = REPO_ROOT / "data"

CANONICAL_BENCHMARK_SCENARIOS = [
    {
        "id": "continuum_active_rehab_census_and_therapy",
        "tenancy": "continuum",
        "request": "Build an active inpatient rehabilitation census showing MRN, FIN, patient full name, current nurse unit, room/bed, attending physician, and verified physical therapy treatment minutes with billed therapy charges.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "powerform_fim_care_assessment_scores",
        "tenancy": "continuum",
        "request": "Determine which Millennium tables and columns hold charted PowerForm IRF-PAI / FIM / CARE functional assessment scores and generate a DA2 report and CCL query for verified answers.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "bcma_missed_and_late_medication_admin",
        "tenancy": "communityworks",
        "request": "Create a barcode medication administration (BCMA) audit report showing patient name, FIN, nurse unit, medication order mnemonic, scheduled date/time, performed date/time, patient/medication barcode scan indicators, and administered dosage.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "dynamic_documentation_discharge_summary_blobs",
        "tenancy": "standalone_rho",
        "request": "Locate the tables and columns that store Dynamic Documentation PowerNotes and discharge summary narrative text blobs and compile a Standalone RHO CCL query that extracts finalized notes.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "iview_vitals_and_intake_output_balance",
        "tenancy": "communityworks",
        "request": "Identify the Millennium columns for InteractiveView vital signs and 2G Intake and Output fluid balance volumes (mL) and generate a DA2 query and CCL program.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "order_entry_format_opioid_benzo_audit",
        "tenancy": "continuum",
        "request": "Audit inpatient discharges with opioid and benzodiazepine medication orders, pulling Order Entry Format details (dose, route, frequency, indication), discharge disposition, facility, and patient age.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "outpatient_scheduling_noshow_and_cancel_reasons",
        "tenancy": "continuum",
        "request": "Generate an outpatient therapy scheduling report with appointment begin/end times, duration, appointment state, scheduling action history, and cancellation or no-show reasons.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "revcycle_cwf_charges_payments_adjustments",
        "tenancy": "communityworks",
        "request": "Build a cross-domain clinical and financial reconciliation report joining encounter admit/discharge dates and primary health plan with Benefit Order total charges, total payments, and total adjustments.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "overdue_nursing_inbox_tasks",
        "tenancy": "continuum",
        "request": "Extract overdue and not-done clinical care tasks from the nursing inbox including task status, task status reason, PRN vs scheduled task class, and scheduled overdue timestamp.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "cpdi_scanned_consent_forms",
        "tenancy": "standalone_rho",
        "request": "Find the Millennium Data Model tables and columns that track CPDI interactive eSignature consent forms and document imaging completion status.",
        "provenance": "discern_intelligence_v1:core_benchmark",
    },
    {
        "id": "da2_xcw_view_alias_join_resolution",
        "tenancy": "communityworks",
        "request": "Resolve the DA2 views XCW_DRG_ENCNTR_EXTENSION (DEE), XCW_ENCNTR_LOC_HIST (ELH), XCW_MED_ADMIN_EVENT (MAE), and XCW_SCH_EV_ACTION (SCH_EV_ACTION) into their DA2 join keys and underlying Millennium V500 tables.",
        "provenance": "historical_reference_findings_2026-11-03:candidate_1:da2_schema_view_map",
    },
    {
        "id": "event_set_hierarchy_lvl10_and_search_traversal",
        "tenancy": "standalone_rho",
        "request": "Traverse the 10-level V500_EVENT_SET_CANON and V500_EVENT_SET_CODE hierarchy rooted at ALL OCF EVENT SETS (MD_D_EVENT_SET_HIERARCHY and MD_D_EVENT_SET_SEARCH) to explode bottom-level event sets via V500_EVENT_SET_EXPLODE into leaf CLINICAL_EVENT EVENT_CD values.",
        "provenance": "historical_reference_findings_2026-11-03:candidate_2:uar-dev-master/datasets/MD_D_EVENT_SET_HIERARCHY_and_MD_D_EVENT_SET_SEARCH",
    },
    {
        "id": "hcahps_language_pid15_and_ub04_admit_discharge_extract",
        "tenancy": "continuum",
        "request": "Generate an inpatient HCAHPS and regulatory discharge extract with Person Language (Code Set 36, HL7 PID-15), UB-04 FL-15 Point of Origin / Enc Admit Source (Code Set 2), UB-04 FL-17 Enc Discharge Disp (Code Set 19), F Enc Recur Seq, and HP Plan Name Key.",
        "provenance": "historical_reference_findings_2026-11-03:candidate_3:selected_definition_sheets",
    },
    {
        "id": "controlled_substance_mar_mltm_csa_schedule_audit",
        "tenancy": "communityworks",
        "request": "Audit controlled substance medication administrations using MLTM_NDC_MAIN_DRUG_CODE CSA_SCHEDULE (Mltm Legal Status), Order Mnemonic, Order AS Mnemonic, Med Adm Id, and CE Med Admin Dose.",
        "provenance": "historical_reference_findings_2026-11-03:candidate_4:validated_ejs_pharmacy_queries",
    },
]


def build_corpora() -> dict[str, int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    interpreter = MillenniumInterpreter()
    forms_resolver = FormsAndOutputResolver()
    ccl_compiler = CCLCompiler(interpreter)
    da2_gen = DA2ReportGenerator(interpreter, ccl_compiler)

    sft_rows = []
    dpo_rows = []
    eval_rows = []

    # 1. Schema & DA_ELEMENT Grounding SFT pairs from DA2 Element Catalog + Code Sets + Forms
    for elem in load_da2_elements():
        sft_rows.append({
            "task": "interpret_da2_element_to_v500",
            "provenance": elem.get("provenance", "discern_intelligence_v1:da2_element_catalog"),
            "instruction": f"Map the Discern Analytics 2.0 (DA2) field '{elem['da2_field_name']}' to its underlying Cerner Millennium V500 table/column, data type, Code Set, and value semantics.",
            "response": {
                "da2_field_name": elem["da2_field_name"],
                "element_uuid": elem["element_uuid"],
                "da2_domain": elem["da2_domain"],
                "da2_subentity": elem["da2_bus_view_subentity"],
                "v500_target": elem["v500_target"],
                "ccl_data_type": elem["ccl_data_type"],
                "code_set": elem.get("code_set"),
                "value_semantics_and_guardrails": elem["value_semantics_and_guardrails"],
            },
        })

    for fm in load_forms_catalog():
        sft_rows.append({
            "task": "resolve_clinical_form_or_output",
            "provenance": "discern_intelligence_v1:forms_and_outputs_catalog",
            "instruction": f"Which tables and columns in the Cerner Millennium V500 Data Model and DA2 store '{fm['name']}'?",
            "response": {
                "form_id": fm["id"],
                "definition_tables": fm["definition_tables"],
                "activity_tables": fm["activity_tables"],
                "payload_columns": fm["payload_columns"],
                "join_pattern_sql": fm["join_pattern_sql"],
                "da2_mapping": fm["da2_mapping"],
            },
        })

    for cs_id, cs_rec in sorted(load_codeset_catalog().items()):
        if cs_rec.get("ccl_meanings") or not cs_rec["name"].startswith("Millennium Code Set"):
            sft_rows.append({
                "task": "resolve_millennium_codeset",
                "provenance": cs_rec.get("provenance", "discern_intelligence_v1:codeset_catalog"),
                "instruction": f"What does Cerner Millennium Code Set {cs_id} ({cs_rec['name']}) represent, which V500 columns reference it, and what CDF_MEANING values are used in CCL uar_get_code_by()?",
                "response": {
                    "code_set": cs_id,
                    "name": cs_rec["name"],
                    "hl7_or_ub04_field": cs_rec.get("hl7_or_ub04_field"),
                    "referenced_by_columns": cs_rec.get("referenced_by_columns", [])[:15],
                    "ccl_meanings": cs_rec.get("ccl_meanings", []),
                    "ccl_displaykeys": cs_rec.get("ccl_displaykeys", []),
                },
            })

    # Add high-value V500 table summaries
    for tname, tinfo in sorted(load_v500_tables().items()):
        sft_rows.append({
            "task": "interpret_v500_table",
            "provenance": tinfo.get("provenance", "discern_intelligence_v1:v500_high_value_tables"),
            "instruction": f"Describe the Cerner Millennium V500 table {tname}, its primary keys, HealtheIntent ODS v1 availability, and its typed columns.",
            "response": {
                "table": tname,
                "table_type": tinfo["table_type"],
                "definition": tinfo["definition"],
                "primary_key": tinfo["primary_key"],
                "in_healtheintent_ods": tinfo["in_healtheintent_ods"],
                "columns": [
                    {
                        "name": c["name"],
                        "ccl_type": c["ccl_type"],
                        "code_set": c["code_set"],
                        "definition": c["definition"],
                    }
                    for c in tinfo["columns"][:20]
                ],
            },
        })

    for som in load_som_summary():
        sft_rows.append({
            "task": "interpret_oracle_som_entity",
            "provenance": "discern_intelligence_v1:oracle_som_v2026.4.01",
            "instruction": f"Describe the Oracle Health Semantic Object Model (SOM) entity {som['object_name']} and its key EHI fields.",
            "response": som,
        })

    # 1B. Historical Reference Candidate 1: 48 DA2 XCW_* view/alias/join mappings + 9 relational variants
    view_cat = load_da2_view_alias_catalog()
    for v_entry in view_cat.get("da2_views", []) + view_cat.get("relational_path_variants", []):
        sft_rows.append({
            "task": "resolve_da2_view_alias_join",
            "provenance": v_entry["provenance"],
            "instruction": (
                f"Resolve the Discern Analytics 2.0 (DA2) view '{v_entry['da2_view_entry']}' "
                f"(alias '{v_entry['da2_alias']}') into its DA2 join key, abbreviation, and separate underlying Cerner Millennium V500 table."
            ),
            "response": v_entry,
        })

    # Add EJS SQL Engine convention SFT example
    sft_rows.append({
        "task": "resolve_ejs_sql_and_combined_query_conventions",
        "provenance": "historical_reference_findings_2026-11-03:candidates_4_and_6",
        "instruction": "How does the Discern SQL Engine (EJS v3.15.1) alias Millennium tables, translate code values and UTC timestamps in Oracle SQL, and name columns in DA2 Combined Queries?",
        "response": view_cat["ejs_sql_engine_conventions"],
    })

    # 1C. Historical Reference Candidate 2: MD_D_EVENT_SET_HIERARCHY & MD_D_EVENT_SET_SEARCH
    esc_cat = load_event_set_and_crosswalks()
    esh = esc_cat["event_set_hierarchy_and_search"]
    sft_rows.append({
        "task": "event_set_hierarchy_and_search",
        "provenance": esh["provenance"],
        "instruction": "How does MD_D_EVENT_SET_HIERARCHY expand V500_EVENT_SET_CANON and V500_EVENT_SET_CODE across 10 levels under 'ALL OCF EVENT SETS' and join V500_EVENT_SET_EXPLODE, and what column-count bug must be fixed in the orphan-event query?",
        "response": esh["md_d_event_set_hierarchy"],
    })
    sft_rows.append({
        "task": "event_set_hierarchy_and_search",
        "provenance": esh["provenance"],
        "instruction": "How does MD_D_EVENT_SET_SEARCH flatten the 10-level event-set hierarchy for keyword and code searching in Vertica/Snowflake, Oracle RDB SQL, and native Cerner CCL?",
        "response": esh["md_d_event_set_search"],
    })

    # 1D. Historical Reference Candidate 3: Selected Terminology Crosswalks & Regulatory Extract Specs
    t_cross = esc_cat["terminology_crosswalks"]
    sft_rows.append({
        "task": "resolve_terminology_or_regulatory_crosswalk",
        "provenance": t_cross["provenance"],
        "instruction": "How are Patient Primary Language values in Cerner Millennium Code Set 36 (PERSON.LANGUAGE_CD) mapped to HL7 PID-15 and Press Ganey survey language codes?",
        "response": {
            "code_set": 36,
            "code_set_name": "Language",
            "hl7_field": "PID-15",
            "v500_column": "PERSON.LANGUAGE_CD",
            "da2_field_name": "Person Language",
            "total_mappings": t_cross["code_set_36_language_crosswalk"]["total_mappings"],
            "active_survey_languages": [
                e for e in t_cross["code_set_36_language_crosswalk"]["entries"]
                if e["press_ganey_language_code"] > 0 or e["millennium_display"] in {"English", "Spanish", "Portuguese", "French", "German", "Italian"}
            ],
        },
    })
    sft_rows.append({
        "task": "resolve_terminology_or_regulatory_crosswalk",
        "provenance": t_cross["provenance"],
        "instruction": "Which UB-04 FL-15 Admission Source (Code Set 2) and UB-04 FL-17 Patient Discharge Status (Code Set 19) codes are eligible vs. ineligible for HCAHPS surveys in DA2 and Cerner Millennium?",
        "response": {
            "ub04_fl15_admission_source": t_cross["ub04_admission_source_code_set_2_hcahps"],
            "ub04_fl17_discharge_status": t_cross["ub04_discharge_status_code_set_19_hcahps"],
        },
    })
    sft_rows.append({
        "task": "resolve_terminology_or_regulatory_crosswalk",
        "provenance": t_cross["provenance"],
        "instruction": "Map standard inpatient regulatory claims extract fields and real-time Hospital Bed Capacity extract elements to their Discern Analytics 2.0 (DA2) and Millennium V500 sources.",
        "response": {
            "regulatory_ip_da2_field_specifications": t_cross["regulatory_ip_da2_field_specifications"],
            "hospital_bed_capacity_extract_specifications": t_cross["hospital_bed_capacity_extract_specifications"],
        },
    })


    # 1h. Complete 10-Domain DA2 Taxonomy & Sub-Entity Architecture (Live Harvest 2026-11-03)
    da2_tax = load_da2_domain_taxonomy()
    for dom in da2_tax.get("domains", []):
        sft_rows.append({
            "task": "resolve_da2_domain_taxonomy",
            "provenance": "live_da2_query_builder_harvest_2026-11-03",
            "instruction": (
                f"What sub-entities, root V500 anchor table, primary join key, and field prefixes are exposed by the "
                f"'{dom['domain_name']}' domain in Discern Analytics 2.0 (DA2)?"
            ),
            "response": dom,
        })


    # 1i. Task Family A: DA2 Qualification Filter Lists, Stratified 80/20 Train/Holdout Value Split, & Lookalike Field Disambiguation
    fvc = load_da2_filter_value_catalog()
    holdout_filter_rows: list[dict] = []

    for fl in fvc.get("qualification_filter_lists", []):
        sft_rows.append({
            "task": "resolve_da2_qualification_filter_list",
            "provenance": fvc["provenance"],
            "instruction": (
                f"What values appear in the Discern Analytics 2.0 (DA2) Qualification Filter List for "
                f"'{fl['da2_field_name']}' in domain '{fl['da2_domain']}' ({fl['v500_target']}), and how should it be disambiguated from related fields?"
            ),
            "response": fl,
        })
        clean_vals = [v for v in fl.get("filter_list_values", []) if v not in ("Null", "0")]
        live_sample = fl.get("live_code_value_sample", {})
        # Stratified 80/20 split: every 5th value (index % 5 == 4) is held out for zero-leakage evaluation
        train_vals = [v for i, v in enumerate(clean_vals) if i % 5 != 4]
        test_vals = [v for i, v in enumerate(clean_vals) if i % 5 == 4]

        # Multi-value training chunks (groups of 3) on train_vals only
        for i in range(0, min(len(train_vals), 18), 3):
            chunk = train_vals[i : i + 3]
            if not chunk:
                continue
            sft_rows.append({
                "task": "classify_value_to_da2_field",
                "provenance": fvc["provenance"],
                "instruction": (
                    f"Which Discern Analytics 2.0 (DA2) Qualification field and Millennium V500 column contain the "
                    f"filter list values {', '.join(repr(v) for v in chunk)}?"
                ),
                "response": {
                    "da2_domain": fl["da2_domain"],
                    "da2_subentity": fl["da2_subentity"],
                    "da2_field_name": fl["da2_field_name"],
                    "v500_target": fl["v500_target"],
                    "code_set": fl.get("code_set"),
                    "matched_values": chunk,
                    "matched_code_values": {v: live_sample[v] for v in chunk if v in live_sample},
                    "disambiguation_note": fl["disambiguation_note"],
                },
            })

        # Holdout single-value routing items (never included in sft_rows)
        for tv in test_vals[:3]:
            holdout_filter_rows.append({
                "eval_type": "holdout_filter_value_routing",
                "provenance": fvc["provenance"],
                "prompt": f"Route the clinical or financial filter value {tv!r} to its exact DA2 domain, sub-entity, field name, V500 column, and Code Set.",
                "held_out_value": tv,
                "expected_da2_domain": fl["da2_domain"],
                "expected_da2_subentity": fl["da2_subentity"],
                "expected_da2_field_name": fl["da2_field_name"],
                "expected_v500_target": fl["v500_target"],
                "expected_code_set": fl.get("code_set"),
                "expected_code_value": live_sample.get(tv),
            })

    # Lookalike Field Disambiguation SFT pairs (Task Family A.2)
    lookalike_pairs = [
        {
            "fields": ["Order Catalog Type (CS 6000)", "Order Activity Type (CS 106)", "Order Catalog (CS 200)"],
            "prompt": "Disambiguate the DA2 Orders fields 'Order Catalog Type', 'Order Activity Type', and 'Order Catalog' when filtering for Pharmacy/Laboratory/Radiology department orders vs. specific orderables like 'Resuscitation Status' or 'HYDROcodone'.",
            "resolution": {
                "Order Catalog Type": {"v500_target": "ORDERS.CATALOG_TYPE_CD", "code_set": 6000, "example_values": ["Pharmacy", "Laboratory", "Radiology", "Admit/Transfer/Discharge"]},
                "Order Activity Type": {"v500_target": "ORDERS.ACTIVITY_TYPE_CD", "code_set": 106, "example_values": ["Physical Therapy", "Occupational Therapy", "Speech Therapy", "Pharmacy"]},
                "Order Catalog": {"v500_target": "ORDERS.CATALOG_CD", "code_set": 200, "example_values": ["Resuscitation Status (2958523)", "Isolation Precautions (22276685)", "HYDROcodone (2760031)"]},
            },
        },
        {
            "fields": ["Enc Type (CS 71)", "EncType Class (CS 69)", "Enc Med Service (CS 34)"],
            "prompt": "Disambiguate 'Enc Type' (Code Set 71), 'EncType Class' (Code Set 69), and 'Enc Med Service' (Code Set 34) in CWC Encounter.",
            "resolution": {
                "Enc Type": {"v500_target": "ENCOUNTER.ENCNTR_TYPE_CD", "code_set": 71, "example_values": ["Inpatient (309308)", "Outpatient (309309)", "Recurring (309314)", "Preadmit (309313)"]},
                "EncType Class": {"v500_target": "ENCOUNTER.ENCNTR_TYPE_CLASS_CD", "code_set": 69, "example_values": ["Inpatient (391)", "Preadmit (395)", "Outpatient"]},
                "Enc Med Service": {"v500_target": "ENCOUNTER.MED_SERVICE_CD", "code_set": 34, "example_values": ["Physical Medicine & Rehab", "Internal Medicine", "Neurology"]},
            },
        },
        {
            "fields": ["Pay Trans Sub Type (CS 20549)", "Pay Trans Reason (CS 18935)", "Adj Trans Sub Type (CS 20549)", "Adj Trans Reason (CS 18935)"],
            "prompt": "Disambiguate 'Pay Trans Sub Type' vs. 'Pay Trans Reason' in CWF GS Payments and 'Adj Trans Sub Type' vs. 'Adj Trans Reason' in CWF GS Adjustments.",
            "resolution": {
                "Pay Trans Sub Type": {"da2_domain": "CWF GS Payments", "v500_target": "RC_D_TRANSACTION_ALIAS.TRANS_SUB_TYPE_CD", "example_values": ["ERA 835 Payment", "Lockbox Check", "Patient Copay"]},
                "Pay Trans Reason": {"da2_domain": "CWF GS Payments", "v500_target": "RC_D_TRANSACTION_ALIAS.TRANS_REASON_CD", "example_values": ["Primary Payer Remittance", "Secondary Payer Remittance"]},
                "Adj Trans Sub Type": {"da2_domain": "CWF GS Adjustments", "v500_target": "RC_D_TRANSACTION_ALIAS.TRANS_SUB_TYPE_CD", "example_values": ["Contractual Adjustment", "Bad Debt Write-Off", "Charity Care"]},
                "Adj Trans Reason": {"da2_domain": "CWF GS Adjustments", "v500_target": "RC_D_TRANSACTION_ALIAS.TRANS_REASON_CD", "example_values": ["CO-45 Charge Exceeds Fee Schedule", "CO-97 Payment Included in Allowance"]},
            },
        },
        {
            "fields": ["Person Race (CS 282)", "Per Info Sub Type (CS 356)", "Per Info Value"],
            "prompt": "Disambiguate top-level 'Person Race' (PERSON.RACE_CD, Code Set 282) from granular multi-valued CDC Race and Ethnicity fields ('Per Info Sub Type' and 'Per Info Value' on PERSON_INFO).",
            "resolution": {
                "Person Race": {"da2_domain": "CWC Person", "v500_target": "PERSON.RACE_CD", "code_set": 282, "note": "Single top-levelOMB race roll-up on PERSON (returns 'Multiple' when multi-racial)."},
                "Per Info Sub Type": {"da2_domain": "CWC Person", "v500_target": "PERSON_INFO.INFO_SUB_TYPE_CD", "code_set": 356, "example_values": ["CDC Ethnicity 1 (623071971)", "CDC Ethnicity 2 (623071989)", "CDC Ethnicity 3 (623071999)"]},
                "Per Info Value": {"da2_domain": "CWC Person", "v500_target": "PERSON_INFO.VALUE_CD", "note": "Stores the specific detailed race/ethnicity code for each CDC slot."},
            },
        },
        {
            "fields": ["CE Event (CS 72)", "Order Catalog (CS 200)", "DTA Mnemonic (CS 14003)"],
            "prompt": "When building a clinical audit in DA2, how do you decide whether a concept belongs in 'CE Event' (Code Set 72), 'Order Catalog' (Code Set 200), or 'DTA Mnemonic' (Code Set 14003)?",
            "resolution": {
                "CE Event": {"v500_target": "CLINICAL_EVENT.EVENT_CD", "code_set": 72, "rule": "Use for charted clinical results, flowsheets (IView), PowerForm answers, therapy minutes, lines/wounds, and Dynamic Documentation notes."},
                "Order Catalog": {"v500_target": "ORDERS.CATALOG_CD", "code_set": 200, "rule": "Use for provider/nursing orders placed in PowerChart (medications, labs, radiology, diet, isolation, resuscitation status)."},
                "DTA Mnemonic": {"v500_target": "DISCRETE_TASK_ASSAY.MNEMONIC", "code_set": 14003, "rule": "Use for the build-level Discrete Task Assay definition linked via PROFILE_TASK_R / DCP_INPUT_REF."},
            },
        },
    ]
    for lp in lookalike_pairs:
        sft_rows.append({
            "task": "disambiguate_lookalike_da2_fields",
            "provenance": "live_da2_overnight_harvest_2026-11-03",
            "instruction": lp["prompt"],
            "response": lp["resolution"],
        })

    ce_tax = fvc.get("ce_event_powerchart_taxonomy", {})
    for ce_cat in ce_tax.get("clinical_categories", []):
        sft_rows.append({
            "task": "resolve_ce_event_powerchart_values",
            "provenance": fvc["provenance"],
            "instruction": (
                f"Which 'CE Event' (CLINICAL_EVENT.EVENT_CD, Code Set 72) Filter List values, Event Set groupers, "
                f"and V500 payload columns represent '{ce_cat['category_name']}' in PowerChart and DA2?"
            ),
            "response": ce_cat,
        })

    # 1j. 16 HealtheIntent EDW/Analytics Feed Schemas & 4 Oracle Health Terminology/FHIR R4 References
    edw_cat = load_healtheintent_edw_catalog()
    for feed in edw_cat.get("feeds", []):
        sft_rows.append({
            "task": "interpret_healtheintent_edw_feed",
            "provenance": edw_cat["provenance"],
            "instruction": (
                f"Describe the Oracle Health HealtheIntent '{feed['feed_id']}' feed schema, its overview, "
                f"table count ({feed['schema_table_count']} tables), and its primary fact/dimension tables."
            ),
            "response": {
                "feed_id": feed["feed_id"],
                "source_file": feed["source_file"],
                "overview": feed["overview"],
                "schema_table_count": feed["schema_table_count"],
                "total_columns_parsed": feed["total_columns_parsed"],
                "files_summary": feed["files_summary"][:15],
                "tables_preview": [
                    {"table_name": t["table_name"], "column_count": t["column_count"]}
                    for t in feed["tables"][:25]
                ],
            },
        })
    for ref_key, ref_val in edw_cat.get("terminology_and_api_references", {}).items():
        sft_rows.append({
            "task": "resolve_oracle_health_terminology_and_fhir_codeset",
            "provenance": edw_cat["provenance"],
            "instruction": f"Explain the Oracle Health Millennium terminology and API specification for '{ref_key}'.",
            "response": ref_val,
        })

    # 1k. Task Families B, C, D, E: 36 Business View (<BV> XML) Schemas, Stratified 80/20 Canonical Queries Split,
    # Bidirectional Cross-Layer Translations (<cclquery> <-> V500 SQL <-> CCL .prg <-> .rptdesign), & 533 Coded Value Pairs
    rep_cat = load_da2_canonical_reports_catalog()
    for bv in rep_cat.get("business_views_xml_catalog", []):
        sft_rows.append({
            "task": "resolve_da2_business_view_xml_schema",
            "provenance": rep_cat["provenance"],
            "instruction": (
                f"Provide the Discern Analytics 2.0 (DA2) Business View (<BV>) XML schema, Logical View UUID "
                f"({bv['logical_view_uuid']}), Categories (<Cat>), and Column (<Col>) UUIDs for '{bv['logical_view_name']}'."
            ),
            "response": bv,
        })

    # Stratified 80/20 split of canonical queries: hold out 4 diverse production queries for zero-leakage testing
    holdout_query_ids = {
        "cwf_gs_adjustments_transaction_detail_extract",
        "bed_board_resuscitation_status_all_inpatients",
        "outpatient_scheduling_and_slot_utilization",
        "encounter_charges_with_ndc_and_duplicate_charge_audit",
    }
    holdout_query_rows: list[dict] = []

    for qr in rep_cat.get("canonical_queries_and_reports", []):
        if qr["id"] in holdout_query_ids:
            holdout_query_rows.append({
                "eval_type": "holdout_production_da2_query",
                "query_id": qr["id"],
                "provenance": rep_cat["provenance"],
                "da2_domain": qr["da2_domain"],
                "prompt": f"Build a Discern Analytics 2.0 (DA2) query and report for: {qr['title']} in domain {qr['da2_domain']}.",
                "ground_truth_selected_elements": qr["selected_elements"],
                "ground_truth_business_views": qr["business_views_used"],
                "ground_truth_v500_tables": qr["v500_and_rc_tables_joined"],
                "ground_truth_filter_highlights": qr.get("qualification_filter_highlights", {}),
                "ground_truth_cclquery_xml": qr.get("cclquery_xml"),
                "ground_truth_ejs_v500_sql": qr.get("ejs_v500_sql"),
            })
            continue

        # Training split queries -> generate bidirectional cross-layer SFT pairs (Task Family B)
        sft_rows.append({
            "task": "generate_canonical_da2_query_and_v500_sql",
            "provenance": rep_cat["provenance"],
            "instruction": (
                f"Generate the Discern Analytics 2.0 (DA2) query specification, <cclquery> XML, Business Views, "
                f"and Discern SQL Engine (EJS v3.17.3) V500 SQL for '{qr['title']}' in domain '{qr['da2_domain']}'."
            ),
            "response": qr,
        })
        if qr.get("cclquery_xml") and qr.get("ejs_v500_sql"):
            # Forward Compiler SFT: <cclquery> XML -> EJS V500 SQL
            sft_rows.append({
                "task": "compile_cclquery_xml_to_ejs_v500_sql",
                "provenance": rep_cat["provenance"],
                "instruction": (
                    f"Compile the following Discern Analytics 2.0 <cclquery> XML for '{qr['title']}' into the exact "
                    f"Oracle V500 SQL generated by Discern SQL Engine (EJS v3.17.3):**\n" + qr['cclquery_xml']
                ),
                "response": {
                    "query_id": qr["id"],
                    "da2_domain": qr["da2_domain"],
                    "v500_and_rc_tables_joined": qr["v500_and_rc_tables_joined"],
                    "ejs_v500_sql": qr["ejs_v500_sql"],
                },
            })
            # Decompiler SFT: EJS V500 SQL -> <cclquery> XML
            sft_rows.append({
                "task": "decompile_ejs_v500_sql_to_cclquery_xml",
                "provenance": rep_cat["provenance"],
                "instruction": (
                    f"Decompile the following Discern SQL Engine (EJS) V500 SQL query for '{qr['title']}' into a "
                    f"valid Discern Analytics 2.0 <cclquery version='1.0'> XML definition:\n" + qr['ejs_v500_sql']
                ),
                "response": {
                    "query_id": qr["id"],
                    "da2_domain": qr["da2_domain"],
                    "domain_uuid": qr["domain_uuid"],
                    "query_uuid": qr["query_uuid"],
                    "cclquery_xml": qr["cclquery_xml"],
                },
            })
        if qr.get("qualification_filter_highlights"):
            # Task Family D: Clinical Concept-to-Bundle Expansion
            sft_rows.append({
                "task": "expand_clinical_concept_to_code_value_bundle",
                "provenance": rep_cat["provenance"],
                "instruction": (
                    f"Which exact Qualification Filter-List DISPLAY strings and Millennium CODE_VALUE IDs (<value display='...' text='...'/>) "
                    f"are used to filter '{qr['title']}' in DA2?"
                ),
                "response": {
                    "query_id": qr["id"],
                    "title": qr["title"],
                    "da2_domain": qr["da2_domain"],
                    "qualification_filter_highlights": qr["qualification_filter_highlights"],
                },
            })

    # Task Family C: Compositional Join & Grain Architecture SFT rows
    for spec_key in ("cclquery_xml_specification", "rptdesign_xml_specification", "ejs_v3_17_3_sql_specification"):
        sft_rows.append({
            "task": "synthesize_da2_join_and_grain_architecture",
            "provenance": rep_cat["provenance"],
            "instruction": f"Explain the Discern Analytics 2.0 architectural specification and join/grain rules for '{spec_key}'.",
            "response": rep_cat[spec_key],
        })

    live_cv_fields = fvc.get("live_harvested_code_value_mappings", {}).get("fields", {})
    for fname, finfo in live_cv_fields.items():
        sft_rows.append({
            "task": "resolve_da2_cclquery_code_value_pairs",
            "provenance": "live_da2_overnight_harvest_2026-11-03",
            "instruction": (
                f"What are the exact <value display='...' text='...'/> CODE_VALUE mappings serialized in "
                f"DA2 <cclquery> XML when filtering on '{fname}'?"
            ),
            "response": {
                "da2_field_name": fname,
                "count": finfo["count"],
                "display_to_code_value": finfo["display_to_code_value"],
            },
        })

    # 2. Full End-to-End Benchmark & Evaluation Scenarios
    for sc in CANONICAL_BENCHMARK_SCENARIOS:
        req = sc["request"]
        ten = sc["tenancy"]
        prov = sc.get("provenance", "discern_intelligence_v1:core_benchmark")
        interp = interpreter.interpret(req, tenancy=ten)
        forms_res = forms_resolver.resolve(req, top_k=2)
        ccl_prog = ccl_compiler.compile(req, tenancy=ten, interpretation=interp)
        da2_bundle = da2_gen.generate(req, report_name=f"DA2_{sc['id']}", tenancy=ten)

        assert ccl_prog.verification.is_valid, f"CCL failed verification for {sc['id']}: {ccl_prog.verification.errors}"
        assert da2_bundle.verification.is_valid, f"DA2 bundle failed verification for {sc['id']}: {da2_bundle.verification.errors}"

        eval_rows.append({
            "scenario_id": sc["id"],
            "provenance": prov,
            "tenancy": ten,
            "request": req,
            "interpretation_summary": {
                "inferred_tenancy": interp.inferred_tenancy,
                "recommended_strategy": interp.recommended_strategy,
                "top_columns": [f"{c.table}.{c.column} ({c.ccl_type}) -> {c.da2_field_name}" for c in interp.matched_columns[:8]],
                "top_form_family": forms_res[0].form_family_name if forms_res else None,
                "payload_columns": [p["table_column"] for p in (forms_res[0].payload_columns if forms_res else [])],
                "guardrails_triggered": interp.guardrails_triggered,
            },
            "ccl_program_name": ccl_prog.program_name,
            "ccl_source": ccl_prog.source_code,
            "da2_queries": [q.to_dict() for q in da2_bundle.queries],
            "birt_join_key": da2_bundle.birt_join_key,
            "verification_invariants": da2_bundle.verification.checked_invariants,
        })

        # Add Contrastive DPO pairs teaching hard guardrails
        dpo_rows.append({
            "scenario_id": sc["id"],
            "provenance": prov,
            "prompt": f"[Tenancy: {ten}] {req}",
            "chosen": {
                "tenancy": interp.inferred_tenancy,
                "execution_strategy": da2_bundle.execution_strategy,
                "queries": [
                    {
                        "domain": q.da2_domain,
                        "elements": [e["da2_field_name"] for e in q.selected_elements],
                        "filters": q.filters,
                    }
                    for q in da2_bundle.queries
                ],
                "ccl_preview": ccl_prog.source_code[:600],
            },
            "rejected": {
                "antipattern": (
                    "Conflating XCW_* DA2 view names with raw V500 table names or using unvalidated 22-column orphan event-set SQL or mislabeled Pharmacy/Accounting SQL"
                    if "historical_reference_findings_2026-11-03" in prov
                    else "Unfiltered CLINICAL_EVENT versions + discharge-date filter on active census + raw SQL/CCL upload on multi-tenant Continuum/CommunityWorks"
                ),
                "broken_filters": [
                    "Using XCW_SCH_EV_ACTION as a raw V500 table name in CCL instead of SCH_EVENT_ACTION, or joining XCW_PHONE (via Organization) on PERSON_ID instead of ORG_ID",
                    "Omitting BOTTOM_LEVEL_EVENT_SET_CODE and BOTTOM_LEVEL_EVENT_SET_DISPLAY in MD_D_EVENT_SET_HIERARCHY orphan inserts (22 vs 24 columns)",
                    "Trusting a SQL file named 'Pharmacy - Intervention Education Detail' without verifying its SELECT body (which actually queries Accounting Charge Report revenue codes)",
                ]
                if "historical_reference_findings_2026-11-03" in prov
                else [
                    "Enc Disch Dt Tm >= Yesterday (drops all active inpatients whose discharge date is NULL)",
                    "Missing CE Valid Until Dt/Tm >= 12/31/2100 (double-counts amended and In-Error clinical events)",
                    "Unwrapped reportContext.getGlobalVariable() in beforeOpen (fails DA2 __bm_beforeOpen validator on Ctrl+S)",
                ],
            },
        })

    # 3. Expand DPO Contrastive Preference Pairs with Lookalike Field Confusion, Missing CODE_VALUE XML, & Cross-Domain Join Antipatterns
    for idx_lp, lp in enumerate(lookalike_pairs):
        dpo_rows.append({
            "scenario_id": f"lookalike_disambiguation_{idx_lp + 1}",
            "provenance": "live_da2_overnight_harvest_2026-11-03:dpo_lookalike_fields",
            "prompt": lp["prompt"],
            "chosen": lp["resolution"],
            "rejected": {
                "antipattern": f"Confusing sibling DA2 fields ({', '.join(lp['fields'])}) and placing filter values into the wrong Code Set or V500 column",
                "broken_filters": [
                    f"Filtering {lp['fields'][0]} using values that actually belong to {lp['fields'][-1]}",
                    "Omitting numeric CODE_VALUE in <value display='...' text='...'/> inside <cclquery> XML",
                ],
            },
        })

    for qr in rep_cat.get("canonical_queries_and_reports", []):
        dpo_rows.append({
            "scenario_id": f"canonical_query_guard_{qr['id']}",
            "provenance": "live_da2_overnight_harvest_2026-11-03:dpo_canonical_queries",
            "prompt": f"Generate a verified DA2 query specification for '{qr['title']}' in domain '{qr['da2_domain']}'.",
            "chosen": {
                "da2_domain": qr["da2_domain"],
                "domain_uuid": qr["domain_uuid"],
                "query_uuid": qr["query_uuid"],
                "business_views_used": qr["business_views_used"],
                "selected_elements": qr["selected_elements"],
                "qualification_filter_highlights": qr.get("qualification_filter_highlights", {}),
            },
            "rejected": {
                "antipattern": "Hallucinated DA2 Element UUIDs, flat Cartesian fan-out across 1-to-many child views, or missing inline_ind='1' / joinType='3' on cross-domain joins",
                "broken_filters": [
                    "Joining CWC Encounter and CWF GS Revenue/Payments without an inline subquery (inline_ind='1') or BIRT <joint-data-set>",
                    "Joining ENCNTR_ALIAS and PERSON_ALIAS directly instead of using CWC_ENC_FUNC.GET_FIN_NBR and CWC_PER_FUNC.GET_MRN",
                ],
            },
        })

    def write_jsonl(path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    write_jsonl(OUT_DIR / "sft_train_v1.jsonl", sft_rows)
    write_jsonl(OUT_DIR / "dpo_contrastive_v1.jsonl", dpo_rows)
    write_jsonl(OUT_DIR / "eval_frozen_v1.jsonl", eval_rows)
    write_jsonl(OUT_DIR / "eval_holdout_filter_values_v1.jsonl", holdout_filter_rows)
    write_jsonl(OUT_DIR / "eval_holdout_queries_v1.jsonl", holdout_query_rows)

    # Apply Phase 2 teacher distillation & verifier-gated <reasoning> enrichment
    import subprocess
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "distill_and_verify_phase2_corpus.py")], check=True)
    sft_final = [l for l in (OUT_DIR / "sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    dpo_final = [l for l in (OUT_DIR / "dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    counts = {
        "sft_train_v1": len(sft_final),
        "dpo_contrastive_v1": len(dpo_final),
        "eval_frozen_v1": len(eval_rows),
        "eval_holdout_filter_values_v1": len(holdout_filter_rows),
        "eval_holdout_queries_v1": len(holdout_query_rows),
    }
    print(f"Built training & evaluation corpora in {OUT_DIR}: {counts}")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="Verify generated corpora non-empty and valid")
    args = ap.parse_args()
    counts = build_corpora()
    if args.verify:
        assert counts["sft_train_v1"] >= 2300, f"Expected >=2300 SFT rows, got {counts['sft_train_v1']}"
        assert counts["dpo_contrastive_v1"] >= 40, f"Expected >=40 DPO rows, got {counts['dpo_contrastive_v1']}"
        assert counts["eval_frozen_v1"] == 14, f"Expected 14 frozen benchmark scenarios, got {counts['eval_frozen_v1']}"
        assert counts["eval_holdout_filter_values_v1"] >= 70, f"Expected >=70 holdout filter values, got {counts['eval_holdout_filter_values_v1']}"
        assert counts["eval_holdout_queries_v1"] == 4, f"Expected 4 stratified holdout queries, got {counts['eval_holdout_queries_v1']}"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
