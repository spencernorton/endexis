"""
Phase 2 Verification Suite for Endexis v2.3.0 (`PIPELINE_VERSION = "endexis-v2.3-clean-r1"`):
- Clinical-vs-billing concept collision elimination (`Adj *`, `Prsnl Position`, `Pay *`, `Chg *`)
- Word-boundary & negated-clause precision (`discharged`, `dose adjustment`, `also` vs `LOS`, `disorder`, `for him`)
- Multi-turn negative & override refinements (`remove_columns`/`exclude_columns`, `remove_filters`/`exclude_filters`, `replace_filter_value`/`override_filters`)
- `_derive_report_name()` conversational & negation hygiene
- `LocalNeuralCompiler` Track-A raw IR scoring vs Track-B neuro-symbolic verifier gate
- OS-level `atime == mtime` non-peeking seal invariant on `data/eval_postfreeze_v3.jsonl`
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from discern_intelligence import (
    PIPELINE_VERSION,
    CCLCompiler,
    DA2ReportGenerator,
    LocalNeuralCompiler,
    MillenniumInterpreter,
    NeuralCompilationIR,
    RefinementSpec,
    __version__,
)
from discern_intelligence.eval_harness import (
    run_full_evaluation_suite,
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from discern_intelligence.sql_execution_harness import execute_and_verify_ejs_sql


def test_version_and_pipeline_constants() -> None:
    assert __version__ == "2.3.0"
    assert PIPELINE_VERSION == "endexis-v2.3-clean-r1"


def test_clinical_physician_provider_never_injects_billing_or_prsnl_position() -> None:
    interp = MillenniumInterpreter()
    gen = DA2ReportGenerator(interpreter=interp)

    clinical_prompts = [
        "Build an active inpatient census by attending physician showing patient name, MRN, FIN, nurse unit, room/bed, and admit date.",
        "Generate an ordering physician medication and lab order audit for active inpatients on ICU.",
        "Create a clinical provider handoff roster for currently admitted patients with attending provider and length of stay.",
        "Build a clinician flowsheet documentation audit for active inpatients.",
    ]
    forbidden_fields = {
        "Adj Post Prsnl",
        "Prsnl Position",
        "Adj Trans Sub Type",
        "Adj Trans Reason",
        "Adj Post Dt",
        "Adj Amount",
    }
    for prompt in clinical_prompts:
        res = interp.interpret(prompt)
        matched_da2 = {c.da2_field_name for c in res.matched_columns if c.da2_field_name}
        assert not (matched_da2 & forbidden_fields), f"Interpreter leaked {matched_da2 & forbidden_fields} on {prompt}"

        bundle = gen.generate(prompt)
        assert bundle.verification.is_valid, bundle.verification.errors
        q_fields = {e["da2_field_name"] for q in bundle.queries for e in q.selected_elements}
        assert not (q_fields & forbidden_fields), f"Bundle leaked {q_fields & forbidden_fields} on {prompt}"
        assert not any(f.startswith(("Adj ", "Pay ", "Chg ")) for f in q_fields), f"Billing leak {q_fields} on {prompt}"
        for q in bundle.queries:
            assert verify_cclquery_xml(q.cclquery_xml).is_valid
            assert verify_ejs_v500_sql(q.ejs_v500_sql).is_valid
            assert execute_and_verify_ejs_sql(q.ejs_v500_sql)['executed']
            assert "V500.CHARGE" not in q.ejs_v500_sql

    # Positive controls: genuine financial adjustment & genuine CWC Personnel position requests MUST retain recall
    fin_bundle = gen.generate(
        "Build a CWF GS Adjustments write-off transaction extract showing adjustment reason, adjustment amount, debit GL, credit GL, and Adj Post Prsnl."
    )
    assert fin_bundle.verification.is_valid
    fin_fields = {e["da2_field_name"] for q in fin_bundle.queries for e in q.selected_elements}
    assert "Adj Trans Reason" in fin_fields
    assert "Adj Amount" in fin_fields

    pos_res = interp.interpret(
        "Extract CWC Personnel credentialing role and Prsnl Position (Code Set 88) for active hospital staff."
    )
    assert any(c.da2_field_name == "Prsnl Position" or c.code_set == 88 for c in pos_res.matched_columns)


def test_word_boundary_and_negation_collision_traps() -> None:
    interp = MillenniumInterpreter()
    gen = DA2ReportGenerator(interpreter=interp)

    # 1. "discharged" must NEVER trigger CHARGE / Chg *
    disch_prompt = "List recently discharged patients with active diet orders and discharge disposition."
    disch_bundle = gen.generate(disch_prompt)
    assert disch_bundle.verification.is_valid
    disch_fields = {e["da2_field_name"] for q in disch_bundle.queries for e in q.selected_elements}
    assert not any(f.startswith("Chg ") for f in disch_fields)
    assert "V500.CHARGE" not in disch_bundle.queries[0].ejs_v500_sql

    # 2. Clinical "dose adjustment" must NEVER trigger CWF GS Adjustments
    dose_prompt = "Build an insulin sliding-scale dose adjustment and titration flowsheet audit for active inpatients."
    dose_bundle = gen.generate(dose_prompt)
    assert dose_bundle.verification.is_valid
    assert dose_bundle.queries[0].da2_domain == "CWC Encounter"
    dose_fields = {e["da2_field_name"] for e in dose_bundle.queries[0].selected_elements}
    assert not any(f.startswith("Adj ") for f in dose_fields)

    # 3. "also" / "closely" vs "LOS"
    also_unit_b = gen.generate("Build an active inpatient census showing patient name and FIN, and also closely monitor nurse unit.")
    assert "Enc LOS" not in {e["da2_field_name"] for e in also_unit_b.queries[0].selected_elements}
    also_los_b = gen.generate("Build an active inpatient census showing patient name and FIN, and also include LOS.")
    assert "Enc LOS" in {e["da2_field_name"] for e in also_los_b.queries[0].selected_elements}

    # 4. "borderline" / "disorder" must not trigger ORDERS
    disorder_res = interp.interpret("Show interactive view vital signs documented for patients with gait disorder and borderline blood pressure.")
    assert "ORDERS" not in {c.table for c in disorder_res.matched_columns}

    # 5. Pronoun "for him" must not trigger HIM coding report
    him_bundle = gen.generate("Build an active inpatient nursing assessment summary documented for him on the shift.")
    assert him_bundle.report_name != "HIM_Diagnosis_Extract_Report"

    # 6. Negated billing clause must not pollute clinical bundle or report name
    neg_prompt = (
        "Hey team, please build an active inpatient census showing patient name, FIN, room/bed, and attending physician "
        "without billing adjustment or duplicate charge columns — thanks!"
    )
    neg_bundle = gen.generate(neg_prompt)
    assert neg_bundle.report_name == "Active_Inpatient_Encounter_Census"
    assert neg_bundle.verification.is_valid
    neg_fields = {e["da2_field_name"] for e in neg_bundle.queries[0].selected_elements}
    assert "Enc Attending Phys" in neg_fields
    assert not any(f.startswith(("Adj ", "Chg ", "Pay ")) for f in neg_fields)


def test_multiturn_negative_and_override_refinements_end_to_end() -> None:
    gen = DA2ReportGenerator()

    # 1. Explicit dict API (remove_columns, remove_filters, replace_filter_value) — even dropping Enc Loc Nurse Unit!
    base_req = "Build an active inpatient census showing patient name, MRN, FIN, facility, nurse unit, room, and attending physician."
    refined_bundle = gen.generate(
        base_req,
        refinement={
            "remove_columns": ["Enc Loc Nurse Unit", "Enc Loc Room"],
            "remove_filters": ["Enc Status"],
            "replace_filter_value": {"Enc Type": ["Inpatient"], "Enc Loc Facility": ["Main Hospital"]},
        },
    )
    assert refined_bundle.verification.is_valid, refined_bundle.verification.errors
    q0 = refined_bundle.queries[0]
    sel_names = [e["da2_field_name"] for e in q0.selected_elements]
    assert "Enc Loc Nurse Unit" not in sel_names
    assert "Enc Loc Room" not in sel_names
    assert "Enc Loc Facility" in sel_names

    # Verify lockstep across <cclquery>, .daquery, EJS SQL, BIRT .rptdesign, and CCL .prg
    assert 'name="Enc Loc Nurse Unit"' not in q0.cclquery_xml
    assert 'name="Enc Loc Room"' not in q0.cclquery_xml
    assert 'name="Enc Status"' not in q0.cclquery_xml.split("<filters", 1)[1]
    assert 'name="Enc Type"' in q0.cclquery_xml and 'text="309308"' in q0.cclquery_xml
    assert "AS Enc_Loc_Nurse_Unit" not in q0.ejs_v500_sql
    assert "ENCNTR_STATUS_CD IN (854)" not in q0.ejs_v500_sql
    assert "CWC_ENCOUNTER.ENCNTR_TYPE_CD IN (309308)" in q0.ejs_v500_sql
    assert "CWC_ENCOUNTER.LOC_FACILITY_CD IN (220001)" in q0.ejs_v500_sql
    assert verify_cclquery_xml(q0.cclquery_xml).is_valid
    assert verify_ejs_v500_sql(q0.ejs_v500_sql).is_valid
    assert execute_and_verify_ejs_sql(q0.ejs_v500_sql)['executed']

    # Check BIRT .rptdesign XML does not reference excluded Enc Loc Nurse Unit in boundDataColumns or group header
    ET.fromstring(refined_bundle.rptdesign_xml)
    assert '<property name="resultSetColumn">Enc Loc Nurse Unit</property>' not in refined_bundle.rptdesign_xml
    assert "e.encntr_type_cd" in refined_bundle.equivalent_ccl.lower()
    assert CCLCompiler().compile(base_req, refinement={"remove_columns": ["Enc Loc Nurse Unit", "Enc Loc Room"], "remove_filters": ["Enc Status"], "replace_filter_value": {"Enc Type": ["Inpatient"], "Enc Loc Facility": ["Main Hospital"]}}).verification.is_valid

    # 2. Natural-language multi-turn [Follow-Up Refinement] format
    mt_prompt = (
        "[Prior Turn] Build a CWF GS Adjustments write-off transaction extract showing Enc FIN, Person Name Full, "
        "HP Financial Class, Adj Post Dt, Adj Amount, and Adj Trans Reason.\n"
        "[Prior Elements] Enc FIN, Person Name Full, Enc Loc Facility, Enc Admit Dt/Tm, Enc Discharge Dt/Tm, "
        "Adj Post Dt, Adj Amount, Adj Trans Sub Type, Adj Trans Reason, HP Financial Class\n"
        "[Follow-Up Refinement] Remove the financial class column, drop the adjustment reason filter, "
        "and change nursing unit filter to ICU and stepdown; keep only inpatient encounters."
    )
    mt_bundle = gen.generate(mt_prompt)
    assert mt_bundle.verification.is_valid, mt_bundle.verification.errors
    mt_q = mt_bundle.queries[0]
    mt_elems = [e["da2_field_name"] for e in mt_q.selected_elements]
    assert "HP Financial Class" not in mt_elems
    assert not any("adj trans reason" in f.lower() for f in mt_q.filters)
    assert any("Enc Loc Nurse Unit IN (ICU, stepdown)" in f for f in mt_q.filters)
    assert any("Enc Type IN (Inpatient)" in f for f in mt_q.filters)
    assert 'text="220003"' in mt_q.cclquery_xml and 'text="220004"' in mt_q.cclquery_xml
    assert 'text="309308"' in mt_q.cclquery_xml
    assert verify_cclquery_xml(mt_q.cclquery_xml).is_valid
    assert verify_ejs_v500_sql(mt_q.ejs_v500_sql).is_valid
    assert execute_and_verify_ejs_sql(mt_q.ejs_v500_sql)['executed']


def test_neural_compiler_track_a_raw_ir_and_track_b_verifier_gate() -> None:
    compiler = LocalNeuralCompiler(endpoint_url="http://127.0.0.1:59999")
    prompt = "Build an active inpatient census by attending physician showing patient name, MRN, FIN, nurse unit, and admit date."

    # 1. Clean NeuralCompilationIR completion passes Track A
    clean_completion = (
        "<reasoning>Clinical active inpatient census by attending physician in CWC Encounter; "
        "exclude billing adjustment fields.</reasoning>\n"
        "<output>"
        + json.dumps({
            "template_id": "multi_query_union_census_extract",
            "da2_domain": "CWC Encounter",
            "tenancy": "communityworks",
            "execution_strategy": "single_domain_oda",
            "selected_elements": [
                "Enc Id",
                "Enc Financial Num",
                "Person MRN",
                "Person Name Full Format",
                "Enc Loc Facility",
                "Enc Loc Nurse Unit",
                "Enc Attending Phys",
                "Enc Registration Dt/Tm",
                "Enc Discharge Dt/Tm",
            ],
            "exclude_columns": [],
            "exclude_filters": [],
            "override_filters": {},
        })
        + "</output>"
    )
    track_a_clean = compiler.compile_track_a_raw_ir(prompt, completion_text=clean_completion)
    assert track_a_clean["parsed_valid_ir"] is True
    assert track_a_clean["has_reasoning_trace"] is True
    assert track_a_clean["contamination_free"] is True
    assert track_a_clean["all_verifiers_passed"] is True

    # 2. Contaminated raw neural IR fails Track A honestly, while Track B gates/rescues to 100% clean output
    contaminated_completion = (
        "<reasoning>Adding physician and adjustment personnel fields.</reasoning>\n"
        "<output>"
        + json.dumps({
            "da2_domain": "CWC Encounter",
            "tenancy": "communityworks",
            "execution_strategy": "single_domain_oda",
            "selected_elements": [
                "Enc Id",
                "Person Name Full Format",
                "Enc Loc Nurse Unit",
                "Adj Post Prsnl",
                "Prsnl Position",
                "Enc Registration Dt/Tm",
                "Enc Discharge Dt/Tm",
            ],
        })
        + "</output>"
    )
    track_a_bad = compiler.compile_track_a_raw_ir(prompt, completion_text=contaminated_completion)
    assert track_a_bad["parsed_valid_ir"] is True
    assert track_a_bad["contamination_free"] is False
    assert track_a_bad["all_verifiers_passed"] is False
    assert any("Adj Post Prsnl" in v for v in track_a_bad["violations"])
    assert any("Prsnl Position" in v for v in track_a_bad["violations"])

    with patch.object(
        compiler,
        "query_neural_endpoint",
        return_value={
            "best_completion": contaminated_completion,
            "candidates": [contaminated_completion],
            "transport": "openai_compatible_gguf_http",
            "latency_ms": 112.4,
            "vram_allocated_mb": 0.0,
        },
    ):
        track_b = compiler.compile_with_verifier_fallback(prompt, n=1)
        assert track_b["model"] == "endexis-qwen3-0.6b-dpo-merged"
        assert track_b["bundle"].verification.is_valid
        tb_fields = {e["da2_field_name"] for q in track_b["bundle"].queries for e in q.selected_elements}
        assert "Adj Post Prsnl" not in tb_fields
        assert "Prsnl Position" not in tb_fields


def test_legacy_eval_suites_and_sealed_postfreeze_untouched() -> None:
    import hashlib
    import json

    repo_root = Path(__file__).resolve().parent.parent
    sealed = repo_root / "data/eval_postfreeze_v3.jsonl"
    assert sealed.stat().st_size == 2674388
    assert (
        hashlib.sha256(sealed.read_bytes()).hexdigest()
        == "a4a509115a4428dc3aa09393b06d0dd01a59636b35325c5bdae5e395d3080af3"
    )
    st_before = os.stat(sealed)

    suite = run_full_evaluation_suite()
    assert suite["zero_leakage_verification"]["zero_leakage_passed"] is True
    assert suite["holdout_filter_value_routing"]["acc_at_1"] == 1.0
    assert suite["canonical_and_holdout_verifiers"]["cclquery_xml_pass_at_1"] == 1.0
    assert suite["canonical_and_holdout_verifiers"]["ejs_v500_sql_pass_at_1"] == 1.0
    assert suite["canonical_and_holdout_verifiers"]["holdout_da2_generator_pass_at_1"] == 1.0
    assert suite["adversarial_v2_benchmark"]["overall_pass_rate"] == 1.0

    st_after = os.stat(sealed)
    assert st_after.st_mtime_ns == st_before.st_mtime_ns
    assert st_after.st_atime_ns == st_before.st_atime_ns
    phase4_rcpt = repo_root / "data/phase4_v2_3_sealed_qualification_receipt.json"
    if phase4_rcpt.exists():
        rcpt_data = json.loads(phase4_rcpt.read_text())
        assert rcpt_data["pre_unseal_integrity_proof"]["untouched_since_phase1_seal"] is True


def test_adversarial_sql_cohort_precision_and_strict_override_validation() -> None:
    import pytest

    gen = DA2ReportGenerator()
    base_req = "Build an active inpatient census showing patient name, MRN, FIN, nurse unit, and admit date."

    # 1. Stepdown override must emit ONLY 220004 (never inject unrequested ICU 220003)
    b_step = gen.generate(base_req, refinement={"replace_filter_value": {"Enc Loc Nurse Unit": ["Stepdown"]}})
    assert b_step.verification.is_valid
    assert "CWC_ENCOUNTER.LOC_NURSE_UNIT_CD IN (220004)" in b_step.queries[0].ejs_v500_sql
    assert "220003" not in b_step.queries[0].ejs_v500_sql

    # 2. Observation override must emit ONLY 309312 (never inject unrequested Inpatient 309308)
    b_obs = gen.generate(base_req, refinement={"replace_filter_value": {"Enc Type": ["Observation"]}})
    assert b_obs.verification.is_valid
    assert "CWC_ENCOUNTER.ENCNTR_TYPE_CD IN (309312)" in b_obs.queries[0].ejs_v500_sql
    assert "309308" not in b_obs.queries[0].ejs_v500_sql

    # 3. Inpatient Rehabilitation must match 72191245 only (first-match break, no duplicate 309308)
    b_rehab = gen.generate(base_req, refinement={"replace_filter_value": {"Enc Type": ["Inpatient Rehabilitation"]}})
    assert b_rehab.verification.is_valid
    assert "CWC_ENCOUNTER.ENCNTR_TYPE_CD IN (72191245)" in b_rehab.queries[0].ejs_v500_sql

    # 4. Unrecognized override value, empty list, or unsupported field raises ValueError
    with pytest.raises(ValueError):
        gen.generate(base_req, refinement={"replace_filter_value": {"Enc Loc Nurse Unit": ["Unknown Mars Unit 99"]}})
    with pytest.raises(ValueError):
        gen.generate(base_req, refinement={"replace_filter_value": {"Enc Loc Nurse Unit": []}})
    with pytest.raises(ValueError):
        gen.generate(base_req, refinement={"replace_filter_value": {"Bogus Filter Field": ["Foo"]}})


def test_adversarial_multiturn_filter_precedence_and_prior_filters() -> None:
    gen = DA2ReportGenerator()
    ccl = CCLCompiler()
    base_req = "Build an active inpatient census showing patient name, MRN, FIN, nurse unit, room, and admit date."

    # 1. prior_filters survive column removal
    b_prior = gen.generate(
        base_req,
        prior_context={"prior_filters": {"Enc Loc Nurse Unit": ["Stepdown"]}},
        refinement={"remove_columns": ["Enc Loc Room"]},
    )
    assert b_prior.verification.is_valid
    assert "CWC_ENCOUNTER.LOC_NURSE_UNIT_CD IN (220004)" in b_prior.queries[0].ejs_v500_sql
    assert 'e.loc_nurse_unit_cd), 3) IN ("Stepdown")' in b_prior.equivalent_ccl

    # 2. exclude_filters drops explicit value_filters; override_filters replaces explicit value_filters
    b_drop_vf = gen.generate(
        base_req,
        value_filters={"Enc Type": ["Inpatient"]},
        refinement={"remove_filters": ["Enc Type"]},
    )
    assert "ENCNTR_TYPE_CD" not in b_drop_vf.queries[0].ejs_v500_sql
    assert "e.encntr_type_cd" not in b_drop_vf.equivalent_ccl.lower().split("plan e", 1)[1]

    b_repl_vf = gen.generate(
        base_req,
        value_filters={"Enc Type": ["Inpatient"]},
        refinement={"replace_filter_value": {"Enc Type": ["Outpatient"]}},
    )
    assert b_repl_vf.queries[0].ejs_v500_sql.count("CWC_ENCOUNTER.ENCNTR_TYPE_CD IN") == 1
    assert "CWC_ENCOUNTER.ENCNTR_TYPE_CD IN (309309)" in b_repl_vf.queries[0].ejs_v500_sql
    assert "309308" not in b_repl_vf.queries[0].ejs_v500_sql

    # 3. exclude_filters drops CE Result Status while preserving CE Valid Until Dt/Tm >= 12/31/2100
    ce_req = "Build an inpatient oral care flowsheet documentation audit."
    b_ce_no_stat = gen.generate(ce_req, refinement={"remove_filters": ["CE Result Status"]})
    assert b_ce_no_stat.verification.is_valid, b_ce_no_stat.verification.errors
    assert "RESULT_STATUS_CD IN" not in b_ce_no_stat.queries[0].ejs_v500_sql
    assert "2100-12-31" in b_ce_no_stat.queries[0].ejs_v500_sql
    assert "ce.result_status_cd IN" not in b_ce_no_stat.equivalent_ccl

    # 4. Discharged override on Enc Status disables e.disch_dt_tm = NULL in CCLCompiler
    ccl_disch = ccl.compile(base_req, refinement={"replace_filter_value": {"Enc Status": ["Discharged"]}})
    assert ccl_disch.verification.is_valid
    assert "e.disch_dt_tm = NULL" not in ccl_disch.source_code
    assert 'e.encntr_status_cd), 3) IN ("Discharged")' in ccl_disch.source_code


def test_adversarial_schema_validation_and_strict_track_a_vs_track_b() -> None:
    import pytest

    # 1. String selected_elements rejected; {"id": ...} accepted without KeyError
    with pytest.raises(ValueError):
        NeuralCompilationIR.from_dict({"selected_elements": "Enc Id"})
    assert NeuralCompilationIR.parse_from_completion('<output>{"selected_elements": "Enc Id"}</output>') is None
    ir_id = NeuralCompilationIR.from_dict({"id": "tpl_alpha", "selected_elements": ["Enc Id"]})
    assert ir_id.template_id == "tpl_alpha"

    # 2. Unknown hallucinated element fails strict Track A honestly; Track B separates raw_neural_ir vs symbolically_repaired_ir vs symbolic_fallback
    compiler = LocalNeuralCompiler(endpoint_url="http://127.0.0.1:59999")
    prompt = "Build an active inpatient census showing patient name, MRN, FIN, nurse unit, and admit date."
    bogus_comp = (
        "<reasoning>Testing unknown field rejection.</reasoning>\n"
        "<output>"
        + json.dumps({
            "da2_domain": "CWC Encounter",
            "tenancy": "communityworks",
            "selected_elements": ["Enc Id", "Bogus Unverified Hallucination Column"],
        })
        + "</output>"
    )
    ra_bogus = compiler.compile_track_a_raw_ir(prompt, completion_text=bogus_comp)
    assert ra_bogus["birt_valid"] is False
    assert ra_bogus["ccl_valid"] is False
    assert ra_bogus["all_verifiers_passed"] is False

    # Empty after exclusions fails strict Track A
    empty_after_excl = (
        "<reasoning>All columns excluded.</reasoning>\n"
        "<output>"
        + json.dumps({
            "da2_domain": "CWC Encounter",
            "tenancy": "communityworks",
            "selected_elements": ["Enc Loc Room"],
            "exclude_columns": ["Enc Loc Room"],
        })
        + "</output>"
    )
    ra_empty = compiler.compile_track_a_raw_ir(prompt, completion_text=empty_after_excl)
    assert ra_empty["all_verifiers_passed"] is False

    # Track B reports acceptance_mode and total latency_ms >= neural_latency_ms
    with patch.object(
        compiler,
        "query_neural_endpoint",
        return_value={
            "best_completion": bogus_comp,
            "candidates": [bogus_comp],
            "transport": "openai_compatible_gguf_http",
            "latency_ms": 55.0,
            "vram_allocated_mb": 0.0,
        },
    ):
        tb = compiler.compile_with_verifier_fallback(prompt, n=1)
        assert tb["raw_neural_ir_verified"] is False
        assert tb["acceptance_mode"] in ("symbolically_repaired_ir", "symbolic_fallback")
        assert tb["latency_ms"] >= 55.0


def test_adversarial_lexical_boundaries_naming_and_zero_b64_denylists() -> None:
    from discern_intelligence.lexical_guards import is_financial_charge_or_revenue_request
    from discern_intelligence.neural_compiler import check_domain_and_refinement_compliance

    gen = DA2ReportGenerator()

    # 1. Negation boundary preserves trailing affirmative clause ("and include LOS and attending physician" / "and include patient age")
    from discern_intelligence.lexical_guards import extract_affirmative_text
    assert "patient age" in extract_affirmative_text("Build an active inpatient census without billing columns and include patient age.")
    b_aff = gen.generate("Build an active inpatient census without billing columns and include LOS and attending physician.")
    aff_elems = {e["da2_field_name"] for e in b_aff.queries[0].selected_elements}
    assert "Enc LOS" in aff_elems and "Enc Attending Phys" in aff_elems
    assert not any(c.startswith(("Adj ", "Chg ")) for c in aff_elems)

    # 2. Clinical "fluid balance", "medication reconciliation", and "NDC" barcode MAR do not open charge gate
    for clin_p in (
        "Build an inpatient fluid balance intake and output flowsheet audit.",
        "Build an admission medication reconciliation clinical audit.",
        "Build an inpatient NDC barcode scan MAR administration audit.",
    ):
        assert is_financial_charge_or_revenue_request(clin_p) is False
        ok, viols = check_domain_and_refinement_compliance(clin_p, ["Enc Id", "ChgRecon:Chg Item Id"], [])
        assert ok is False and any("ChgRecon:" in v for v in viols)

    # 3. Pure removal prompt does not derive "Charge_Report"
    b_rem = gen.generate("remove charges")
    assert b_rem.report_name == "Clinical_Encounter_Summary_Report"

    # 4. Explicit "in domain <Domain Name>" works with word boundaries
    b_dom = gen.generate("Extract general ledger transactions in domain CWF GS Revenue showing CPT4 and CDM.")
    assert b_dom.queries[0].da2_domain == "CWF GS Revenue"

    # 5. Zero reversible Base64 literal payloads in src/ or scripts/
    import re
    repo_root = Path(__file__).resolve().parent.parent
    for folder in ("src", "scripts"):
        for py_f in (repo_root / folder).rglob("*.py"):
            content = py_f.read_text(encoding="utf-8")
            assert not re.search(r'b64decode\(\s*["\'][A-Za-z0-9+/=\s]{16,}["\']\s*\)', content), f"Found b64decode literal in {py_f}"


def test_adversarial_codex_round2_ccl_ce_status_and_tuple_guards() -> None:
    import pytest
    from discern_intelligence.ccl_compiler import CCLCompiler
    from discern_intelligence.da2_generator import DA2ReportGenerator
    from discern_intelligence.schemas import NeuralCompilationIR, RefinementSpec

    gen = DA2ReportGenerator()
    ccl = CCLCompiler()

    # 1. Tuple rejected by NeuralCompilationIR.from_dict
    with pytest.raises(ValueError):
        NeuralCompilationIR.from_dict({"selected_elements": ("Enc Id",)})

    # 2. Negated / substring override values rejected in both DA2 and CCL
    for bad_val in ["not inpatient", "not ICU", "3 West", ""]:
        with pytest.raises(ValueError):
            gen.generate("Active inpatient census", refinement=RefinementSpec(override_filters={"Enc Loc Nurse Unit": [bad_val]}))
        with pytest.raises(ValueError):
            ccl.compile("Active inpatient census", refinement=RefinementSpec(override_filters={"Enc Loc Nurse Unit": [bad_val]}))

    # 3. CCL prior_filters superseded by explicit value_filters on same field
    p_vf = ccl.compile(
        "Encounter census with FIN and Nurse Unit",
        refinement=RefinementSpec(prior_filters={"Enc Type": ["Inpatient"]}),
        value_filters=[("ENCOUNTER.ENCNTR_TYPE_CD", ["309309"])],
    )
    assert p_vf.verification.is_valid
    assert "309309" in p_vf.source_code
    assert '"Inpatient"' not in p_vf.source_code

    # 4. CE Result Status override consistent across <cclquery>, .daquery, EJS SQL, and CCL
    b_ce_ov = gen.generate(
        "Clinical event results with FIN and Nurse Unit",
        refinement=RefinementSpec(override_filters={"CE Result Status": ["Modified"]}),
    )
    q0 = b_ce_ov.queries[0]
    assert b_ce_ov.verification.is_valid
    assert q0.daquery_xml.count('<element name="CE Result Status" type="1"') == 1
    assert '<value display="Modified" text="35.00"/>' in q0.daquery_xml
    assert '<value display="Auth (Verified)"' not in q0.daquery_xml
    assert '<value display="Modified" text="35"/>' in q0.cclquery_xml
    assert "CWC_CLINICAL_EVENT.RESULT_STATUS_CD IN (35)" in q0.ejs_v500_sql
    assert "IN (25, 34, 35)" not in q0.ejs_v500_sql

    p_ce_ov = ccl.compile(
        "Clinical event results with FIN and Nurse Unit",
        refinement=RefinementSpec(override_filters={"CE Result Status": ["Modified"]}),
    )
    assert p_ce_ov.verification.is_valid
    assert 'trim(uar_get_code_display(ce.result_status_cd), 3) IN ("Modified")' in p_ce_ov.source_code
    assert "c_auth_cd, c_modified_cd, c_altered_cd" not in p_ce_ov.source_code
