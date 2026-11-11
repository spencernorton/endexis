"""
Clinical Forms, Assessments, Notes, Flowsheets, Intake/Output, Order Details,
and Financial Output Resolver for Cerner Millennium V500 and DA2.
"""
from __future__ import annotations

import re
from typing import Any

from .knowledge import load_forms_catalog, load_som_summary
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
        text_low = request_text.lower()
        tokens = set(re.findall(r"[a-z0-9_&-]+", text_low))

        scored: list[FormOutputResolution] = []
        for entry in self._forms:
            score = 0.0
            for kw in entry.get("keywords", []):
                kw_low = kw.lower()
                if _mentions(text_low, kw_low):
                    # Multi-word exact phrase gets a strong boost
                    score += 4.5 if " " in kw_low else 2.5
                else:
                    kw_parts = set(kw_low.split())
                    overlap = len(tokens & kw_parts)
                    if overlap:
                        score += overlap * 0.9

            # Boost if table or payload column names are explicitly mentioned
            for dt in entry.get("definition_tables", []) + entry.get("activity_tables", []):
                if _mentions(text_low, dt["table"].lower()):
                    score += 5.0
            for pc in entry.get("payload_columns", []):
                tc = pc["table_column"].lower()
                if any(_mentions(text_low, part) for part in tc.split(".")):
                    score += 1.5

            if score > 0:
                som_matches = self._match_som_objects(text_low, tokens, entry["id"])
                da2_map = entry.get("da2_mapping", {})
                scored.append(
                    FormOutputResolution(
                        form_id=entry["id"],
                        form_family_name=entry["name"],
                        definition_tables=entry.get("definition_tables", []),
                        activity_tables=entry.get("activity_tables", []),
                        payload_columns=entry.get("payload_columns", []),
                        join_pattern_sql=entry.get("join_pattern_sql", ""),
                        da2_domain=da2_map.get("domain", "CWC Encounter"),
                        da2_sub_entity=da2_map.get("sub_entity", "Clinical Events"),
                        da2_fields=da2_map.get("fields", []),
                        mandatory_filters=da2_map.get("mandatory_filters", []),
                        matched_som_objects=som_matches,
                        score=round(score, 2),
                    )
                )

        scored.sort(key=lambda x: x.score, reverse=True)
        if not scored:
            # Default fallback to PowerForms + Clinical Events if generic form/output query
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
            if oname in preferred or oname.lower() in text_low:
                results.append({
                    "som_object": oname,
                    "description": obj["description"],
                    "sample_fields": [f["key"] for f in obj.get("key_fields", [])[:8]],
                })
        return results[:4]
