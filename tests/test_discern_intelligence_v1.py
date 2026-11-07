"""Unit and integration tests for Discern Intelligence V1."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from discern_intelligence import (
    CCLCompiler,
    DA2ReportGenerator,
    FormsAndOutputResolver,
    MillenniumInterpreter,
)
from discern_intelligence.cli import main as cli_main


def test_interpreter_v500_columns_types_codesets_and_tenancy() -> None:
    interp = MillenniumInterpreter()
    res = interp.interpret(
        "Show active inpatient rehab census with MRN, FIN, nurse unit, attending physician, and verified therapy minutes"
    )
    assert res.inferred_tenancy == "continuum"
    assert res.recommended_strategy == "two_dataset_birt_join"

    tables_and_cols = {(c.table, c.column): c for c in res.matched_columns}
    assert ("ENCOUNTER", "ENCNTR_ID") in tables_and_cols
    assert "SURROGATE_KEY" in tables_and_cols[("ENCOUNTER", "ENCNTR_ID")].ccl_type
    assert ("ENCNTR_ALIAS", "ALIAS") in tables_and_cols
    assert tables_and_cols[("ENCNTR_ALIAS", "ALIAS")].code_set == 319
    assert ("CLINICAL_EVENT", "RESULT_VAL") in tables_and_cols
    assert ("CLINICAL_EVENT", "VALID_UNTIL_DT_TM") in tables_and_cols
    assert "DATETIME" in tables_and_cols[("CLINICAL_EVENT", "VALID_UNTIL_DT_TM")].ccl_type

    cs_ids = {cs["code_set"] for cs in res.matched_code_sets}
    assert {4, 8, 72, 220, 319, 333}.issubset(cs_ids)
    assert any("CE_VERSION_DEDUP" in g for g in res.guardrails_triggered)
    assert any("ACTIVE_CENSUS_NULL_DISCH" in g for g in res.guardrails_triggered)


@pytest.mark.parametrize(
    ("query", "expected_form_id", "expected_payload_substring"),
    [
        (
            "Where are PowerForm FIM and IRF-PAI functional assessment answers stored?",
            "powerforms_assessments",
            "CLINICAL_EVENT.RESULT_VAL",
        ),
        (
            "Which column holds InteractiveView vital signs and dynamic wound flowsheets?",
            "interactive_view_flowsheets",
            "CLINICAL_EVENT.RESULT_VAL",
        ),
        (
            "Which table and column stores 2G Intake and Output fluid balance volume in mL?",
            "intake_and_output_2g",
            "CE_INTAKE_OUTPUT_RESULT.IO_VOLUME",
        ),
        (
            "Where is Dynamic Documentation PowerNote and discharge summary narrative blob text stored?",
            "dynamic_documentation_notes_blobs",
            "CE_BLOB.BLOB_CONTENTS",
        ),
        (
            "Which table and column stores Order Entry Format prompt answers and order details?",
            "order_entry_formats_and_details",
            "ORDER_DETAIL.OE_FIELD_DISPLAY_VALUE",
        ),
        (
            "Where are CPDI scanned interactive eSignature consent forms tracked?",
            "cpdi_interactive_and_scanned_forms",
            "CDI_FORM_ACTIVITY.FORM_STATUS_CD",
        ),
        (
            "Which columns store BCMA barcode medication administration dose and wristband scan flags?",
            "mar_bcma_medication_administration",
            "CE_MED_RESULT.ADMIN_DOSAGE",
        ),
        (
            "Where are rehab PT/OT/SLP treatment minutes and billed CPT charge quantities stored?",
            "rehab_therapy_productivity_and_charges",
            "CHARGE.ITEM_QUANTITY",
        ),
        (
            "Which tables hold Patient Accounting Benefit Order total charges, payments, and adjustments?",
            "revcycle_patient_accounting_outputs",
            "BO_HP_RELTN.TOTAL_CHARGE_AMT",
        ),
        (
            "Which table tracks overdue nursing inbox tasks and task status reasons?",
            "clinical_tasks_and_overdue_inbox",
            "TASK_ACTIVITY.TASK_STATUS_CD",
        ),
        (
            "Where are appointment check-in, cancellation, and no-show reasons stored?",
            "scheduling_appointments_and_actions",
            "SCH_EVENT_ACTION.SCH_REASON_CD",
        ),
    ],
)
def test_forms_and_output_resolver_all_families(
    query: str, expected_form_id: str, expected_payload_substring: str
) -> None:
    resolver = FormsAndOutputResolver()
    matches = resolver.resolve(query, top_k=2)
    assert matches, f"No form resolution returned for: {query}"
    top = matches[0]
    assert top.form_id == expected_form_id
    payload_cols = " | ".join(p["table_column"] for p in top.payload_columns)
    assert expected_payload_substring in payload_cols
    assert top.definition_tables
    assert top.activity_tables
    assert top.da2_fields


def test_ccl_compiler_generates_valid_prg_with_uar_and_version_guards() -> None:
    compiler = CCLCompiler()
    prog = compiler.compile(
        "Active inpatient census with MRN, FIN, nurse unit, attending provider, and PowerForm clinical event results",
        program_name="cust_rehab_census_test",
        tenancy="standalone_rho",
    )
    assert prog.verification.is_valid, f"Verification errors: {prog.verification.errors}"
    assert prog.program_name == "cust_rehab_census_test"
    assert "DROP PROGRAM cust_rehab_census_test:dba GO" in prog.source_code
    assert 'uar_get_code_by("MEANING", 319, "FIN NBR")' in prog.source_code
    assert 'uar_get_code_by("MEANING", 8, "AUTH")' in prog.source_code
    assert 'ce.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")' in prog.source_code
    assert "e.disch_dt_tm = NULL" in prog.source_code


def test_da2_generator_single_and_two_dataset_rptdesign_xml_valid() -> None:
    gen = DA2ReportGenerator()

    # 1. Single-domain query
    b_single = gen.generate(
        "List patient full name, MRN, FIN, admit date, and nurse unit for admissions",
        report_name="DA2_Unit_Admissions",
        tenancy="communityworks",
    )
    assert b_single.verification.is_valid, f"Single-domain errors: {b_single.verification.errors}"
    assert b_single.execution_strategy == "single_domain_oda"
    assert len(b_single.queries) == 1
    root1 = ET.fromstring(b_single.rptdesign_xml)
    assert root1.attrib["version"] == "3.2.23"

    # 2. Cross-domain / multi-child two-dataset BIRT join query
    b_join = gen.generate(
        "Reconcile active inpatient rehab therapy clinical event minutes with billed CPT charges and Benefit Order total charges",
        report_name="DA2_Therapy_Charge_Recon",
        tenancy="continuum",
    )
    assert b_join.verification.is_valid, f"Two-dataset errors: {b_join.verification.errors}"
    assert b_join.execution_strategy == "two_dataset_birt_join"
    assert b_join.birt_join_key == "Enc Id"
    assert len(b_join.queries) == 2
    assert "<joint-data-set" in b_join.rptdesign_xml
    assert "<footer>" not in b_join.rptdesign_xml


def test_cli_subcommands_and_file_emission(tmp_path: Path) -> None:
    assert cli_main(["interpret", "Find PowerForm assessment scores and nurse unit"]) == 0
    assert cli_main(["forms", "Dynamic documentation discharge summary blob"]) == 0

    ccl_out = tmp_path / "test_out.prg"
    assert cli_main(["compile-ccl", "Extract BCMA medication administrations", "--out", str(ccl_out)]) == 0
    assert ccl_out.exists() and "SELECT INTO $OUTDEV" in ccl_out.read_text()

    da2_dir = tmp_path / "da2_bundle"
    assert cli_main(["generate-da2", "Active inpatient census with therapy charges", "--out-dir", str(da2_dir)]) == 0
    rpt_files = list(da2_dir.glob("*.rptdesign"))
    assert len(rpt_files) == 1
    ET.fromstring(rpt_files[0].read_text())


def test_zero_client_or_phi_strings_in_repo() -> None:
    from discern_intelligence.release_audit import audit_repository_tree, get_forbidden_substrings

    repo_root = Path(__file__).resolve().parent.parent
    forbidden = get_forbidden_substrings()
    assert len(forbidden) >= 40
    audit_res = audit_repository_tree(repo_root)
    assert audit_res["repo_tree_clean"], f"Forbidden strings found in repo: {audit_res['violations']}"



def test_da2_view_alias_join_catalog_48_direct_and_9_variants() -> None:
    from discern_intelligence.knowledge import (
    load_da2_domain_taxonomy,
        load_da2_view_alias_catalog,
        load_v500_tables,
        resolve_da2_view_or_alias,
    )

    cat = load_da2_view_alias_catalog()
    direct = cat["da2_views"]
    variants = cat["relational_path_variants"]
    assert len(direct) == 48
    assert len(variants) == 9
    assert cat["summary_counts"] == {
        "distinct_direct_da2_views": 48,
        "encounter_id_join_views": 24,
        "person_id_join_views": 23,
        "org_id_join_views": 1,
        "relational_via_variants": 9,
    }

    v500_names = set(load_v500_tables().keys())
    for row in direct:
        assert row["da2_view_name"].startswith("XCW_")
        assert row["da2_view_name"] != row["underlying_v500_table"]
        assert row["underlying_v500_table"] in v500_names, f"Missing backing V500 table: {row['underlying_v500_table']}"

    org_phone = [v for v in variants if v["da2_view_entry"] == "XCW_PHONE (via Organization)"][0]
    assert org_phone["documented_da2_join_key"] == "PERSON_ID"
    assert org_phone["effective_da2_join_key"] == "ORG_ID"

    resolved_ev = resolve_da2_view_or_alias("XCW_SCH_EV_ACTION")
    assert resolved_ev is not None
    assert resolved_ev["underlying_v500_table"] == "SCH_EVENT_ACTION"
    assert resolved_ev["da2_alias"] == "SCH_EV_ACTION"
    assert resolved_ev["effective_da2_join_key"] == "ENCOUNTER_ID"

    interp = MillenniumInterpreter()
    res = interp.interpret("Resolve XCW_PHONE (via Organization) and XCW_PFT_CHARGE join key and V500 table")
    view_names = {m.get("da2_view_entry") or m.get("da2_view_name") for m in res.matched_da2_views}
    assert "XCW_PHONE (via Organization)" in view_names
    assert "XCW_PFT_CHARGE" in view_names
    assert any("DA2_VIEW_ALIAS_RESOLUTION" in g for g in res.guardrails_triggered)


def test_event_set_hierarchy_and_search_sql_and_ccl() -> None:
    from discern_intelligence.knowledge import load_event_set_and_crosswalks

    ev_cat = load_event_set_and_crosswalks()
    hier = ev_cat["event_set_hierarchy_and_search"]["md_d_event_set_hierarchy"]
    search = ev_cat["event_set_hierarchy_and_search"]["md_d_event_set_search"]

    assert len(hier["columns"]) == 24
    assert "BOTTOM_LEVEL_EVENT_SET_CODE" in hier["canonical_hierarchy_sql"]
    assert "BOTTOM_LEVEL_EVENT_SET_CODE" in hier["validated_orphan_event_sql"]
    assert any("insert-ophans" in f for f in hier["validation_findings"])

    assert search["vertica_snowflake_sql"].count("UNION ALL") == 9
    assert search["oracle_rdb_sql"].count("UNION ALL") == 9
    assert "::INT" not in search["oracle_rdb_sql"]

    compiler = CCLCompiler()
    prog = compiler.compile(
        "Traverse 10-level V500_EVENT_SET_CANON hierarchy and V500_EVENT_SET_EXPLODE rooted at ALL OCF EVENT SETS",
        program_name="cust_evset_hier_test",
    )
    assert prog.verification.is_valid, f"CCL errors: {prog.verification.errors}"
    assert "V500_EVENT_SET_CANON" in prog.source_code
    assert "V500_EVENT_SET_EXPLODE" in prog.source_code
    assert "V500_EVENT_SET_CODE" in prog.source_code
    assert "ALL OCF EVENT SETS" in prog.source_code


def test_terminology_crosswalks_codeset_36_and_ub04_hcahps() -> None:
    from discern_intelligence.knowledge import (
    load_da2_domain_taxonomy,
        load_event_set_and_crosswalks,
        lookup_language_crosswalk,
    )

    ev_cat = load_event_set_and_crosswalks()
    cw = ev_cat["terminology_crosswalks"]
    lang_rows = cw["code_set_36_language_crosswalk"]["entries"]
    assert len(lang_rows) == 229

    # Verify normalized bottom rows (Friulian, Frisian, Asturian)
    friulian = lookup_language_crosswalk("Friulian")
    assert friulian and friulian[0]["millennium_code_set"] == 36
    assert friulian[0]["hl7_field"] == "PID-15"
    assert friulian[0]["press_ganey_language_code"] == 23

    portuguese = lookup_language_crosswalk("Portuguese")
    assert portuguese and portuguese[0]["press_ganey_language_code"] == 47

    admit_rows = cw["ub04_admission_source_code_set_2_hcahps"]["entries"]
    disch_rows = cw["ub04_discharge_status_code_set_19_hcahps"]["entries"]
    assert len(admit_rows) == 10
    assert len(disch_rows) == 39
    assert all(str(r.get("ub04_code")) != "30" for r in disch_rows)


def test_historical_review_audit_ledger_and_corpus_provenance() -> None:
    from discern_intelligence.knowledge import load_event_set_and_crosswalks

    ev_cat = load_event_set_and_crosswalks()
    ledger = ev_cat["historical_review_audit_ledger"]
    assert len(ledger["added"]) >= 5
    assert len(ledger["already_covered"]) >= 4
    assert len(ledger["rejected"]) >= 6

    repo_root = Path(__file__).resolve().parent.parent
    sft_rows = [
        json.loads(line)
        for line in (repo_root / "data/sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dpo_rows = [
        json.loads(line)
        for line in (repo_root / "data/dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    eval_rows = [
        json.loads(line)
        for line in (repo_root / "data/eval_frozen_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(sft_rows) >= 700
    assert len(dpo_rows) >= 14
    assert len(eval_rows) >= 14
    hist_sft = [r for r in sft_rows if "historical_reference_findings_2026-11-03" in r.get("provenance", "")]
    assert len(hist_sft) == 117


def test_live_da2_capture_ingester_xml_and_ejs_sql() -> None:
    import sys
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root / "scripts") not in sys.path:
        sys.path.insert(0, str(repo_root / "scripts"))
    from ingest_live_da2_capture import ingest_text

    # 1. Test .rptdesign XML capture ingestion
    gen = DA2ReportGenerator()
    bundle = gen.generate(
        "List patient full name, MRN, FIN, admit date, and nurse unit",
        report_name="DA2_Harvester_Smoke",
        tenancy="continuum",
    )
    xml_meta = ingest_text(bundle.rptdesign_xml, default_domain="CWC Encounter")
    assert xml_meta["capture_type"] == "rptdesign_xml"
    assert xml_meta["birt_version"] == "3.2.23"
    assert len(xml_meta["datasets"]) == 1
    assert xml_meta["datasets"][0]["element_count"] >= 5
    first_el = xml_meta["datasets"][0]["elements"][0]
    assert first_el["element_uuid"] is not None

    # 2. Test EJS SQL capture ingestion
    sample_ejs = """/* Discern SQL Engine (EJS) v. 3.15.1: Sample Domain Extract [11111111-2222-3333-4444-555555555555] */
