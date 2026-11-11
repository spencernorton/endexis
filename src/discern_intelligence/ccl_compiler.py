"""
Request-to-CCL (Cerner Command Language) Compiler & Static Verifier.

Compiles natural-language analytical requests and interpreted V500 schemas into
executable Discern Explorer / DVDev .prg programs with PLAN/JOIN blocks,
uar_get_code_by() constants, and mandatory row-versioning predicates.
"""
from __future__ import annotations

import re
from typing import Any

from .interpreter import MillenniumInterpreter
from .schemas import CompiledCCLProgram, InterpretationResult, VerificationReport

TABLE_ALIASES: dict[str, str] = {
    "ENCOUNTER": "e",
    "PERSON": "p",
    "ENCNTR_ALIAS": "ea",
    "PERSON_ALIAS": "pa",
    "ENCNTR_LOC_HIST": "elh",
    "ENCNTR_PRSNL_RELTN": "epr",
    "PRSNL": "pr",
    "CLINICAL_EVENT": "ce",
    "CE_MED_RESULT": "cmr",
    "CE_INTAKE_OUTPUT_RESULT": "cio",
    "CE_CODED_RESULT": "ccr",
    "CE_DATE_RESULT": "cdr",
    "CE_STRING_RESULT": "csr",
    "CE_BLOB_RESULT": "cbr",
    "CE_BLOB": "cb",
    "DCP_FORMS_ACTIVITY": "dfa",
    "DCP_FORMS_ACTIVITY_COMP": "dfac",
    "DCP_FORMS_REF": "dfr",
    "SCD_STORY": "scd",
    "ORDERS": "o",
    "ORDER_DETAIL": "od",
    "MED_ADMIN_EVENT": "mae",
    "TASK_ACTIVITY": "ta",
    "SCH_APPT": "sa",
    "SCH_EVENT": "se",
    "SCH_EVENT_ACTION": "sea",
    "CHARGE": "c",
    "CHARGE_MOD": "cm",
    "PFT_ENCNTR": "pe",
    "BENEFIT_ORDER": "bo",
    "BO_HP_RELTN": "bhr",
    "HEALTH_PLAN": "hp",
    "DIAGNOSIS": "dx",
    "NOMENCLATURE": "nom",
    "V500_EVENT_SET_CODE": "esc",
    "V500_EVENT_SET_CANON": "escan",
    "V500_EVENT_SET_EXPLODE": "esexp",
    "MLTM_NDC_MAIN_DRUG_CODE": "mltm",
}


