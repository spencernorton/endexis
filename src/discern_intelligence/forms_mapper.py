"""
Clinical Forms, Assessments, Notes, Flowsheets, Intake/Output, Order Details,
and Financial Output Resolver for Cerner Millennium V500 and DA2.
"""
from __future__ import annotations

import re
from typing import Any

from .knowledge import load_forms_catalog, load_som_summary
from .lexical_guards import (
    extract_affirmative_text,
    has_word_phrase,
    is_financial_adjustment_request,
    is_financial_charge_or_revenue_request,
    is_financial_payment_request,
)
from .schemas import FormOutputResolution


def _mentions(text_low: str, term: str) -> bool:
    """The term at the start of a word, so "charge" matches "charges" and "charged" but not "discharge"."""
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}", text_low))


class FormsAndOutputResolver:
    """Determines the exact V500 tables, payload columns, and DA2 fields for clinical forms & outputs."""

    def __init__(self) -> None:
        self._forms = load_forms_catalog()
        self._som = load_som_summary()

    def resolve(self, request_text: str, top_k: int = 3) -> list[FormOutputResolution]:
        aff_text = extract_affirmative_text(request_text)
        text_low = aff_text.lower()
        tokens = set(re.findall(r"[a-z0-9_&-]+", text_low))
        has_fin_intent = (
            is_financial_charge_or_revenue_request(request_text)
            or is_financial_adjustment_request(request_text)
            or is_financial_payment_request(request_text)
        )
        has_chg_intent = is_financial_charge_or_revenue_request(request_text)

        scored: list[FormOutputResolution] = []
        for entry in self._forms:
            fid = entry["id"]
            if fid == "revcycle_patient_accounting_outputs" and not has_fin_intent:
                continue

            score = 0.0
            for kw in entry.get("keywords", []):
                kw_low = kw.lower()
                if has_word_phrase(text_low, kw_low):
                    score += 4.5 if " " in kw_low else 2.5
                else:
                    kw_parts = {p for p in kw_low.split() if len(p) > 2}
                    # Never allow 'charge' token to match unless explicit financial/charge intent
                    if not has_chg_intent:
                        kw_parts -= {"charge", "charges", "billed", "cpt"}
                    overlap = len(tokens & kw_parts)
                    if overlap:
                        score += overlap * 0.9

            # Boost only on explicit SQL table/column tokens (never substring 'charge' in 'discharged')
            for dt in entry.get("definition_tables", []) + entry.get("activity_tables", []):
                t_low = dt["table"].lower()
                if ("_" in t_low and has_word_phrase(text_low, t_low)) or f"v500.{t_low}" in text_low:
                    score += 5.0
            for pc in entry.get("payload_columns", []):
                tc = pc["table_column"].lower()
                col_only = tc.split(".", 1)[1] if "." in tc else tc
                if tc in text_low or ("_" in col_only and len(col_only) > 5 and has_word_phrase(text_low, col_only)):
                    score += 2.0

            if score > 0:
                som_matches = self._match_som_objects(text_low, tokens, fid)
                da2_map = entry.get("da2_mapping", {})
                da2_fields = list(da2_map.get("fields", []))
                if not has_chg_intent:
                    da2_fields = [f for f in da2_fields if not f.startswith(("Chg ", "BO ", "Adj ", "Pay "))]
                scored.append(
                    FormOutputResolution(
                        form_id=fid,
                        form_family_name=entry["name"],
                        definition_tables=entry.get("definition_tables", []),
                        activity_tables=entry.get("activity_tables", []),
                        payload_columns=entry.get("payload_columns", []),
                        join_pattern_sql=entry.get("join_pattern_sql", ""),
                        da2_domain=da2_map.get("domain", "CWC Encounter"),
                        da2_sub_entity=da2_map.get("sub_entity", "Clinical Events"),
                        da2_fields=da2_fields,
                        mandatory_filters=da2_map.get("mandatory_filters", []),
                        matched_som_objects=som_matches,
                        score=round(score, 2),
                    )
                )

        scored.sort(key=lambda x: x.score, reverse=True)
        if not scored:
            default_entry = self._forms[0]
            da2_map = default_entry.get("da2_mapping", {})
            scored.append(
                FormOutputResolution(
                    form_id=default_entry["id"],
                    form_family_name=default_entry["name"],
                    definition_tables=default_entry.get("definition_tables", []),
                    activity_tables=default_entry.get("activity_tables", []),
                    payload_columns=default_entry.get("payload_columns", []),
                    join_pattern_sql=default_entry.get("join_pattern_sql", ""),
                    da2_domain=da2_map.get("domain", "CWC Encounter"),
                    da2_sub_entity=da2_map.get("sub_entity", "Clinical Events"),
                    da2_fields=da2_map.get("fields", []),
                    mandatory_filters=da2_map.get("mandatory_filters", []),
                    matched_som_objects=self._match_som_objects(text_low, tokens, default_entry["id"]),
                    score=1.0,
                )
            )
        return scored[:top_k]

    def _match_som_objects(
        self, text_low: str, tokens: set[str], form_id: str
    ) -> list[dict[str, Any]]:
        form_to_som = {
            "powerforms_assessments": ["Questionnaire", "QuestionnaireResponse", "QuestionnaireSection", "Evaluation", "Observation"],
            "interactive_view_flowsheets": ["Observation", "ObservationDefinition", "EshEventcode", "EshEventset"],
            "intake_and_output_2g": ["Observation", "ObservationDefinition"],
            "dynamic_documentation_notes_blobs": ["DocumentReference", "DocumentContent", "NoteComponent", "ClinicalReviewRecord"],
            "order_entry_formats_and_details": ["ServiceRequest", "MedicationRequest", "NutritionOrder", "OrderCatalog"],
            "cpdi_interactive_and_scanned_forms": ["DocumentReference", "Consent", "SignatureRecord"],
            "mar_bcma_medication_administration": ["MedicationAdministration", "MedicationDispense", "Medication"],
            "rehab_therapy_productivity_and_charges": ["Evaluation", "Procedure", "ChargeItem"],
            "revcycle_patient_accounting_outputs": ["Account", "ChargeItem", "Claim", "FinancialTransaction", "GeneralLedgerEntry", "Balance"],
            "clinical_tasks_and_overdue_inbox": ["Task", "Checklist", "ChecklistItem"],
            "scheduling_appointments_and_actions": ["Appointment", "Schedule", "Slot"],
            "powerplans_care_pathways_and_outcomes": ["CarePlan", "CareGoal", "PlanDefinition", "PatientCarePathway"],
            "radiology_and_pathology_diagnostic_reports": ["DiagnosticReport", "Specimen", "ImageDocument"],
        }
        preferred = set(form_to_som.get(form_id, []))
        results = []
        for obj in self._som:
            oname = obj["object_name"]
            if oname in preferred or has_word_phrase(text_low, oname.lower()):
                results.append({
                    "som_object": oname,
                    "description": obj["description"],
                    "sample_fields": [f["key"] for f in obj.get("key_fields", [])[:8]],
                })
        return results[:4]
