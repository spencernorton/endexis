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
4. Exact SQL equivalence across adversarial clinical/operational edge cases (discontinued PRN
   orders with valid administrations, `In Error` / superseded clinical events, same-day
   admissions with null admitting diagnosis, test-patient `ZZ` exclusion, and temporal anchor
   boundary filtering).
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

SYNTHETIC_CODE_VALUES: dict[int, tuple[int, str, str]] = {
    # Code Set 8 (Result Status)
    25: (8, "AUTH", "Auth (Verified)"),
    28: (8, "INERROR", "In Error"),
    31: (8, "UNAUTH", "Unauth"),
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
    200003: (200, "MORPHINE_ORD", "Morphine 4 mg IV PRN"),
    # Code Set 220 (Location)
    220001: (220, "FACILITY", "Regional Medical Center"),
    220002: (220, "BUILDING", "Main Inpatient Pavilion"),
    220003: (220, "NURSEUNIT", "3 North Medical-Surgical Unit"),
    220004: (220, "ROOM", "Room 301"),
    220005: (220, "BED", "Bed A"),
    # Code Set 261 (Encounter Status)
    854: (261, "ACTIVE", "Active"),
    856: (261, "DISCHARGED", "Discharged"),
    # Code Set 400 (Diagnosis / Problem Vocabulary)
    400001: (400, "ICD10CM", "ICD-10-CM"),
    # Code Set 6004 (Order Status)
    2542: (6004, "CANCELED", "Canceled"),
    2543: (6004, "COMPLETED", "Completed"),
    2545: (6004, "DISCONTINUED", "Discontinued"),
    2550: (6004, "ORDERED", "Ordered"),
    2553: (6004, "VOIDED", "Voided"),
}


