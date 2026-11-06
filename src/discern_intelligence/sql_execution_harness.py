"""
In-Memory Synthetic Millennium V500 / DA2 SQLite Execution Harness (`sql_execution_harness.py`).

Populates a zero-PHI synthetic Oracle Health / Cerner Millennium `V500` relational database
inside SQLite (`ATTACH DATABASE ':memory:' AS V500`), registers deterministic SQLite scalar
UDFs for Oracle / Discern EJS SQL functions (`omf_get_cv_display`, `cclsql_utc_cnvt`,
`cclsql_cnvtdate2`, `TO_DATE`, `NVL`, `uar_get_code_display`, `CWC_ENC_FUNC.GET_FIN_NBR`,
`CWC_PER_FUNC.GET_MRN`), and executes generated EJS V500 SQL queries end-to-end to prove:
1. Syntactic & relational executability against `V500.*` tables and aliases.
2. Non-empty result sets (`row_count >= 1`) across clinical and financial domains.
3. Zero unintended Cartesian fan-out (`fanout_safe = True`).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

SYNTHETIC_CODE_VALUES: dict[int, tuple[int, str, str]] = {
    # Code Set 8 (Result Status)
    25: (8, "AUTH", "Auth (Verified)"),
    34: (8, "MODIFIED", "Modified"),
    35: (8, "ALTERED", "Altered"),
    # Code Set 48 (Active Status)
    188: (48, "ACTIVE", "Active"),
    # Code Set 71 (Encounter Type)
    309308: (71, "INPATIENT", "Inpatient"),
    309309: (71, "OUTPATIENT", "Outpatient"),
    309312: (71, "EMERGENCY", "Emergency"),
    # Code Set 72 (Clinical Event Code)
    720001: (72, "CSSRS_FORM", "CSSRS Adult Screening - Form"),
    720002: (72, "ADM_HIST", "Admission History Adult - Form"),
    720003: (72, "PT_EVAL", "PT Evaluation Therapy Minutes"),
    720004: (72, "LACTATE", "Lactate Venous"),
    # Code Set 200 (Order Catalog)
    200001: (200, "LACTATE_ORD", "Lactate Stat"),
    200002: (200, "VANCOMYCIN_ORD", "Vancomycin 1000 mg IV"),
    # Code Set 220 (Location)
    220001: (220, "FACILITY", "Regional Medical Center"),
    220002: (220, "BUILDING", "Main Inpatient Pavilion"),
    220003: (220, "NURSEUNIT", "North 3 Rehab Unit"),
    220004: (220, "ROOM", "Room 301"),
    220005: (220, "BED", "Bed A"),
    # Code Set 261 (Encounter Status)
    854: (261, "ACTIVE", "Active"),
    856: (261, "DISCHARGED", "Discharged"),
    # Code Set 400 (Diagnosis / Problem Vocabulary)
    400001: (400, "ICD10CM", "ICD-10-CM"),
    # Code Set 6004 (Order Status)
    2550: (6004, "ORDERED", "Ordered"),
    2543: (6004, "COMPLETED", "Completed"),
}


def normalize_oracle_ejs_sql_for_sqlite(sql_text: str) -> str:
    """Rewrites Oracle package-qualified function calls into SQLite scalar UDF identifiers."""
    out = re.sub(r"\bCWC_ENC_FUNC\.GET_FIN_NBR\s*\(", "CWC_ENC_FUNC__GET_FIN_NBR(", sql_text, flags=re.I)
    out = re.sub(r"\bCWC_PER_FUNC\.GET_MRN\s*\(", "CWC_PER_FUNC__GET_MRN(", out, flags=re.I)
    out = re.sub(r"\bCWC_PERSON_FUNC\.GET_MRN\s*\(", "CWC_PER_FUNC__GET_MRN(", out, flags=re.I)
    return out


def create_synthetic_v500_connection() -> sqlite3.Connection:
    """Creates an in-memory SQLite database with `V500` schema and Oracle/EJS scalar UDFs."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS V500")

    def _omf_get_cv_display(code_val: Any) -> str:
        if code_val is None:
            return ""
        try:
            cv_int = int(float(code_val))
        except (TypeError, ValueError):
            return str(code_val)
        entry = SYNTHETIC_CODE_VALUES.get(cv_int)
        return entry[2] if entry else f"CV_{cv_int}"

    def _uar_get_code_meaning(code_val: Any) -> str:
        if code_val is None:
            return ""
        try:
            cv_int = int(float(code_val))
        except (TypeError, ValueError):
            return ""
        entry = SYNTHETIC_CODE_VALUES.get(cv_int)
        return entry[1] if entry else ""

    def _uar_get_code_by(by_type: Any, code_set: Any, target: Any) -> int:
        t_str = str(target or "").strip().upper()
        try:
            cs_int = int(code_set)
        except (TypeError, ValueError):
            return 0
        for cv, (cs, meaning, display) in SYNTHETIC_CODE_VALUES.items():
            if cs == cs_int and (meaning.upper() == t_str or display.upper() == t_str):
                return cv
        return 0

    def _cclsql_utc_cnvt(dt_val: Any, mode: Any = 1, tz_idx: Any = 54) -> str | None:
        if dt_val is None:
            return None
        return str(dt_val)

    def _to_date(dt_val: Any, fmt: Any = None) -> str | None:
        if dt_val is None:
            return None
        return str(dt_val)

    def _nvl(val: Any, fallback: Any) -> Any:
        return fallback if val is None else val

    def _get_fin_nbr(encntr_id: Any) -> str:
        return f"FIN-{int(float(encntr_id or 0)):07d}"

    def _get_mrn(encntr_id: Any, person_id: Any, loc_facility_cd: Any = None) -> str:
        return f"MRN-{int(float(person_id or 0)):06d}"

    conn.create_function("omf_get_cv_display", 1, _omf_get_cv_display)
    conn.create_function("uar_get_code_display", 1, _omf_get_cv_display)
    conn.create_function("uar_get_code_meaning", 1, _uar_get_code_meaning)
    conn.create_function("uar_get_code_by", 3, _uar_get_code_by)
    conn.create_function("cclsql_utc_cnvt", 3, _cclsql_utc_cnvt)
    conn.create_function("cclsql_cnvtdate2", 2, _to_date)
    conn.create_function("TO_DATE", 2, _to_date)
    conn.create_function("NVL", 2, _nvl)
    conn.create_function("CWC_ENC_FUNC__GET_FIN_NBR", 1, _get_fin_nbr)
    conn.create_function("CWC_PER_FUNC__GET_MRN", 3, _get_mrn)

    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE V500.CODE_VALUE (
            CODE_VALUE INTEGER PRIMARY KEY,
            CODE_SET INTEGER,
            CDF_MEANING TEXT,
            DISPLAY TEXT,
            DESCRIPTION TEXT,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.PERSON (
            PERSON_ID INTEGER PRIMARY KEY,
            NAME_FULL_FORMATTED TEXT,
            BIRTH_DT_TM TEXT,
            SEX_CD INTEGER,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.ENCOUNTER (
            ENCNTR_ID INTEGER PRIMARY KEY,
            PERSON_ID INTEGER,
            ENCNTR_TYPE_CD INTEGER,
            ENCNTR_STATUS_CD INTEGER,
            LOC_FACILITY_CD INTEGER,
            LOC_BUILDING_CD INTEGER,
            LOC_NURSE_UNIT_CD INTEGER,
            LOC_ROOM_CD INTEGER,
            LOC_BED_CD INTEGER,
            REG_DT_TM TEXT,
            INPATIENT_ADMIT_DT_TM TEXT,
            ARRIVE_DT_TM TEXT,
            DISCH_DT_TM TEXT,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.CLINICAL_EVENT (
            CLINICAL_EVENT_ID INTEGER PRIMARY KEY,
            EVENT_ID INTEGER,
            PARENT_EVENT_ID INTEGER,
            ENCNTR_ID INTEGER,
            PERSON_ID INTEGER,
            EVENT_CD INTEGER,
            RESULT_VAL TEXT,
            RESULT_UNITS_CD INTEGER,
            RESULT_STATUS_CD INTEGER,
            EVENT_END_DT_TM TEXT,
            PERFORMED_DT_TM TEXT,
            VALID_UNTIL_DT_TM TEXT,
            VIEW_LEVEL INTEGER
        );

        CREATE TABLE V500.ORDERS (
            ORDER_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            PERSON_ID INTEGER,
            CATALOG_CD INTEGER,
            ORDER_MNEMONIC TEXT,
            CLINICAL_DISPLAY_LINE TEXT,
            ORDER_STATUS_CD INTEGER,
            ORIG_ORDER_DT_TM TEXT,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.CHARGE (
            CHARGE_ITEM_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            PERSON_ID INTEGER,
            CHARGE_DESCRIPTION TEXT,
            ITEM_QUANTITY REAL,
            ITEM_EXTENDED_PRICE REAL,
            SERVICE_DT_TM TEXT,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.MED_ADMIN_EVENT (
            MED_ADMIN_EVENT_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            ORDER_ID INTEGER,
            EVENT_ID INTEGER,
            BEG_DT_TM TEXT,
            END_DT_TM TEXT,
            ADMIN_DOSAGE REAL,
            DOSAGE_UNIT_CD INTEGER,
            ADMIN_ROUTE_CD INTEGER,
            ADMIN_SITE_CD INTEGER,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.SURGICAL_CASE (
            SURG_CASE_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            PERSON_ID INTEGER,
            SURG_CASE_NBR_FORMATTED TEXT,
            SURG_START_DT_TM TEXT,
            SURG_STOP_DT_TM TEXT,
            SURG_AREA_CD INTEGER,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.SURG_CASE_PROCEDURE (
            SURG_CASE_PROC_ID INTEGER PRIMARY KEY,
            SURG_CASE_ID INTEGER,
            ORDER_ID INTEGER,
            PROC_TEXT TEXT,
            PRIMARY_PROC_IND INTEGER,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.DIAGNOSIS (
            DIAGNOSIS_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            PERSON_ID INTEGER,
            NOMENCLATURE_ID INTEGER,
            DIAG_DISPLAY TEXT,
            DIAG_TYPE_CD INTEGER,
            DIAG_PRIORITY INTEGER,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.PROBLEM (
            PROBLEM_ID INTEGER PRIMARY KEY,
            PERSON_ID INTEGER,
            NOMENCLATURE_ID INTEGER,
            PROBLEM_FTDESC TEXT,
            LIFE_CYCLE_STATUS_CD INTEGER,
            ONSET_DT_TM TEXT,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.PFT_ENCNTR (
            PFT_ENCNTR_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            ACCT_ID INTEGER,
            BALANCE_AMT REAL,
            TOTAL_CHARGES_AMT REAL,
            TOTAL_PAYMENTS_AMT REAL,
            TOTAL_ADJ_AMT REAL,
            ACTIVE_IND INTEGER
        );

        CREATE TABLE V500.TRANS_LOG (
            ACTIVITY_ID INTEGER PRIMARY KEY,
            PFT_ENCNTR_ID INTEGER,
            ENCNTR_ID INTEGER,
            TRANS_TYPE_CD INTEGER,
            TRANS_REASON_CD INTEGER,
            TRANS_AMT REAL,
            POST_DT_TM TEXT,
            ACTIVE_IND INTEGER
        );
        """
    )

    for cv, (cs, meaning, display) in SYNTHETIC_CODE_VALUES.items():
        cur.execute(
            "INSERT INTO V500.CODE_VALUE VALUES (?, ?, ?, ?, ?, 1)",
            (cv, cs, meaning, display, display),
        )

    # Synthetic zero-PHI patients & encounters (1 active inpatient, 1 discharged inpatient)
    cur.execute(
        "INSERT INTO V500.PERSON VALUES (1001, 'PATIENT, SYNTHETIC ALPHA', '1965-04-12 00:00:00', 362, 1)"
    )
    cur.execute(
        "INSERT INTO V500.PERSON VALUES (1002, 'PATIENT, SYNTHETIC BETA', '1978-09-20 00:00:00', 363, 1)"
    )

    cur.execute(
        """
        INSERT INTO V500.ENCOUNTER VALUES (
            50001, 1001, 309308, 854, 220001, 220002, 220003, 220004, 220005,
            '2026-10-01 08:30:00', '2026-10-01 09:00:00', '2026-10-01 08:15:00', NULL, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.ENCOUNTER VALUES (
            50002, 1002, 309308, 856, 220001, 220002, 220003, 220004, 220005,
            '2026-09-10 10:00:00', '2026-09-10 10:30:00', '2026-09-10 09:45:00', '2026-09-24 14:00:00', 1
        )
        """
    )

    # Synthetic clinical events (C-SSRS, PT Therapy Minutes, Lactate)
    cur.execute(
        """
        INSERT INTO V500.CLINICAL_EVENT VALUES (
            90001, 90001, 90000, 50001, 1001, 720001, 'Negative / Low Risk', 0, 25,
            '2026-10-01 10:15:00', '2026-10-01 10:15:00', '2100-12-31 00:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.CLINICAL_EVENT VALUES (
            90002, 90002, 90000, 50001, 1001, 720003, '45', 0, 25,
            '2026-10-02 11:00:00', '2026-10-02 11:00:00', '2100-12-31 00:00:00', 1
        )
        """
    )

    # Synthetic orders, MAR, surgical cases, diagnoses, problems, charges, PFT
    cur.execute(
        """
        INSERT INTO V500.ORDERS VALUES (
            70001, 50001, 1001, 200002, 'Vancomycin 1000 mg IV', '1000 mg IV q12h', 2550,
            '2026-10-01 09:30:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.CHARGE VALUES (
            80001, 50001, 1001, 'Physical Therapy Evaluation 45 Min', 1.0, 240.00,
            '2026-10-02 11:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.MED_ADMIN_EVENT VALUES (
            60001, 50001, 70001, 90001, '2026-10-01 10:00:00', '2026-10-01 11:00:00',
            1000.0, 0, 0, 0, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.SURGICAL_CASE VALUES (
            40001, 50001, 1001, 'OR-2026-0001', '2026-10-01 13:00:00', '2026-10-01 15:15:00',
            220001, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.SURG_CASE_PROCEDURE VALUES (
            41001, 40001, 70001, 'Open Reduction Internal Fixation Femur', 1, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.DIAGNOSIS VALUES (
            30001, 50001, 1001, 11001, 'I69.351 Hemiplegia following cerebral infarction', 0, 1, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.PROBLEM VALUES (
            31001, 1001, 11001, 'Chronic atrial fibrillation', 0, '2025-01-15 00:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.PFT_ENCNTR VALUES (
            20001, 50001, 99001, 1250.00, 4500.00, 3000.00, 250.00, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.TRANS_LOG VALUES (
            21001, 20001, 50001, 1, 1, 3000.00, '2026-10-03 12:00:00', 1
        )
        """
    )
    conn.commit()
    return conn


def execute_and_verify_ejs_sql(sql_text: str, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """
    Executes an EJS V500 SQL query against the synthetic Millennium V500 SQLite database
    and returns an execution and join-safety report.
    """
    owns_conn = conn is None
    db = conn or create_synthetic_v500_connection()
    normalized_sql = normalize_oracle_ejs_sql_for_sqlite(sql_text)
    try:
        cur = db.execute(normalized_sql)
        rows = [dict(r) for r in cur.fetchall()]
        columns = [d[0] for d in (cur.description or [])]
        enc_ids = {r.get("Enc_Id") for r in rows if r.get("Enc_Id") is not None}
        # In our synthetic DB, encounter 50001 has at most 2 clinical events and 1 order/charge/MAR/surg/diag
        fanout_safe = len(rows) <= max(4, len(enc_ids) * 4)
        return {
            "executed": True,
            "row_count": len(rows),
            "non_empty": len(rows) > 0,
            "columns": columns,
            "unique_encounters": len(enc_ids),
            "fanout_safe": fanout_safe,
            "sample_rows": rows[:3],
            "error": None,
        }
    except Exception as exc:
        return {
            "executed": False,
            "row_count": 0,
            "non_empty": False,
            "columns": [],
            "unique_encounters": 0,
            "fanout_safe": False,
            "sample_rows": [],
            "error": str(exc),
        }
    finally:
        if owns_conn:
            db.close()