SELECT DISTINCT
  CWC_ENCOUNTER.ENCNTR_ID AS enc_id,
  omf_get_cv_display(CWC_PERSON.LANGUAGE_CD) AS person_language,
  cclsql_utc_cnvt(CWC_ENCOUNTER.REG_DT_TM, 1, 54) AS reg_dt_tm
FROM CWC_ENCOUNTER CWC_ENCOUNTER
JOIN CWC_PERSON CWC_PERSON ON CWC_PERSON.PERSON_ID = CWC_ENCOUNTER.PERSON_ID
"""
    sql_meta = ingest_text(sample_ejs, default_domain="CWC Encounter")
    assert sql_meta["capture_type"] == "ejs_sql"
    assert sql_meta["ejs_version"] == "3.15.1"
    assert sql_meta["query_uuid"] == "11111111-2222-3333-4444-555555555555"
    assert sql_meta["table_aliases"]["CWC_ENCOUNTER"] == "CWC_ENCOUNTER"
    assert len(sql_meta["select_mappings"]) == 3
    lang_map = [m for m in sql_meta["select_mappings"] if m["sql_column_alias"] == "person_language"][0]
    assert lang_map["uses_omf_get_cv_display"] is True
    assert lang_map["resolved_v500_Change_refs"][0]["v500_table"] == "PERSON"
    assert lang_map["resolved_v500_Change_refs"][0]["v500_column"] == "LANGUAGE_CD"


def test_da2_10_domain_taxonomy_and_expanded_elements() -> None:
    from discern_intelligence.knowledge import load_da2_domain_taxonomy, load_da2_elements
    tax = load_da2_domain_taxonomy()
    domains = {d["domain_name"]: d for d in tax.get("domains", [])}
    assert len(domains) == 10
    expected_domains = {
        "CWC Encounter",
        "CWC Formulary",
        "CWC Organization",
        "CWC Person",
        "CWC Personnel",
        "CWF GL",
        "CWF GS Adjustments",
        "CWF GS Payments",
        "CWF GS Revenue",
        "CWC Scheduling Resource",
    }
    assert set(domains.keys()) == expected_domains
    assert "Med Product" in domains["CWC Formulary"]["subentities"]
    assert "Revenue" in domains["CWF GS Revenue"]["subentities"]
    assert "Adjustment" in domains["CWF GS Adjustments"]["subentities"]
    assert "Payment" in domains["CWF GS Payments"]["subentities"]

    da2_elems = load_da2_elements()
    assert len(da2_elems) >= 420
    gl_name = next((e for e in da2_elems if e["da2_field_name"] == "GL Account Name"), None)
    assert gl_name is not None
    assert gl_name["element_uuid"] == "5ff1a983-0732-4fa9-85ef-ddbb809d62bd"

    interp = MillenniumInterpreter()
    form_res = interp.interpret("Audit pharmacy formulary med product AWP and acquisition cost with bill item CDM and price schedule")
    form_tables = {c.table for c in form_res.matched_columns}
    assert {"ITEM_DEFINITION", "MED_PRODUCT", "MED_COST_HX", "BILL_ITEM", "PRICE_SCHED"} <= form_tables

    esi_res = interp.interpret("Inspect inbound HL7 interface errors in ESI_LOG by contributor system and MSH_MSG_TYPE")
    esi_tables = {c.table for c in esi_res.matched_columns}
    assert {"CONTRIBUTOR_SYSTEM", "ESI_LOG"} <= esi_tables


def test_priority_domains_filter_value_catalog_and_ce_event_taxonomy() -> None:
    from discern_intelligence.knowledge import (
        load_da2_elements,
        load_da2_filter_value_catalog,
        load_healtheintent_edw_catalog,
        resolve_da2_filter_value,
    )

    fvc = load_da2_filter_value_catalog()
    assert fvc["priority_domains"] == [
        "CWC Person",
        "CWC Encounter",
        "CWC Personnel",
        "CWF GS Revenue",
        "CWF GS Payments",
        "CWF GS Adjustments",
    ]
    assert len(fvc["qualification_filter_lists"]) >= 35
    ce_tax = fvc["ce_event_powerchart_taxonomy"]
    assert ce_tax["code_set"] == 72
    assert len(ce_tax["clinical_categories"]) >= 7

    # Test resolving specific Filter List values to their exact DA2 fields
    swing_hits = resolve_da2_filter_value("Swing Bed")
    assert any(h["da2_field_name"] in ("Enc Type", "EncType Class") for h in swing_hits)

    era_hits = resolve_da2_filter_value("ERA 835")
    assert any(h["da2_domain"] == "CWF GS Payments" and h["da2_field_name"] == "Pay Trans Sub Type" for h in era_hits)

    carc_hits = resolve_da2_filter_value("CO-45")
    assert any(h["da2_domain"] == "CWF GS Adjustments" and h["da2_field_name"] == "Adj Trans Reason" for h in carc_hits)

    rev_hits = resolve_da2_filter_value("0420 - Physical Therapy")
    assert any(h["da2_field_name"] == "F Chg Rev Code" and h["code_set"] == 14002 for h in rev_hits)

    # Test resolving CE Event (Code Set 72) PowerChart values
    gg_hits = resolve_da2_filter_value("GG0130A1")
    assert any(h["match_type"] == "ce_event_powerchart_code_set_72" and h["da2_field_name"] == "CE Event" for h in gg_hits)

    braden_hits = resolve_da2_filter_value("Braden Scale Total Score")
    assert any(h["match_type"] == "ce_event_powerchart_code_set_72" and h["code_set"] == 72 for h in braden_hits)

    # Verify expanded DA2 element catalog (669 elements including No Join Required scalar functions)
    da2_elems = load_da2_elements()
    assert len(da2_elems) >= 660
    subentities = {e["da2_bus_view_subentity"] for e in da2_elems}
    assert {
        "Person Functions",
        "Encounter Functions",
        "Financial Charge Functions",
        "Bill Record Functions",
        "CE Med Identifier Functions",
        "Discrete Task Assay",
        "PowerNote Story",
        "Care Management",
        "Avoidable Days",
    } <= subentities

    # Verify HealtheIntent EDW & Oracle Health terminology catalog
    edw_cat = load_healtheintent_edw_catalog()
    assert edw_cat["total_feed_schemas_parsed"] == 16
    assert edw_cat["total_schema_tables_parsed"] >= 1580
    assert edw_cat["total_columns_parsed"] >= 38000
    assert "fhir_r4_proprietary_code_systems" in edw_cat["terminology_and_api_references"]
    assert "codevalues_api_v20240101" in edw_cat["terminology_and_api_references"]

    # Verify new interpreter guardrails
    interp = MillenniumInterpreter()
    res = interp.interpret("Build an inpatient rehab census with MRN, FIN, attending physician, and PowerForm FIM scores")
    assert any("NO_JOIN_REQUIRED_SCALAR_FUNCTIONS" in g for g in res.guardrails_triggered)
    assert any("CE_EVENT_FILTER_LIST_AND_DTA_RESOLUTION" in g for g in res.guardrails_triggered)


def test_overnight_da2_harvest_canonical_reports_bv_and_code_values() -> None:
    from discern_intelligence.knowledge import (
        load_da2_canonical_reports_catalog,
        load_da2_elements,
        load_da2_filter_value_catalog,
        resolve_da2_filter_value,
    )

    rep_cat = load_da2_canonical_reports_catalog()
    assert rep_cat["summary_counts"]["harvested_business_views_lv_count"] >= 58
    assert rep_cat["summary_counts"]["harvested_da2_elements_with_uuids_count"] >= 2450
    assert rep_cat["summary_counts"]["harvested_coded_filter_value_pairs_count"] >= 5500
    assert len(rep_cat["canonical_queries_and_reports"]) >= 20

    bv_by_name = {b["logical_view_name"]: b for b in rep_cat["business_views_xml_catalog"]}
    assert "CWC Encounter" in bv_by_name
    assert "CWC Person" in bv_by_name
    assert "CWC Personnel" in bv_by_name
    assert "CWC Clinical Event" in bv_by_name
    assert "CWC Orders" in bv_by_name
    assert "CWC Diagnosis" in bv_by_name
    assert "CWC Allergy" in bv_by_name
    assert "CWC Surgery" in bv_by_name
    assert "CWC Item Org Reltn" in bv_by_name
    assert "CWC Organization" in bv_by_name
    assert "CWF GL Trans Log" in bv_by_name
    assert "CWF RC F Revenue" in bv_by_name
    assert "CWF RC F Adjustment" in bv_by_name
    assert "CWF RC F Cash" in bv_by_name

    qr_by_id = {q["id"]: q for q in rep_cat["canonical_queries_and_reports"]}
    assert "cwf_gs_revenue_transaction_detail_extract" in qr_by_id
    assert "cwf_gs_payments_transaction_detail_extract" in qr_by_id
    assert "cwf_gs_adjustments_transaction_detail_extract" in qr_by_id
    assert "cross_domain_inline_subquery_inpatient_revenue" in qr_by_id
    assert "birt_rptdesign_discharge_disposition_by_facility" in qr_by_id
    assert "(+)" in qr_by_id["cross_domain_inline_subquery_inpatient_revenue"]["ejs_v500_sql"]

    fvc = load_da2_filter_value_catalog()
    assert len(fvc["qualification_filter_lists"]) >= 60
    live_cv = fvc["live_harvested_code_value_mappings"]
    assert live_cv["total_coded_value_pairs"] >= 5500
    assert live_cv["fields"]["CE Event"]["count"] >= 1450
    assert live_cv["fields"]["Order Catalog"]["count"] >= 950
    assert live_cv["fields"]["Person Language"]["count"] >= 235
    assert live_cv["fields"]["Order Activity Type"]["count"] >= 200
    assert live_cv["fields"]["TL Transaction Reason"]["count"] >= 200
    assert live_cv["fields"]["Prsnl Position"]["count"] >= 110
    assert live_cv["fields"]["Order Catalog Type"]["count"] >= 50

    res_hits = resolve_da2_filter_value("Resuscitation Status")
    assert any(h.get("match_type") == "live_cclquery_code_value_map" for h in res_hits)

    da2_elems = load_da2_elements()
    assert len(da2_elems) >= 2450


def test_deterministic_verifiers_and_zero_leakage_eval_harness() -> None:
    from discern_intelligence.eval_harness import (
        run_full_evaluation_suite,
        score_query_element_and_filter_f1,
        verify_cclquery_xml,
        verify_ejs_v500_sql,
    )

    # 1. Positive & Negative <cclquery> XML verification
    valid_cclquery = """<?xml version="1.0" encoding="UTF-8"?>