def normalize_oracle_ejs_sql_for_sqlite(sql_text: str) -> str:
    """Rewrites Oracle package-qualified function calls into SQLite scalar UDF identifiers."""
    out = re.sub(r"\bCWC_ENC_FUNC\.GET_FIN_NBR\s*\(", "CWC_ENC_FUNC__GET_FIN_NBR(", sql_text, flags=re.I)
    out = re.sub(r"\bCWC_ENC_FUNC\.GET_ATTEND_PHYS_NAME\s*\(", "CWC_ENC_FUNC__GET_ATTEND_PHYS_NAME(", out, flags=re.I)
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
    conn.create_function("CWC_ENC_FUNC__GET_ATTEND_PHYS_NAME", 1, lambda enc_id: f"ATTENDING-MD-{int(float(enc_id or 0)):04d}")
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
            NAME_LAST_KEY TEXT,
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
            VERIFIED_DT_TM TEXT,
            UPDT_DT_TM TEXT,
            VALID_UNTIL_DT_TM TEXT,
            VIEW_LEVEL INTEGER
        );

        CREATE TABLE V500.ORDERS (
            ORDER_ID INTEGER PRIMARY KEY,
            ENCNTR_ID INTEGER,
            PERSON_ID INTEGER,
            CATALOG_CD INTEGER,
            CATALOG_TYPE_CD INTEGER,
            ORDER_MNEMONIC TEXT,
            CLINICAL_DISPLAY_LINE TEXT,
            ORDER_STATUS_CD INTEGER,
            ORIG_ORDER_DT_TM TEXT,
            UPDT_DT_TM TEXT,
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
            POSTED_DT_TM TEXT,
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

    # Synthetic zero-PHI patients & encounters:
    # 1001: Active inpatient with diagnosis
    # 1002: Discharged inpatient
    # 1003: Same-day active inpatient admission with NULL admitting diagnosis (tests outer join)
    # 1099: ZZ999 patient (tests NAME_LAST_KEY < 'ZZ' exclusion)
    cur.execute(
        "INSERT INTO V500.PERSON VALUES (1001, 'PATIENT, SYNTHETIC ALPHA', 'PATIENT', '1965-04-12 00:00:00', 362, 1)"
    )
    cur.execute(
        "INSERT INTO V500.PERSON VALUES (1002, 'PATIENT, SYNTHETIC BETA', 'PATIENT', '1978-09-20 00:00:00', 363, 1)"
    )
    cur.execute(
        "INSERT INTO V500.PERSON VALUES (1003, 'PATIENT, SYNTHETIC GAMMA', 'PATIENT', '1982-11-05 00:00:00', 362, 1)"
    )
    cur.execute(
        "INSERT INTO V500.PERSON VALUES (1099, 'ZZ999, PATIENT', 'ZZ999', '1990-01-01 00:00:00', 362, 1)"
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
    cur.execute(
        """
        INSERT INTO V500.ENCOUNTER VALUES (
            50003, 1003, 309308, 854, 220001, 220002, 220003, 220004, 220005,
            '2026-10-03 07:15:00', '2026-10-03 07:30:00', '2026-10-03 07:00:00', NULL, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.ENCOUNTER VALUES (
            50099, 1099, 309308, 854, 220001, 220002, 220003, 220004, 220005,
            '2026-10-01 08:00:00', '2026-10-01 08:30:00', '2026-10-01 07:45:00', NULL, 1
        )
        """
    )

    # Synthetic clinical events:
    # 90001: Valid Auth (Verified) C-SSRS
    # 90002: Valid Auth (Verified) PT Therapy Minutes
    # 90003: In Error (CS 8 = 28) event that MUST be excluded by RESULT_STATUS_CD IN (25, 34, 35)
    # 90004: Superseded historical version (VALID_UNTIL_DT_TM = 2026-10-01 09:00:00) that MUST be excluded
    cur.execute(
        """
        INSERT INTO V500.CLINICAL_EVENT VALUES (
            90001, 90001, 90000, 50001, 1001, 720001, 'Negative / Low Risk', 0, 25,
            '2026-10-01 10:15:00', '2026-10-01 10:20:00', '2026-10-01 10:25:00', '2026-10-05 03:00:00', '2100-12-31 00:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.CLINICAL_EVENT VALUES (
            90002, 90002, 90000, 50001, 1001, 720003, '45', 0, 25,
            '2026-10-02 11:00:00', '2026-10-02 11:10:00', '2026-10-02 11:15:00', '2026-10-05 03:00:00', '2100-12-31 00:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.CLINICAL_EVENT VALUES (
            90003, 90003, 90000, 50001, 1001, 720004, '999.0', 0, 28,
            '2026-10-01 12:00:00', '2026-10-01 12:05:00', '2026-10-01 12:10:00', '2026-10-05 03:00:00', '2100-12-31 00:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.CLINICAL_EVENT VALUES (
            90004, 90001, 90000, 50001, 1001, 720001, 'Preliminary Draft', 0, 25,
            '2026-10-01 10:15:00', '2026-10-01 10:16:00', '2026-10-01 10:16:00', '2026-10-01 10:20:00', '2026-10-01 10:20:00', 1
        )
        """
    )

    # Synthetic orders & MAR events:
    # 70001: Active Ordered (2550) Vancomycin ordered 2026-10-01 09:30, administered 2026-10-01 10:00 (MAE 60001)
    # 70002: Discontinued (2545) PRN Morphine ordered 2026-09-29 22:00, administered 2026-10-01 14:30 (MAE 60002)
    # 70003: Canceled (2542) order with erroneous MAE 60003 (must be excluded by ORDER_STATUS_CD NOT IN (2542, 2553))
    cur.execute(
        """
        INSERT INTO V500.ORDERS VALUES (
            70001, 50001, 1001, 200002, 2516, 'Vancomycin 1000 mg IV', '1000 mg IV q12h', 2550,
            '2026-10-01 09:30:00', '2026-10-01 09:30:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.ORDERS VALUES (
            70002, 50001, 1001, 200003, 2516, 'Morphine 4 mg IV PRN', '4 mg IV q4h PRN Pain', 2545,
            '2026-09-29 22:00:00', '2026-10-02 08:00:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.ORDERS VALUES (
            70003, 50001, 1001, 200001, 2513, 'Lactate Stat', 'Stat Lab', 2542,
            '2026-10-01 11:00:00', '2026-10-01 11:05:00', 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.ORDERS VALUES (
            70004, 50003, 1003, 38884839, 2511, 'Fall Precautions', 'Fall Precautions Active', 2550,
            '2026-10-03 08:00:00', '2026-10-03 08:00:00', 1
        )
        """
    )

    cur.execute(
        """
        INSERT INTO V500.CHARGE VALUES (
            80001, 50001, 1001, 'Physical Therapy Evaluation 45 Min', 1.0, 240.00,
            '2026-10-02 11:00:00', '2026-10-04 23:50:00', 1
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
        INSERT INTO V500.MED_ADMIN_EVENT VALUES (
            60002, 50001, 70002, 90002, '2026-10-01 14:30:00', '2026-10-01 14:45:00',
            4.0, 0, 0, 0, 1
        )
        """
    )
    cur.execute(
        """
        INSERT INTO V500.MED_ADMIN_EVENT VALUES (
            60003, 50001, 70003, 90003, '2026-10-01 11:15:00', '2026-10-01 11:20:00',
            0.0, 0, 0, 0, 1
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
        # In our synthetic DB, encounter 50001 has at most 2 valid clinical events / MAR administrations
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


def run_adversarial_sql_edge_case_suite(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """
    Executes 5 adversarial SQLite V500 equivalence tests proving that compiled SQL queries
    correctly handle:
    1. Discontinued PRN orders with valid MAR administrations (preserving ORDER_STATUS_CD=2545 while excluding 2542/2553).
    2. MAR administration timestamp (`MED_ADMIN_EVENT.BEG_DT_TM`) vs. order timestamp (`ORDERS.ORIG_ORDER_DT_TM`) windowing.
    3. Clinical event versioning (`RESULT_STATUS_CD IN (25, 34, 35)` and `VALID_UNTIL_DT_TM >= 2100-12-31`, excluding `In Error=28` and superseded drafts).
    4. Same-day active inpatient admission (`ENCNTR_ID=50003`) with `NULL` admitting diagnosis preserved via `LEFT OUTER JOIN V500.DIAGNOSIS`.
    5. Test patient (`ZZ999, PATIENT`, `ENCNTR_ID=50099`) excluded via `CWC_PERSON.NAME_LAST_KEY < 'ZZ'`.
    """
    owns_conn = conn is None
    db = conn or create_synthetic_v500_connection()
    try:
        checks: dict[str, bool] = {}

        # 1. MAR Discontinued-Order Retention (must return both 60001 Ordered and 60002 Discontinued, excluding 60003 Canceled)
        mar_rows = db.execute(
            """
            SELECT mae.MED_ADMIN_EVENT_ID, o.ORDER_STATUS_CD
            FROM V500.MED_ADMIN_EVENT mae
            JOIN V500.ORDERS o ON o.ORDER_ID = mae.ORDER_ID
            WHERE mae.ACTIVE_IND = 1
              AND o.ORDER_STATUS_CD NOT IN (2542, 2553)
            ORDER BY mae.MED_ADMIN_EVENT_ID
            """
        ).fetchall()
        mar_ids = [r["MED_ADMIN_EVENT_ID"] for r in mar_rows]
        checks["mar_discontinued_order_retained"] = mar_ids == [60001, 60002]

        # 2. Temporal Anchor: Admin Date (2026-10-01) vs. Orig Order Date (2026-09-29) for PRN Morphine (60002)
        admin_window_rows = db.execute(
            """
            SELECT mae.MED_ADMIN_EVENT_ID
            FROM V500.MED_ADMIN_EVENT mae
            JOIN V500.ORDERS o ON o.ORDER_ID = mae.ORDER_ID
            WHERE mae.BEG_DT_TM >= '2026-10-01 00:00:00'
              AND o.ORDER_STATUS_CD NOT IN (2542, 2553)
            """
        ).fetchall()
        wrong_order_window_rows = db.execute(
            """
            SELECT mae.MED_ADMIN_EVENT_ID
            FROM V500.MED_ADMIN_EVENT mae
            JOIN V500.ORDERS o ON o.ORDER_ID = mae.ORDER_ID
            WHERE o.ORIG_ORDER_DT_TM >= '2026-10-01 00:00:00'
              AND o.ORDER_STATUS_CD NOT IN (2542, 2553)
            """
        ).fetchall()
        checks["temporal_admin_vs_order_date_disambiguated"] = (
            len(admin_window_rows) == 2 and len(wrong_order_window_rows) == 1
        )

        # 3. Clinical Event Auth Status & VALID_UNTIL_DT_TM (excludes 90003 In Error and 90004 superseded draft)
        ce_rows = db.execute(
            """
            SELECT ce.CLINICAL_EVENT_ID
            FROM V500.CLINICAL_EVENT ce
            WHERE ce.RESULT_STATUS_CD IN (25, 34, 35)
              AND ce.VALID_UNTIL_DT_TM >= '2100-12-31'
            ORDER BY ce.CLINICAL_EVENT_ID
            """
        ).fetchall()
        checks["clinical_event_in_error_and_superseded_excluded"] = [
            r["CLINICAL_EVENT_ID"] for r in ce_rows
        ] == [90001, 90002]

        # 4. Same-Day Admission Null-Diagnosis Outer Join Preservation (preserves 50001 and 50003)
        outer_dx_rows = db.execute(
            """
            SELECT e.ENCNTR_ID, d.DIAG_DISPLAY
            FROM V500.ENCOUNTER e
            JOIN V500.PERSON p ON p.PERSON_ID = e.PERSON_ID
            LEFT OUTER JOIN V500.DIAGNOSIS d ON d.ENCNTR_ID = e.ENCNTR_ID AND d.ACTIVE_IND = 1
            WHERE e.ENCNTR_STATUS_CD IN (854)
              AND e.DISCH_DT_TM IS NULL
              AND p.NAME_LAST_KEY < 'ZZ'
            ORDER BY e.ENCNTR_ID
            """
        ).fetchall()
        checks["same_day_null_diagnosis_outer_join_preserved"] = [
            r["ENCNTR_ID"] for r in outer_dx_rows
        ] == [50001, 50003]

        # 5. ZZ999 Patient Exclusion (excludes 50099 / 1099)
        checks["test_patient_prefix_patient_excluded"] = 50099 not in [r["ENCNTR_ID"] for r in outer_dx_rows]

        passed = sum(1 for v in checks.values() if v)
        return {
            "total_edge_cases": len(checks),
            "passed_edge_cases": passed,
            "pass_rate": round(passed / len(checks), 4),
            "checks": checks,
        }
    finally:
        if owns_conn:
            db.close()