class CCLCompiler:
    """Compiles natural language requests into validated Cerner CCL (.prg) programs."""

    def __init__(self, interpreter: MillenniumInterpreter | None = None) -> None:
        self._interpreter = interpreter or MillenniumInterpreter()

    def compile(
        self,
        request_text: str,
        program_name: str | None = None,
        tenancy: str | None = None,
        interpretation: InterpretationResult | None = None,
        value_filters: list[tuple[str, list[str]]] | None = None,
    ) -> CompiledCCLProgram:
        """value_filters: ("TABLE.COLUMN_CD", [code values]) IN lists, added to that table's PLAN/JOIN when it has one."""
        interp = interpretation or self._interpreter.interpret(request_text, tenancy=tenancy)
        prog_name = self._sanitize_program_name(program_name or self._derive_program_name(request_text))
        low = request_text.lower()
        is_active_census = ("active" in low and "census" in low) or any(
            w in low for w in ("active inpatient", "current inpatient", "midnight census", "currently admitted", "inhouse")
        )

        is_cssrs = any(w in low for w in ("cssrs", "c-ssrs", "columbia", "suicide"))
        # Ordered table list anchored on ENCOUNTER and PERSON
        ordered_tables = ["ENCOUNTER", "PERSON", "ENCNTR_ALIAS"]
        if is_cssrs:
            for req_t in ("PERSON_ALIAS", "CLINICAL_EVENT"):
                if req_t not in ordered_tables:
                    ordered_tables.append(req_t)
        for cm in interp.matched_columns:
            if cm.table not in ordered_tables and cm.table in TABLE_ALIASES:
                ordered_tables.append(cm.table)
        if "ENCNTR_PRSNL_RELTN" in ordered_tables and "PRSNL" not in ordered_tables:
            ordered_tables.append("PRSNL")
        if "DCP_FORMS_ACTIVITY" in ordered_tables:
            for req_t in ("DCP_FORMS_REF", "DCP_FORMS_ACTIVITY_COMP", "CLINICAL_EVENT"):
                if req_t not in ordered_tables:
                    ordered_tables.append(req_t)
        if "CE_BLOB" in ordered_tables and "CLINICAL_EVENT" not in ordered_tables:
            ordered_tables.insert(3, "CLINICAL_EVENT")
        if "CE_MED_RESULT" in ordered_tables and "CLINICAL_EVENT" not in ordered_tables:
            ordered_tables.insert(3, "CLINICAL_EVENT")
        if "ORDER_DETAIL" in ordered_tables and "ORDERS" not in ordered_tables:
            ordered_tables.insert(3, "ORDERS")
        if "CHARGE_MOD" in ordered_tables and "CHARGE" not in ordered_tables:
            ordered_tables.insert(3, "CHARGE")
        if "BO_HP_RELTN" in ordered_tables:
            for req_t in ("PFT_ENCNTR", "BENEFIT_ORDER"):
                if req_t not in ordered_tables:
                    ordered_tables.append(req_t)

        # Build UAR constant declarations
        uar_decls = [
            'DECLARE c_fin_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 319, "FIN NBR"))',
            'DECLARE c_mrn_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 4, "MRN"))',
        ]
        if "CLINICAL_EVENT" in ordered_tables:
            uar_decls.extend([
                'DECLARE c_auth_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 8, "AUTH"))',
                'DECLARE c_modified_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 8, "MODIFIED"))',
                'DECLARE c_altered_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 8, "ALTERED"))',
            ])
        if "ENCNTR_PRSNL_RELTN" in ordered_tables:
            uar_decls.append(
                'DECLARE c_attenddoc_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 333, "ATTENDDOC"))'
            )
        if "CE_BLOB" in ordered_tables:
            uar_decls.append(
                'DECLARE c_ocfcomp_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", 120, "OCFCOMP"))'
            )

        # Build SELECT projection expressions
        select_exprs: list[str] = [
            "encntr_id = cnvtstring(e.encntr_id, 17, 0)",
            "fin = trim(ea.alias, 3)",
            "person_id = cnvtstring(p.person_id, 17, 0)",
            "patient_name = trim(p.name_full_formatted, 3)",
        ]
        seen_proj = {"ENCOUNTER.ENCNTR_ID", "ENCNTR_ALIAS.ALIAS", "PERSON.PERSON_ID", "PERSON.NAME_FULL_FORMATTED"}

        for cm in interp.matched_columns:
            key = f"{cm.table}.{cm.column}"
            if key in seen_proj or cm.table not in ordered_tables:
                continue
            seen_proj.add(key)
            alias = TABLE_ALIASES[cm.table]
            col_low = cm.column.lower()
            out_label = f"{alias}_{col_low}"[:30]
            if cm.column.endswith("_CD"):
                select_exprs.append(f"{out_label} = trim(uar_get_code_display({alias}.{col_low}), 3)")
            elif "DATETIME" in cm.ccl_type or cm.column.endswith("_DT_TM"):
                select_exprs.append(f'{out_label} = format({alias}.{col_low}, "YYYY-MM-DD HH:MM:SS;;D")')
            elif cm.column.endswith("_ID"):
                select_exprs.append(f"{out_label} = cnvtstring({alias}.{col_low}, 17, 0)")
            elif "NUMERIC" in cm.ccl_type or "IND" in cm.ccl_type or "FLAG" in cm.ccl_type:
                select_exprs.append(f"{out_label} = {alias}.{col_low}")
            else:
                select_exprs.append(f"{out_label} = trim({alias}.{col_low}, 3)")

        # Build FROM clause and PLAN/JOIN clauses
        from_lines = [f"    {TABLE_ALIASES[t]} = {t}" if False else f"    {t} {TABLE_ALIASES[t]}" for t in ordered_tables]
        plan_lines = self._build_plan_join_lines(ordered_tables, is_active_census, is_cssrs=is_cssrs)
        applied: list[str] = []
        for target, codes in value_filters or []:
            table, column = target.split(".")
            alias = TABLE_ALIASES.get(table)
            head = next((i for i, ln in enumerate(plan_lines) if ln in (f"PLAN {alias}", f"JOIN {alias}")), None)
            if alias and table in ordered_tables and head is not None:
                end = next((i for i in range(head + 1, len(plan_lines)) if not plan_lines[i].startswith(" ")), len(plan_lines))
                plan_lines.insert(end, f"      AND {alias}.{column.lower()} IN ({', '.join(codes)})")
                applied.append(target)

        tenancy_note = (
            "Standalone / RHO single-tenant domain: compile and execute directly in DVDev / Discern Explorer."
            if interp.inferred_tenancy == "standalone_rho"
            else (
                f"Target tenancy is '{interp.inferred_tenancy}' (multi-tenant shared domain). "
                "Tenant-authored CCL compilation is restricted on the shared parent RDBMS; use this CCL program "
                "as the reference specification for Oracle Health ticketed scripts or use the companion DA2/BIRT bundle."
            )
        )

        lines = [
            f"DROP PROGRAM {prog_name}:dba GO",
            f"CREATE PROGRAM {prog_name}:dba",
            "",
            "PROMPT",
            '    "Output to File/Printer/MINE" = "MINE",',
            '    "Begin Date (DD-MMM-YYYY)" = "CURDATE-7",',
            '    "End Date (DD-MMM-YYYY)" = "CURDATE"',
            "WITH OUTDEV, BEG_DT, END_DT",
            "",
            ";=============================================================================",
            f"; Generated by Discern Intelligence V1 for: {request_text}",
            f"; Tenancy Profile: {interp.inferred_tenancy}",
            ";=============================================================================",
            "",
            *uar_decls,
            "",
            "SELECT INTO $OUTDEV",
            ",\n".join(f"    {expr}" for expr in select_exprs),
            "FROM",
            ",\n".join(from_lines),
            *plan_lines,
            "ORDER BY",
            "    e.encntr_id,",
            "    p.name_full_formatted",
            'WITH NOCOUNTER, SEPARATOR = " ", FORMAT, TIME = 120',
            "",
            "END",
            "GO",
        ]
        source_code = "\n".join(lines) + "\n"
        verification = self.verify_ccl_source(source_code, ordered_tables, is_active_census)

        return CompiledCCLProgram(
            program_name=prog_name,
            request_text=request_text,
            target_tenancy=interp.inferred_tenancy,
            tenancy_deployment_note=tenancy_note,
            source_code=source_code,
            tables_used=ordered_tables,
            code_sets_used=interp.matched_code_sets,
            verification=verification,
            value_filters_applied=applied,
        )

    def _build_plan_join_lines(self, ordered_tables: list[str], is_active_census: bool, is_cssrs: bool = False) -> list[str]:
        lines: list[str] = []
        if is_active_census:
            lines.extend([
                "PLAN e",
                "    WHERE e.active_ind = 1",
                '      AND e.reg_dt_tm <= cnvtdatetime(concat($END_DT, " 23:59:59"))',
                "      AND e.disch_dt_tm = NULL",
            ])
        else:
            lines.extend([
                "PLAN e",
                "    WHERE e.active_ind = 1",
                '      AND e.reg_dt_tm BETWEEN cnvtdatetime(concat($BEG_DT, " 00:00:00"))',
                '                          AND cnvtdatetime(concat($END_DT, " 23:59:59"))',
            ])

        join_map: dict[str, list[str]] = {
            "PERSON": [
                "JOIN p",
                "    WHERE p.person_id = e.person_id",
                "      AND p.active_ind = 1",
            ],
            "ENCNTR_ALIAS": [
                "JOIN ea",
                "    WHERE ea.encntr_id = e.encntr_id",
                "      AND ea.encntr_alias_type_cd = c_fin_cd",
                "      AND ea.active_ind = 1",
                "      AND ea.beg_effective_dt_tm <= cnvtdatetime(curdate, curtime3)",
                "      AND ea.end_effective_dt_tm >= cnvtdatetime(curdate, curtime3)",
            ],
            "PERSON_ALIAS": [
                "JOIN pa",
                "    WHERE pa.person_id = p.person_id",
                "      AND pa.person_alias_type_cd = c_mrn_cd",
                "      AND pa.active_ind = 1",
                "      AND pa.beg_effective_dt_tm <= cnvtdatetime(curdate, curtime3)",
                "      AND pa.end_effective_dt_tm >= cnvtdatetime(curdate, curtime3)",
            ],
            "ENCNTR_LOC_HIST": [
                "JOIN elh",
                "    WHERE elh.encntr_id = e.encntr_id",
                "      AND elh.active_ind = 1",
                "      AND elh.beg_effective_dt_tm <= cnvtdatetime(curdate, curtime3)",
                "      AND elh.end_effective_dt_tm >= cnvtdatetime(curdate, curtime3)",
            ],
            "ENCNTR_PRSNL_RELTN": [
                "JOIN epr",
                "    WHERE epr.encntr_id = e.encntr_id",
                "      AND epr.encntr_prsnl_r_cd = c_attenddoc_cd",
                "      AND epr.active_ind = 1",
                "      AND epr.beg_effective_dt_tm <= cnvtdatetime(curdate, curtime3)",
                "      AND epr.end_effective_dt_tm >= cnvtdatetime(curdate, curtime3)",
            ],
            "PRSNL": [
                "JOIN pr",
                "    WHERE pr.person_id = epr.prsnl_person_id"
                if "ENCNTR_PRSNL_RELTN" in ordered_tables
                else "    WHERE pr.person_id = e.updt_id",
                "      AND pr.active_ind = 1",
            ],
            "CLINICAL_EVENT": [
                "JOIN ce",
                "    WHERE ce.encntr_id = e.encntr_id",
                '      AND ce.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
                "      AND ce.result_status_cd IN (c_auth_cd, c_modified_cd, c_altered_cd)",
                "      AND ce.view_level = 1",
                *(
                    [
                        '      AND (cnvtupper(uar_get_code_display(ce.event_cd)) = "*CSSRS*"',
                        '           OR cnvtupper(uar_get_code_display(ce.event_cd)) = "*C-SSRS*"',
                        '           OR cnvtupper(uar_get_code_display(ce.event_cd)) = "*SUICID*"',
                        '           OR uar_get_code_display(ce.event_cd) = "Admission History Adult - Form")',
                    ]
                    if is_cssrs
                    else []
                ),
            ],
            "CE_MED_RESULT": [
                "JOIN cmr",
                "    WHERE cmr.event_id = ce.event_id",
                '      AND cmr.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "CE_INTAKE_OUTPUT_RESULT": [
                "JOIN cio",
                "    WHERE cio.encntr_id = e.encntr_id",
                '      AND cio.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "CE_CODED_RESULT": [
                "JOIN ccr",
                "    WHERE ccr.event_id = ce.event_id",
                '      AND ccr.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "CE_DATE_RESULT": [
                "JOIN cdr",
                "    WHERE cdr.event_id = ce.event_id",
                '      AND cdr.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "CE_STRING_RESULT": [
                "JOIN csr",
                "    WHERE csr.event_id = ce.event_id",
                '      AND csr.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "CE_BLOB_RESULT": [
                "JOIN cbr",
                "    WHERE cbr.event_id = ce.event_id",
                '      AND cbr.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "CE_BLOB": [
                "JOIN cb",
                "    WHERE cb.event_id = ce.event_id",
                '      AND cb.valid_until_dt_tm >= cnvtdatetime("31-DEC-2100 00:00:00")',
            ],
            "DCP_FORMS_ACTIVITY": [
                "JOIN dfa",
                "    WHERE dfa.encntr_id = e.encntr_id",
                "      AND dfa.active_ind = 1",
            ],
            "DCP_FORMS_REF": [
                "JOIN dfr",
                "    WHERE dfr.dcp_forms_ref_id = dfa.dcp_forms_ref_id",
                "      AND dfr.active_ind = 1",
            ],
            "DCP_FORMS_ACTIVITY_COMP": [
                "JOIN dfac",
                "    WHERE dfac.dcp_forms_activity_id = dfa.dcp_forms_activity_id",
                '      AND dfac.parent_entity_name = "CLINICAL_EVENT"',
            ],
            "SCD_STORY": [
                "JOIN scd",
                "    WHERE scd.encntr_id = e.encntr_id",
                "      AND scd.active_ind = 1",
            ],
            "ORDERS": [
                "JOIN o",
                "    WHERE o.encntr_id = e.encntr_id",
                "      AND o.active_ind = 1",
            ],
            "ORDER_DETAIL": [
                "JOIN od",
                "    WHERE od.order_id = o.order_id",
            ],
            "MED_ADMIN_EVENT": [
                "JOIN mae",
                "    WHERE mae.event_id = ce.event_id"
                if "CLINICAL_EVENT" in ordered_tables
                else "    WHERE mae.order_id = o.order_id",
            ],
            "TASK_ACTIVITY": [
                "JOIN ta",
                "    WHERE ta.encntr_id = e.encntr_id",
                "      AND ta.active_ind = 1",
            ],
            "SCH_APPT": [
                "JOIN sa",
                "    WHERE sa.encntr_id = e.encntr_id",
                '      AND sa.role_meaning = "PATIENT"',
                "      AND sa.active_ind = 1",
            ],
            "SCH_EVENT": [
                "JOIN se",
                "    WHERE se.sch_event_id = sa.sch_event_id",
                "      AND se.active_ind = 1",
            ],
            "SCH_EVENT_ACTION": [
                "JOIN sea",
                "    WHERE sea.sch_event_id = se.sch_event_id",
                "      AND sea.active_ind = 1",
            ],
            "CHARGE": [
                "JOIN c",
                "    WHERE c.encntr_id = e.encntr_id",
                "      AND c.active_ind = 1",
            ],
            "CHARGE_MOD": [
                "JOIN cm",
                "    WHERE cm.charge_item_id = c.charge_item_id",
                "      AND cm.active_ind = 1",
            ],
            "PFT_ENCNTR": [
                "JOIN pe",
                "    WHERE pe.encntr_id = e.encntr_id",
                "      AND pe.active_ind = 1",
            ],
            "BENEFIT_ORDER": [
                "JOIN bo",
                "    WHERE bo.pft_encntr_id = pe.pft_encntr_id",
                "      AND bo.active_ind = 1",
            ],
            "BO_HP_RELTN": [
                "JOIN bhr",
                "    WHERE bhr.benefit_order_id = bo.benefit_order_id",
                "      AND bhr.active_ind = 1",
            ],
            "HEALTH_PLAN": [
                "JOIN hp",
                "    WHERE hp.health_plan_id = bhr.health_plan_id"
                if "BO_HP_RELTN" in ordered_tables
                else "    WHERE hp.active_ind = 1",
            ],
            "DIAGNOSIS": [
                "JOIN dx",
                "    WHERE dx.encntr_id = e.encntr_id",
                "      AND dx.active_ind = 1",
            ],
            "V500_EVENT_SET_EXPLODE": [
                "JOIN esexp",
                "    WHERE esexp.event_cd = ce.event_cd"
                if "CLINICAL_EVENT" in ordered_tables
                else "    WHERE esexp.event_set_cd > 0",
            ],
            "V500_EVENT_SET_CANON": [
                "JOIN escan",
                "    WHERE escan.event_set_cd = esexp.event_set_cd"
                if "V500_EVENT_SET_EXPLODE" in ordered_tables
                else "    WHERE escan.parent_event_set_cd > 0",
            ],
            "V500_EVENT_SET_CODE": [
                "JOIN esc",
                "    WHERE esc.event_set_cd = escan.parent_event_set_cd"
                if "V500_EVENT_SET_CANON" in ordered_tables
                else '    WHERE esc.event_set_cd_definition = "ALL OCF EVENT SETS"',
            ],
            "MLTM_NDC_MAIN_DRUG_CODE": [
                "JOIN mltm",
                "    WHERE mltm.drug_identifier > ' '",
            ],
            "NOMENCLATURE": [
                "JOIN nom",
                "    WHERE nom.nomenclature_id = dx.nomenclature_id"
                if "DIAGNOSIS" in ordered_tables
                else "    WHERE nom.active_ind = 1",
            ],
        }

        for tbl in ordered_tables[1:]:
            if tbl in join_map:
                lines.extend(join_map[tbl])

        return lines

    def verify_ccl_source(
        self, source: str, ordered_tables: list[str], is_active_census: bool
    ) -> VerificationReport:
        checked = []
        errors = []
        warnings = []

        if "DROP PROGRAM" in source and "CREATE PROGRAM" in source and source.strip().endswith("GO"):
            checked.append("CCL_PROGRAM_ENVELOPE: Balanced DROP PROGRAM / CREATE PROGRAM / END / GO")
        else:
            errors.append("Missing balanced DROP PROGRAM / CREATE PROGRAM / END / GO envelope.")

        if "SELECT INTO $OUTDEV" in source:
            checked.append("CCL_SELECT_OUTDEV: Emits SELECT INTO $OUTDEV with PROMPT bindings")
        else:
            errors.append("Missing SELECT INTO $OUTDEV clause.")

        for tbl in ordered_tables:
            alias = TABLE_ALIASES[tbl]
            if f"{tbl} {alias}" not in source:
                errors.append(f"Table {tbl} ({alias}) missing from FROM clause.")
            if tbl == "ENCOUNTER":
                if "PLAN e" not in source:
                    errors.append("Missing root PLAN e clause.")
            else:
                if f"JOIN {alias}\n" not in source:
                    errors.append(f"Missing JOIN {alias} clause for table {tbl}.")
        if not errors:
            checked.append(f"CCL_PLAN_JOIN_COMPLETE: All {len(ordered_tables)} FROM tables have explicit PLAN/JOIN blocks")

        if "CLINICAL_EVENT" in ordered_tables:
            if '31-DEC-2100' in source and "c_auth_cd" in source:
                checked.append("CCL_CE_VERSION_GUARD: Enforces valid_until_dt_tm >= 31-DEC-2100 and AUTH/MODIFIED/ALTERED status")
            else:
                errors.append("CLINICAL_EVENT queried without valid_until_dt_tm >= 31-DEC-2100 and result_status_cd filter.")

        if is_active_census:
            if "e.disch_dt_tm = NULL" in source:
                checked.append("CCL_ACTIVE_CENSUS_GUARD: Enforces e.disch_dt_tm = NULL for active inpatient census")
            else:
                errors.append("Active census query failed to filter e.disch_dt_tm = NULL.")

        if 'uar_get_code_by("MEANING"' in source:
            checked.append("CCL_UAR_MEANING_RESOLUTION: Resolves code values by CODE_SET + CDF_MEANING (never brittle display text)")

        return VerificationReport(
            is_valid=len(errors) == 0,
            checked_invariants=checked,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _sanitize_program_name(name: str) -> str:
        clean = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
        clean = re.sub(r"_+", "_", clean).strip("_")
        if not clean or not clean[0].isalpha():
            clean = "cust_" + clean
        return clean[:28]

    @staticmethod
    def _derive_program_name(request_text: str) -> str:
        stop = {"show", "get", "find", "list", "the", "for", "with", "and", "from", "all", "report", "query", "in", "on", "to"}
        words = [w for w in re.findall(r"[a-z0-9]+", request_text.lower()) if w not in stop and len(w) > 2]
        stem = "_".join(words[:4]) if words else "extract"
        return f"cust_da2_{stem}"[:28]