<cclquery version="1.0">
  <name>Test_Query</name>
  <query_list xCount="1">
    <query_list_item1 domain="a67e07c5-0244-4b28-a146-7523f6090016" inline_ind="0" name="Test_Query" uuid="34678cce-8583-49f2-bfd7-2626a58daaf0" version="2">
      <elements xCount="1">
        <element display="Enc FIN" generate="true" name="Enc FIN" seq="1" sort="0" uuid="a73734e5-28c6-4b74-a5b6-783bc168f274"/>
      </elements>
      <filters xCount="1">
        <filter group="0" joinType="0" mode="0" op="9" type="LIST" useIndex="0">
          <element display="Enc Type" name="Enc Type" uuid="531f3d16-672c-414b-9e69-ec07ef29453e"/>
          <values type="LIST" xCount="1">
            <value display="Inpatient" text="309308"/>
          </values>
        </filter>
      </filters>
    </query_list_item1>
  </query_list>
</cclquery>"""
    v_ok = verify_cclquery_xml(valid_cclquery)
    assert v_ok.is_valid, f"Expected valid cclquery XML, got errors: {v_ok.errors}"
    assert "FILTER_LIST_CODE_VALUE_NUMERIC" in v_ok.checked_invariants

    # Negative check: Filter List <value> missing numeric CODE_VALUE in text=""
    broken_cclquery = valid_cclquery.replace('text="309308"', 'text="Inpatient"')
    v_bad = verify_cclquery_xml(broken_cclquery)
    assert not v_bad.is_valid
    assert any("missing numeric CODE_VALUE" in e for e in v_bad.errors)

    # 2. Positive & Negative EJS V500 SQL verification
    valid_sql = """/* Discern SQL Engine (EJS) v. 3.17.3: Census [1b7977a7-7c9c-479c-b4ae-38748e707fc5] */
SELECT DISTINCT
  CWC_ENC_FUNC.GET_FIN_NBR(CWC_ENCOUNTER.ENCNTR_ID) AS Enc_FIN,
  omf_get_cv_display(CWC_ENCOUNTER.LOC_NURSE_UNIT_CD) AS Enc_Loc_Nurse_Unit
FROM V500.ENCOUNTER CWC_ENCOUNTER
WHERE CWC_ENCOUNTER.ENCNTR_STATUS_CD IN (854)
  AND CWC_ENCOUNTER.DISCH_DT_TM IS NULL"""
    s_ok = verify_ejs_v500_sql(valid_sql)
    assert s_ok.is_valid, f"Expected valid EJS SQL, got errors: {s_ok.errors}"

    broken_sql = valid_sql.replace("CWC_ENCOUNTER.DISCH_DT_TM IS NULL", "CWC_ENCOUNTER.DISCH_DT_TM >= SYSDATE - 1")
    s_bad = verify_ejs_v500_sql(broken_sql)
    assert not s_bad.is_valid
    assert any("drops active patients" in e for e in s_bad.errors)

    # 3. Element & Filter F1 scorer
    f1 = score_query_element_and_filter_f1(
        predicted_elements=["Enc FIN", "Person Name Full", "Adj Amount"],
        ground_truth_elements=["Enc FIN", "Person Name Full", "Adj Amount", "Adj Post Dt"],
        predicted_filter_fields=["Enc Type (Code Set 71)"],
        ground_truth_filter_fields=["Enc Type"],
    )
    assert f1["element_precision"] == 1.0
    assert f1["element_recall"] == 0.75
    assert f1["filter_field_f1"] == 1.0

    # 4. Full End-to-End Evaluation Suite & CLI `evaluate` subcommand
    rep = run_full_evaluation_suite()
    assert rep["zero_leakage_verification"]["zero_leakage_passed"] is True
    assert rep["holdout_filter_value_routing"]["acc_at_1"] >= 0.85
    assert rep["holdout_filter_value_routing"]["acc_at_3"] >= 0.95
    assert rep["holdout_filter_value_routing"]["code_value_exact_match_rate"] == 1.0
    assert rep["canonical_and_holdout_verifiers"]["cclquery_xml_pass_at_1"] == 1.0
    assert rep["canonical_and_holdout_verifiers"]["ejs_v500_sql_pass_at_1"] == 1.0
    assert rep["canonical_and_holdout_verifiers"]["holdout_mean_element_recall"] == 1.0
    assert rep["canonical_and_holdout_verifiers"]["dpo_preference_validity_rate"] == 1.0

    assert cli_main(["evaluate"]) == 0



def test_phase2_distillation_reasoning_traces_and_best_of_n() -> None:
    from discern_intelligence.eval_harness import (
        generate_reasoning_trace,
        run_full_evaluation_suite,
        verifier_guided_best_of_n,
    )
    from discern_intelligence.knowledge import resolve_da2_filter_value

    repo_root = Path(__file__).resolve().parent.parent
    sft_rows = [
        json.loads(line)
        for line in (repo_root / "data/sft_train_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dpo_rows = [
        json.loads(line)
        for line in (repo_root / "data/dpo_contrastive_v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(sft_rows) >= 3900
    assert len(dpo_rows) >= 150
    assert all("<reasoning>" in r.get("reasoning_trace", "") and "</reasoning>" in r.get("reasoning_trace", "") for r in sft_rows)
    assert all("<reasoning>" in r["chosen"].get("reasoning_trace", "") for r in dpo_rows)

    # Disambiguation between Person Language (CS 36) and Person Race (CS 282) for "Japanese"
    lang_hits = resolve_da2_filter_value("Japanese", domain="CWC Person", subentity="Person", code_set=36)
    assert lang_hits[0]["da2_field_name"] == "Person Language"
    race_hits = resolve_da2_filter_value("Japanese", domain="CWC Person", subentity="Person", code_set=282)
    assert race_hits[0]["da2_field_name"] == "Person Race"

    # Full evaluation suite post-Phase-2 targets
    rep = run_full_evaluation_suite()
    assert rep["zero_leakage_verification"]["zero_leakage_passed"] is True
    assert rep["holdout_filter_value_routing"]["unconditioned_acc_at_1"] >= 0.89
    assert rep["holdout_filter_value_routing"]["acc_at_1"] == 1.0
    assert rep["holdout_filter_value_routing"]["acc_at_3"] == 1.0
    assert rep["holdout_filter_value_routing"]["code_value_exact_match_rate"] == 1.0
    assert rep["canonical_and_holdout_verifiers"]["holdout_mean_element_recall"] == 1.0
    assert rep["canonical_and_holdout_verifiers"]["holdout_mean_element_f1"] == 1.0

    # Verifier-Guided Best-of-N (N=4) & <reasoning> trace emission
    r_trace = generate_reasoning_trace("Active inpatient rehab census with MRN, FIN, nurse unit, and PowerForm scores")
    assert "<reasoning>" in r_trace and "1. Tenancy" in r_trace and "6. Mandatory" in r_trace

    bon = verifier_guided_best_of_n(
        "Reconcile active inpatient rehab therapy clinical event minutes with billed CPT charges",
        n=4,
    )
    assert bon["n_candidates_evaluated"] == 4
    assert bon["all_candidates_verified_count"] == 4
    assert bon["bundle"].verification.is_valid
    assert bon["ccl_program"].verification.is_valid
    assert "<reasoning>" in bon["reasoning_trace"]

    assert cli_main(["generate-da2", "Active inpatient census with MRN and FIN", "--best-of-n", "4", "--reasoning"]) == 0



def test_phase5_apollo_neural_compiler_and_verifier_fallback() -> None:
    from discern_intelligence import ApolloNeuralCompiler

    # Test deterministic verifier fallback when offline (safe inside isolated CI containers)
    nc = ApolloNeuralCompiler(endpoint_url="http://127.0.0.1:9", ssh_host="offline-ci-host.invalid")
    h = nc.health(timeout=0.2)
    assert h["status"] in ("ok", "offline_fallback")

    res = nc.compile_with_verifier_fallback(
        "Build an active inpatient rehab census with MRN, FIN, nurse unit, attending provider, and PowerForm scores",
        n=4,
    )
    assert res["model"] == "endexis-qwen3-0.6b-dpo-merged"
    assert res["n_candidates_evaluated"] == 4
    assert res["all_candidates_verified_count"] == 4
    assert res["bundle"].verification.is_valid
    assert res["ccl_program"].verification.is_valid
    assert "<reasoning>" in res["verified_reasoning_trace"]


def test_standalone_cclquery_xml_and_ejs_v500_sql_generation(tmp_path: Path) -> None:
    from discern_intelligence.da2_generator import DA2ReportGenerator
    from discern_intelligence.eval_harness import verify_cclquery_xml, verify_ejs_v500_sql

    gen = DA2ReportGenerator()
    bundle = gen.generate(
        "Build an active inpatient rehab census with MRN, FIN, nurse unit, attending provider, and PowerForm therapy minutes",
        report_name="Active_Rehab_Census",
    )
    assert bundle.verification.is_valid
    assert len(bundle.queries) >= 1
    for q in bundle.queries:
        assert q.cclquery_xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert '<cclquery version="1.0">' in q.cclquery_xml
        xml_ver = verify_cclquery_xml(q.cclquery_xml)
        assert xml_ver.is_valid, f"cclquery_xml errors: {xml_ver.errors}"

        assert "/* Discern SQL Engine (EJS) v. 3.17.3:" in q.ejs_v500_sql
        sql_ver = verify_ejs_v500_sql(q.ejs_v500_sql)
        assert sql_ver.is_valid, f"ejs_v500_sql errors: {sql_ver.errors}"

    out_dir = tmp_path / "da2_out"
    assert cli_main([
        "generate-da2",
        "Active inpatient census with MRN, FIN, nurse unit, and clinical event vitals",
        "--name",
        "Inpatient_Vitals_Audit",
        "--out-dir",
        str(out_dir),
    ]) == 0
    assert (out_dir / "Inpatient_Vitals_Audit.rptdesign").exists()
    assert (out_dir / "Inpatient_Vitals_Audit.prg").exists()
    assert (out_dir / "Inpatient_Vitals_Audit_Query_cclquery.xml").exists()
    assert (out_dir / "Inpatient_Vitals_Audit_Query_ejs_v500.sql").exists()

    xml_out = tmp_path / "standalone_query.xml"
    sql_out = tmp_path / "standalone_query.sql"
    assert cli_main([
        "generate-query",
        "Active inpatient census with MRN, FIN, nurse unit, and clinical event vitals",
        "--name",
        "Standalone_Vitals_Query",
        "--out-xml",
        str(xml_out),
        "--out-sql",
        str(sql_out),
    ]) == 0
    assert verify_cclquery_xml(xml_out.read_text(encoding="utf-8")).is_valid
    assert verify_ejs_v500_sql(sql_out.read_text(encoding="utf-8")).is_valid


def test_cssrs_da2_inline_subqueries_and_ce_event_spec() -> None:
    import xml.etree.ElementTree as ET
    from discern_intelligence.da2_generator import DA2ReportGenerator
    gen = DA2ReportGenerator()
    bundle = gen.generate(
        "Show patients with the CSSRS adult form completed and their responses",
        report_name="CSSRS_Adult_Completed_Responses",
    )
    assert len(bundle.queries) == 1
    q = bundle.queries[0]
    root = ET.fromstring(q.daquery_xml)
    ql = root.find("query_list")
    assert ql is not None
    assert ql.attrib.get("xCount") == "7"
    items = list(ql)
    assert len(items) == 7
    assert items[0].attrib.get("name") == "Query1"
    assert items[0].attrib.get("inline_ind") == "0"
    for sub_item in items[1:]:
        assert sub_item.attrib.get("inline_ind") == "1"
        assert sub_item.find("output/elements").attrib.get("xCount") == "3"
        assert sub_item.attrib.get("usegroupby") == "0"
        assert sub_item.attrib.get("useorderby") == "0"
        assert "CE Event" in ET.tostring(sub_item.find("qualification"), encoding="unicode")
    q1_out = ET.tostring(items[0].find("output"), encoding="unicode")
    assert 'joinParent="WishD"' in q1_out
    assert 'joinParent="LevelRisk"' in q1_out
    assert 'query="' not in q1_out
    q1_qual = ET.tostring(items[0].find("qualification"), encoding="unicode")
    assert "CSSRS Since Last Ask Suicidal Thoughts" in q1_qual
    assert 'joinType="3"' in q1_qual
    assert 'joinParent="WishD"' in q1_qual
    assert 'name="WishD:Enc Id"' in q1_qual
    assert 'uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"' in q1_qual
    ET.fromstring(bundle.rptdesign_xml)
    assert "WishD:CE Result Value" in bundle.rptdesign_xml
    assert "LevelRisk:CE Result Value" in bundle.rptdesign_xml

    assert bundle.verification.is_valid, f"Expected valid bundle: {bundle.verification.errors}"
    assert any("DA2_OWNER_GROUP_DISPLAY_SET" in inv for inv in bundle.verification.checked_invariants)
    assert any("DA2_INLINE_JOINPARENT_SYNTAX" in inv for inv in bundle.verification.checked_invariants)
    assert any("DA2_SINGLE_TABLE_OUTER_JOIN_ORA01416_SAFE" in inv for inv in bundle.verification.checked_invariants)
    assert any("DA2_INLINE_SUBQUERY_DATE_PUSHDOWN_AND_DEDUP" in inv for inv in bundle.verification.checked_invariants)

    # Verify deterministic rejection when CE Parent Event Id outer join is used on IView inline subqueries
    broken_q = gen.generate(
        "Show patients with the CSSRS adult form completed and their responses",
        report_name="CSSRS_Broken_Test",
    ).queries[0]
    import dataclasses
    broken_q = dataclasses.replace(
        broken_q,
        daquery_xml=broken_q.daquery_xml.replace(
            '<element name="Enc Id" type="1" uuid="5ef4381c-6344-43c4-81b5-2c18cd6bcb70"/>',
            '<element name="CE Parent Event Id" type="1" uuid="8c750217-756f-4276-8888-17d7cc0408ae"/>',
        ),
    )
    interp = gen._interpreter.interpret("Show patients with the CSSRS adult form completed and their responses")
    bad_ver = gen.verify_da2_bundle(bundle.rptdesign_xml, [broken_q], interp, is_active_census=False)
    assert not bad_ver.is_valid
    assert any("self-parented" in err for err in bad_ver.errors)


def test_birt_442_advanced_report_designer_features_and_corpus() -> None:
    """Verifies that DA2ReportGenerator emits and validates all 6 advanced BIRT 4.4.2 Report Designer capabilities."""
    from discern_intelligence.da2_generator import DA2ReportGenerator
    gen = DA2ReportGenerator()
    bundle = gen.generate(
        "Show patients with the CSSRS adult form completed and their responses",
        report_name="CSSRS_Adult_Completed_Responses",
    )
    assert bundle.verification.is_valid
    invs = " ".join(bundle.verification.checked_invariants)
    for expected in (
        "BIRT_442_PARAMETERS_SLOT_ORDER",
        "BIRT_442_COMPUTED_COLUMNS_SYNCED",
        "BIRT_442_DATETIME_AND_NUMBER_FORMATS",
        "BIRT_442_GROUP_AND_AGGREGATION_VALID",
        "BIRT_442_FILTER_AND_SORT_VALID",
        "BIRT_442_HIGHLIGHT_RULES_VALID",
    ):
        assert expected in invs, f"Missing expected invariant {expected}"
    rpt_xml = bundle.rptdesign_xml
    assert '<scalar-parameter name="p_NurseUnit"' in rpt_xml
    assert '<list-property name="computedColumns">' in rpt_xml
    assert '<structure name="dateTimeFormat">' in rpt_xml
    assert '<property name="aggregateFunction">COUNTDISTINCT</property>' in rpt_xml
    assert '<group id="' in rpt_xml
    assert '<list-property name="highlightRules">' in rpt_xml


def test_four_complex_clinical_and_financial_bundles_and_deprecate_uuid_guard() -> None:
    """Verifies all 4 complex clinical/financial .daquery + .dareport bundles and deprecated UUID guard."""
    import dataclasses
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    from discern_intelligence.da2_generator import DA2ReportGenerator, DEPRECATED_CWC_UUIDS, LIVE_CWC_ELEMENT_MAP

    assert len(LIVE_CWC_ELEMENT_MAP) >= 250
    assert LIVE_CWC_ELEMENT_MAP["Order Id"][1] == "0081bf6a-b747-49ad-be49-e193d5b3bc93"
    assert LIVE_CWC_ELEMENT_MAP["CE Result Units"][1] == "3d18ca87-7efd-487a-9121-0e5072f25f7a"
    assert LIVE_CWC_ELEMENT_MAP["Chg Service Dt/Tm"][1] == "9452e25e-98ec-4820-b40c-b95fbc94935e"
    assert LIVE_CWC_ELEMENT_MAP["Chg Item Quantity"][1] == "9c7440d4-51a5-4dd1-ac0c-a8bf799fb074"
    assert LIVE_CWC_ELEMENT_MAP["F Chg Status"][1] == "30aadfd4-ba16-4e13-a56d-b0de02c4882b"
    assert "a2ff2540-fa94-41bf-a0eb-69771900201f" in DEPRECATED_CWC_UUIDS

    gen = DA2ReportGenerator()
    scenarios = [
        ("CSSRS_Adult_Completed_Responses", "Generate a DA2 report and query bundle for C-SSRS Adult Completed Responses across inpatient units."),
        ("Rehab_Therapy_Minutes_By_Discipline", "Generate a DA2 report and query bundle for Rehab PT/OT/SLP Therapy Minutes by Discipline."),
        ("Active_Inpatient_Census_Risk_Indicators", "Generate a DA2 report and query bundle for Active Inpatient Census with Wound, Catheter, and Fall Risk Indicators."),
        ("Revenue_Charge_Reconciliation", "Generate a DA2 report and query bundle for Revenue/Charge Reconciliation across inpatient and outpatient encounters."),
        ("Provider_Consult_And_Progress_Notes_12Mo", "Generate a DA2 report and query bundle for the number of initial consult notes and progress notes written by consulting physicians over the last 12 months."),
    ]
    for r_name, req in scenarios:
        bundle = gen.generate(req, report_name=r_name)
        assert bundle.verification.is_valid, f"{r_name} errors: {bundle.verification.errors}"
        invs = " ".join(bundle.verification.checked_invariants)
        for expected in (
            "DA2_OWNER_GROUP_DISPLAY_SET",
            "DA2_CWC_CANONICAL_UUID_NO_DEPRECATE_COLLISION",
            "BIRT_442_PARAMETERS_SLOT_ORDER",
            "BIRT_442_COMPUTED_COLUMNS_SYNCED",
            "BIRT_442_DATETIME_AND_NUMBER_FORMATS",
            "BIRT_442_GROUP_AND_AGGREGATION_VALID",
            "BIRT_442_FILTER_AND_SORT_VALID",
            "BIRT_442_HIGHLIGHT_RULES_VALID",
        ):
            assert expected in invs, f"{r_name} missing {expected}"
        assert "2 table <group> block(s)" in invs
        assert "<property name=\"aggregateFunction\">COUNTDISTINCT</property>" in bundle.rptdesign_xml
        assert "<property name=\"aggregateFunction\">SUM</property>" in bundle.rptdesign_xml
        assert "<property name=\"aggregateFunction\">AVE</property>" in bundle.rptdesign_xml
        assert "BirtDateTime.diffDay" in bundle.rptdesign_xml
        assert "BirtMath.round" in bundle.rptdesign_xml

        zbytes = gen.build_dareport_zip_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
            names = set(zf.namelist())
            assert f"{r_name}.rptdesign" in names
            assert f"{r_name}.rpt" in names
            assert f"{bundle.queries[0].query_name}.daquery" in names
            rpt_root = ET.fromstring(zf.read(f"{r_name}.rpt"))
            assert bool(rpt_root.find("report").attrib.get("owner_group_display"))

    # Negative check: deprecated CWF Encounter UUID must be rejected by verify_da2_bundle
    rehab_bundle = gen.generate(scenarios[1][1], report_name=scenarios[1][0])
    bad_q = dataclasses.replace(
        rehab_bundle.queries[0],
        daquery_xml=rehab_bundle.queries[0].daquery_xml.replace(
            "5ef4381c-6344-43c4-81b5-2c18cd6bcb70",
            "a2ff2540-fa94-41bf-a0eb-69771900201f",
        ),
    )
    interp = gen._interpreter.interpret(scenarios[1][1])
    bad_ver = gen.verify_da2_bundle(rehab_bundle.rptdesign_xml, [bad_q], interp, is_active_census=False)
    assert not bad_ver.is_valid
    assert any("deprecated" in err.lower() for err in bad_ver.errors)


def test_tenant_agnostic_owner_group_and_custom_tenant_config(tmp_path: Path) -> None:
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    from discern_intelligence.da2_generator import DA2ReportGenerator
    from discern_intelligence.schemas import DEFAULT_OWNER_GROUP_DISPLAY, TenantConfig

    assert DEFAULT_OWNER_GROUP_DISPLAY == "TENANT_OWNER_GROUP"
    gen = DA2ReportGenerator()

    # 1. Default bundle uses TENANT_OWNER_GROUP and has zero Logical Domain ID filters
    b_default = gen.generate(
        "Show patients with the CSSRS adult form completed and their responses",
        report_name="CSSRS_Tenant_Default",
    )
    assert b_default.verification.is_valid
    assert 'owner_group_display="TENANT_OWNER_GROUP"' in b_default.queries[0].daquery_xml
    assert "Logical Domain ID" not in b_default.queries[0].daquery_xml

    # 2. Custom TenantConfig via CLI --tenant-config and --owner-group
    cfg_file = tmp_path / "tenant_profile.json"
    cfg_file.write_text(
        json.dumps({
            "owner_group_display": "METRO_HEALTH_ANALYTICS",
            "tenancy": "communityworks",
            "facility_display": "Metro University Hospital",
            "facility_code_value": "88001122",
        }),
        encoding="utf-8",
    )
    out_dir = tmp_path / "custom_tenant_out"
    assert cli_main([
        "generate-da2",
        "Active inpatient encounter census with FIN, MRN, and nurse unit",
        "--name",
        "Metro_Inpatient_Census",
        "--tenant-config",
        str(cfg_file),
        "--out-dir",
        str(out_dir),
    ]) == 0
    daquery_txt = (out_dir / "Metro_Inpatient_Census_Query.daquery").read_text(encoding="utf-8")
    assert 'owner_group_display="METRO_HEALTH_ANALYTICS"' in daquery_txt
    with zipfile.ZipFile(out_dir / "Metro_Inpatient_Census.dareport") as zf:
        rpt_root = ET.fromstring(zf.read("Metro_Inpatient_Census.rpt"))
        assert rpt_root.find("report").attrib.get("owner_group_display") == "METRO_HEALTH_ANALYTICS"

    # 3. Natural-language owner group parsing
    nl_cfg = TenantConfig().resolve_for_request(
        "Generate an active inpatient census for owner group 'VALLEY_CLINICAL_DA2' at facility 'Valley Medical Center'"
    )
    assert nl_cfg.owner_group_display == "VALLEY_CLINICAL_DA2"
    assert nl_cfg.facility_display == "Valley Medical Center"


def test_release_audit_safetensors_and_canary_verification(tmp_path: Path) -> None:
    import struct
    from discern_intelligence.release_audit import (
        audit_safetensors_weights,
        build_behavioral_canary_prompts,
        get_forbidden_substrings,
        verify_canary_generations,
        verify_tenant_agnostic_invariants,
    )

    invariants = verify_tenant_agnostic_invariants()
    assert invariants["tenant_agnostic_passed"], f"Invariants failed: {invariants}"

    prompts = build_behavioral_canary_prompts()
    assert len(prompts) >= 15
    clean_gens = ["owner_group_display='TENANT_OWNER_GROUP', Enc Loc Nurse Unit='2 North Medical-Surgical Unit'" for _ in prompts]
    assert verify_canary_generations(clean_gens)["canary_audit_passed"]

    # Construct a valid clean .safetensors binary file and verify it passes
    clean_st = tmp_path / "clean_model.safetensors"
    header_json = json.dumps({
        "__metadata__": {"format": "pt", "model": "endexis-qwen3-0.6b-dpo-merged", "license": "Apache-2.0"},
        "model.embed_tokens.weight": {"dtype": "BF16", "shape": [2, 2], "data_offsets": [0, 8]},
    }).encode("utf-8")
    clean_st.write_bytes(struct.pack("<Q", len(header_json)) + header_json + b"\x00" * 8)
    clean_res = audit_safetensors_weights(clean_st)
    assert clean_res["safetensors_clean"], f"Expected clean safetensors: {clean_res}"

    # Construct a dirty .safetensors binary file containing a forbidden token and verify it is caught
    dirty_tok = get_forbidden_substrings()[0]
    dirty_st = tmp_path / "dirty_model.safetensors"
    dirty_st.write_bytes(struct.pack("<Q", len(header_json)) + header_json + dirty_tok.encode("ascii") + b"\x00" * 8)
    dirty_res = audit_safetensors_weights(dirty_st)
    assert not dirty_res["safetensors_clean"]


def test_phase3_endexis_toml_profile_and_code_set_220_discovery(tmp_path: Path) -> None:
    from discern_intelligence.cli import main as cli_main
    from discern_intelligence.schemas import TenantConfig

    toml_file = tmp_path / ".endexis.toml"
    assert cli_main([
        "init",
        "--out",
        str(toml_file),
        "--owner-group",
        "NORTHWEST_CLINICAL_DA2",
        "--tenancy",
        "communityworks",
        "--utc-timezone-index",
        "62",
        "--facility",
        "Northwest Regional Medical Center",
        "--facility-code-value",
        "77001100",
    ]) == 0
    loaded = TenantConfig.from_file(toml_file)
    assert loaded.owner_group_display == "NORTHWEST_CLINICAL_DA2"
    assert loaded.utc_timezone_index == 62
    assert loaded.facility_display == "Northwest Regional Medical Center"
    assert loaded.get_location_overrides("Enc Loc Facility")["Northwest Regional Medical Center"] == "77001100"

    # Test Code Set 220 CSV discovery via init-discover
    csv_export = tmp_path / "code_set_220.csv"
    csv_export.write_text(
        "CODE_SET,CODE_VALUE,CDF_MEANING,DISPLAY,ACTIVE_IND\n"
        "220,91001,FACILITY,Summit Medical Campus,1\n"
        "220,91002,BUILDING,Summit East Tower,1\n"
        "220,91003,NURSEUNIT,4 East Telemetry,1\n"
        "220,91004,ROOM,Room 412,1\n"
        "220,91005,BED,Bed B,1\n",
        encoding="utf-8",
    )
    discovered_toml = tmp_path / "discovered.endexis.toml"
    assert cli_main([
        "init-discover",
        str(csv_export),
        "--out",
        str(discovered_toml),
        "--owner-group",
        "SUMMIT_HEALTH_DA2",
        "--utc-timezone-index",
        "58",
    ]) == 0
    disc_cfg = TenantConfig.from_file(discovered_toml)
    assert disc_cfg.owner_group_display == "SUMMIT_HEALTH_DA2"
    assert disc_cfg.utc_timezone_index == 58
    assert disc_cfg.get_location_overrides("Enc Loc Facility")["Summit Medical Campus"] == "91001"
    assert disc_cfg.get_location_overrides("Enc Loc Building")["Summit East Tower"] == "91002"
    assert disc_cfg.get_location_overrides("Enc Loc Nurse Unit")["4 East Telemetry"] == "91003"
    assert disc_cfg.get_location_overrides("Enc Loc Room")["Room 412"] == "91004"
    assert disc_cfg.get_location_overrides("Enc Loc Bed")["Bed B"] == "91005"

    # Verify custom utc_timezone_index flows into generated EJS V500 SQL
    bundle = DA2ReportGenerator(tenant_config=disc_cfg).generate(
        "Active inpatient encounter census with FIN, MRN, and nurse unit",
        report_name="Summit_Census",
        tenant_config=disc_cfg,
    )
    assert "cclsql_utc_cnvt(CWC_ENCOUNTER.REG_DT_TM, 1, 58)" in bundle.queries[0].ejs_v500_sql


def test_phase4_sqlite_v500_execution_harness_and_expanded_subject_areas() -> None:
    from discern_intelligence.cli import main as cli_main
    from discern_intelligence.sql_execution_harness import (
        create_synthetic_v500_connection,
        execute_and_verify_ejs_sql,
    )

    gen = DA2ReportGenerator()
    conn = create_synthetic_v500_connection()
    try:
        requests_and_names = [
            ("Active inpatient encounter census with FIN, MRN, Nurse Unit, and Admit Date", "Census_Check"),
            ("C-SSRS adult completed suicide risk screening responses", "CSSRS_Screening_Check"),
            ("Rehab therapy minutes by discipline PT OT SLP", "Rehab_Minutes_Check"),
            ("Active Sepsis Lactate orders and lab results", "Sepsis_Orders_Check"),
            ("Patient account payments and charges by bill item", "Charges_Check"),
            ("Inpatient MAR medication administration dosage and start time", "MAR_Med_Admin_Check"),
            ("Perioperative surgical case start time and case number", "Periop_Surg_Case_Check"),
            ("Inpatient ICD-10 diagnosis priority and diagnosis display", "ICD10_Diag_Check"),
        ]
        for req_text, rpt_name in requests_and_names:
            bundle = gen.generate(req_text, report_name=rpt_name)
            assert bundle.verification.is_valid, f"Bundle verification failed for {rpt_name}: {bundle.verification.errors}"
            for q in bundle.queries:
                exec_res = execute_and_verify_ejs_sql(q.ejs_v500_sql, conn=conn)
                assert exec_res["executed"], f"SQLite execution failed for {q.query_name}: {exec_res['error']}"
                assert exec_res["non_empty"], f"Empty result set for {q.query_name}"
                assert exec_res["fanout_safe"], f"Fan-out detected for {q.query_name}: {exec_res}"
    finally:
        conn.close()

    assert cli_main([
        "verify-sql",
        "Active inpatient encounter census with FIN, MRN, Nurse Unit, and Admit Date",
    ]) == 0


def test_v2_adversarial_benchmark_and_precision_disambiguation() -> None:
    from discern_intelligence.eval_harness import evaluate_adversarial_v2_benchmark, verify_cclquery_xml, verify_ejs_v500_sql
    from discern_intelligence.knowledge import load_da2_elements, load_v500_tables, resolve_da2_filter_value
    from discern_intelligence.sql_execution_harness import run_adversarial_sql_edge_case_suite

    # 1. Verify catalog enrichments (Track C)
    elems = load_da2_elements()
    assert len(elems) == 2606
    assert all("v500_table" in e and "v500_column" in e and "daquery_type" in e and "recommended_filters" in e for e in elems)
    vt = load_v500_tables()
    assert len(vt) == 230
    assert any("polymorphic_bridges" in t for t in vt.values())

    # 2. Verify context-conditioned filter value resolution (Track B)
    lang_hits = resolve_da2_filter_value(
        "Japanese",
        query_intent="Filter patient spoken language requiring interpreter services for value Japanese",
    )
    assert lang_hits[0]["da2_field_name"] == "Person Language"
    assert lang_hits[0]["code_set"] == 36
    assert lang_hits[0]["matched_code_values"]["Japanese"] == "312739"

    ord_hits = resolve_da2_filter_value(
        "Canceled",
        query_intent="Exclude pharmacy medication orders where Order Status is Canceled",
    )
    assert ord_hits[0]["da2_field_name"] == "Order Status"
    assert ord_hits[0]["code_set"] == 6004
    assert ord_hits[0]["matched_code_values"]["Canceled"] == "2542"

    # 3. Verify SQLite V500 edge-case execution suite (Track D)
    edge_res = run_adversarial_sql_edge_case_suite()
    assert edge_res["pass_rate"] == 1.0, f"SQLite edge cases failed: {edge_res}"

    # 4. Verify zero-leakage Adversarial V2 Benchmark (Track A + B + D)
    adv_rep = evaluate_adversarial_v2_benchmark()
    assert adv_rep["total_scenarios"] == 20
    assert adv_rep["zero_leakage_passed"] is True
    assert adv_rep["column_precision"] == 1.0
    assert adv_rep["column_recall"] == 1.0
    assert adv_rep["column_f1"] == 1.0
    assert adv_rep["forbidden_column_bloat_rate"] == 0.0
    assert adv_rep["ten_domain_routing_accuracy"] == 1.0
    assert adv_rep["context_conditioned_filter_acc_at_1"] == 1.0
    assert adv_rep["negative_trap_rejection_rate"] == 1.0
    assert adv_rep["sqlite_v500_edge_case_pass_rate"] == 1.0
    assert adv_rep["overall_pass_rate"] == 1.0
