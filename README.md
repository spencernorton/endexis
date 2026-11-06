# Endexis (`endexis`) — Tenant-Agnostic Clinical Data Model, DA2, CCL & BIRT 4.4.2 Compiler

**Endexis** (`endexis`, *EHR Neural Data Extraction, Inquiry & XML Intelligence System*) is a specialized clinical data model interpreter, form/output resolver, **Cerner Command Language (CCL)** compiler, and **Discern Analytics 2.0 (DA2)** / **Eclipse BIRT 4.4.2 (`.rptdesign`)** generator for **Cerner Millennium** (`V500`), **HealtheIntent** (`millennium-ods/v1`), and **Oracle Health EHI / Semantic Object Model (`v2026.4.01`)**.

![V1 Forms, CCL, and DA2 BIRT Pipeline](docs/da2_v1_forms_ccl_and_rptdesign_pipeline.png)

## Four Core V1 Capabilities

1. **Millennium V500 Data Model Interpreter (`discern_intelligence.interpreter`)**
   - Translates natural-language clinical, operational, and financial requests into exact **Cerner Millennium V500** tables and columns (`PERSON`, `ENCOUNTER`, `ENCNTR_ALIAS`, `CLINICAL_EVENT`, `ORDERS`, `ORDER_DETAIL`, `DCP_FORMS_ACTIVITY`, `CE_INTAKE_OUTPUT_RESULT`, `CE_BLOB_RESULT`, `CHARGE`, `PFT_ENCNTR`, etc.).
   - Classifies every resolved column by its physical **CCL data type** (`f8` numeric/surrogate key/code value, `dq8` timestamp, `i4` indicator/flag, `vc` variable character/RTF/HTML blob) and semantic role (`surrogate_primary_or_foreign_key`, `code_value_foreign_key`, `timestamp_utc_or_local`, `boolean_indicator`, `enumeration_flag`, `free_text_or_display_value`, `numeric_quantity_or_measurement`, `compressed_or_long_blob`).
   - Resolves exact **Code Sets (`CODE_SET`)** and `CDF_MEANING` constants (`Code Set 4` MRN, `Code Set 319` FINNbr, `Code Set 71` Encounter Type, `Code Set 8` Result Auth Status, `Code Set 6004` Order Status, `Code Set 72` Clinical Event Code, `Code Set 14003` Discrete Task Assay).
   - Evaluates **HealtheIntent `millennium-ods/v1` parity** and routes execution across **`CommunityWorks`**, **`Continuum`**, and **`Standalone_RHO`**.

2. **Clinical Forms & Output Data Resolver (`discern_intelligence.forms_mapper`)**
   - Maps 13 distinct **Cerner Millennium** clinical and financial documentation architectures from their definition/reference tables through activity bridges to exact payload columns and **DA2** elements:
     - **PowerForms & Clinical Assessments**: `DCP_FORMS_REF` -> `DCP_FORMS_DEF` -> `DCP_SECTION_REF` -> `DCP_INPUT_REF` (`MERGE_UID` -> `DISCRETE_TASK_ASSAY.TASK_ASSAY_CD`), activity in `DCP_FORMS_ACTIVITY` -> `DCP_FORMS_ACTIVITY_COMP` (`PARENT_ENTITY_ID = EVENT_ID`), and 3-tier `CLINICAL_EVENT` hierarchy (`Form` -> `Section` -> `DTA`) with typed answers in `CLINICAL_EVENT.RESULT_VAL`, `CE_CODED_RESULT.NOMENCLATURE_ID`, `CE_DATE_RESULT.RESULT_DT_TM`, and `CE_STRING_RESULT.STRING_RESULT_TEXT`.
     - **Interactive View (IView) Flowsheets & Dynamic Groups**: `WORKING_VIEW` -> `WORKING_VIEW_SECTION` -> `WORKING_VIEW_ITEM`, dynamic instances (lines, drains, wounds) in `CE_DYNAMIC_LABEL`, and charted values in `CLINICAL_EVENT.RESULT_VAL`.
     - **Intake & Output (I&O 2G)**: `CE_INTAKE_OUTPUT_RESULT.IO_VOLUME` (mL), `IO_TYPE_FLAG` (`1` = Intake, `2` = Output), and `IO_START_DT_TM` / `IO_END_DT_TM`.
     - **Dynamic Documentation (PowerNotes) & Narrative RTF/HTML Blobs**: `SCD_STORY` -> `SCD_PARAGRAPH` -> `SCD_SENTENCE` -> `SCD_TERM` and `CE_BLOB_RESULT` -> `CE_BLOB.BLOB_CONTENTS` (handling `OCFCOMP` decompression via `uar_ocf_uncompress` when `COMPRESSION_CD` in Code Set 120 is compressed).
     - **Order Entry Formats (OEF) & Order Details**: `ORDER_ENTRY_FORMAT` -> `OE_FORMAT_FIELDS` -> `ORDER_ENTRY_FIELDS` (`OE_FIELD_ID`, `OE_FIELD_MEANING_ID`), clinician prompt responses in `ORDER_DETAIL.OE_FIELD_DISPLAY_VALUE` / `OE_FIELD_VALUE` / `OE_FIELD_DT_TM_VALUE`, and summary line in `ORDERS.CLINICAL_DISPLAY_LINE`.
     - **Additional Output Architectures**: CPDI Scanned Documents (`CDI_TRANS_LOG`, `CE_BLOB_RESULT`), MAR / BCMA (`CE_MED_RESULT`, `MED_ADMIN_EVENT`), Rehab Therapy Productivity & Charges (`CHARGE`, `CLINICAL_EVENT`), RevCycle / Patient Accounting (`PFT_ENCNTR`, `BO_HP_RELJN`, `ACCT_BALANCE`), Clinical Tasks (`TASK_ACTIVITY`), Scheduling (`SCH_APPT`, `SCH_EVENT`), PowerPlans (`PATHWAY`, `ACT_PW_COMP`), and Radiology/Pathology Diagnostic Reports (`RAD_REPORT`, `CE_BLOB`).

3. **Request-to-CCL Compiler (`discern_intelligence.ccl_compiler`)**
   - Compiles natural-language analytical specifications into complete, executable `.prg` **Cerner Command Language** programs (`DROP PROGRAM ... GO`, `CREATE PROGRAM ...`, `PROMPT "Output to File/Printer/MINE" = "MINE"`, `DECLARE c_*_cd = f8 WITH protect, constant(uar_get_code_by("MEANING", ...))`, `SELECT INTO $OUTDEV`, explicit `PLAN` / `JOIN` order, and row-versioning `ACTIVE_IND = 1` + `END_EFFECTIVE_DT_TM > cnvtdatetime(curdate, curtime3)`).
   - Includes a deterministic static verifier (`verify_ccl_source`) that checks header/footer parity, zero hardcoded `CODE_VALUE` literals, row-versioning predicates, and `uar_get_code_display()` projection.

4. **Direct-Upload DA2 Query & Eclipse BIRT 4.4.2 `.rptdesign` Generator (`discern_intelligence.da2_generator`)**
   - Produces complete, direct-upload **Eclipse BIRT 4.4.2** (`version="3.2.23"`) `.rptdesign` XML files paired with **Discern Analytics 2.0** ODA metadata (`extensionID="com.cerner.discern.connectivity.oda"` and `com.cerner.discern.connectivity.oda.dataSet`).
   - Populates `<property name="com.cerner.discern.da2.display.names.set">1</property>`, `<structure name="cachedMetaData">`, `<list-property name="resultSet">` with exact `DA_ELEMENT.ELEMENT_UUID` mappings, `<xml-property name="queryText">`, and `<ex-property><name>defaultRuntimePrompts</name>` XML.
   - Supports both single-domain ODA reports and multi-domain `<joint-data-set>` reports joined on `Enc Id` (`ENCNTR_ID`), with `try { ... } catch (e) {}` guards around `reportContext` calls inside `beforeOpen` and `onFetch` so DA2's design-time `__bm_beforeOpen()` validator accepts the upload without errors.

## Installation & Quick Start

```bash
pip install -e ".[dev]"
pytest -v
```

### CLI Commands (`endexis`)

```bash
# 1. Interpret a natural-language request into V500 TABLE.COLUMN items and DA2 elements
endexis interpret "Show active inpatient encounters with Morse Fall Scale score, FIN, MRN, and Nurse Unit" --tenancy CommunityWorks

# 2. Resolve clinical form or output storage (definition tables, activity tables, payload columns)
endexis forms "Where are PowerForm discrete assessment values and IView dynamic group flowsheet cells stored?"

# 3. Compile a request into a verified Cerner Command Language (.prg) script
endexis compile-ccl "Active inpatient encounters with charted Morse Fall Scale PowerForm results" \
  --program-name cust_r_morse_fall_v1 \
  --output examples/cust_r_active_morse_v1.prg

# 4. Generate a direct-upload DA2 .rptdesign XML file and DA2 Domain Query JSON specification
endexis generate-da2 "Active inpatient encounter census with FIN, MRN, Nurse Unit, Attending Physician, and Admit Date" \
  --title "Active Inpatient Encounter Census" \
  --output-rptdesign examples/active_inpatient_census_v1.rptdesign \
  --output-spec examples/active_inpatient_census_v1_query_spec.json
```

## Knowledge Base & SFT / DPO Training Corpora

- `src/discern_intelligence/data/v500_high_value_tables.json`: **214** curated **Cerner Millennium V500** clinical, financial, form, output, event-set, and `DA_*`/`CCL_*`/`DO_*` internal metadata tables with full column definitions, CCL data types, `CODE_SET` foreign keys, and **HealtheIntent `millennium-ods/v1`** export flags (including all 24 backing V500 tables for the 48 `XCW_*` DA2 views plus `LOGICAL_DOMAIN` and `MLTM_NDC_MAIN_DRUG_CODE`).
- `src/discern_intelligence/data/da2_view_alias_join_catalog.json`: **48** direct **Discern Analytics 2.0** `XCW_*` view/alias/join-key mappings (`24 ENCOUNTER_ID`, `23 PERSON_ID`, `1 ORG_ID`) + **9** `(via ...)` relational-path variants (`57` rows total, kept strictly separate from raw `V500` table names, with `XCW_PHONE (via Organization)` validated and corrected to `ORG_ID`), plus **Discern SQL Engine (EJS v3.15.1)** `CWC_*` aliases, scalar helper functions (`omf_get_cv_display`, `cclsql_utc_cnvt`, `PM_GET_ALIAS`, `PM_GET_ENCNTR_PRSNL_ID`, `SQL_GET_NAME_DISPLAY`, `RCRGETFINNBR`), and Combined Query `<SubqueryAlias>:<ElementName>` ODA result-set conventions.
- `src/discern_intelligence/data/event_set_hierarchy_and_crosswalks.json`: Validated 10-level `MD_D_EVENT_SET_HIERARCHY` SQL (`V500_EVENT_SET_CANON` `A..I` + `V500_EVENT_SET_CODE` + `V500_EVENT_SET_EXPLODE` rooted at `ALL OCF EVENT SETS`, plus the corrected 24-column orphan-event query), `MD_D_EVENT_SET_SEARCH` 10-way `UNION ALL` flattened search view (Vertica/Snowflake, Oracle RDB, and CCL), **229** Code Set 36 (`Language` / HL7 `PID-15` / Press Ganey code) mappings, **10** UB-04 FL-15 Admission Source (Code Set 2) HCAHPS mappings, **39** UB-04 FL-17 Discharge Status (Code Set 19) HCAHPS mappings, **13** regulatory IP/OP -> DA2 field mappings, **20** Hospital Bed Capacity extract elements, and the historical reference audit ledger (`added`, `already_covered`, `rejected`).
- `src/discern_intelligence/data/da2_element_catalog.json`: **2,471** sanitized **Discern Analytics 2.0** `DA_ELEMENT` entries with `ELEMENT_UUID`s across all 10 DA2 domains, V500 `TABLE.COLUMN` targets, and join-explosion guardrails.
- `src/discern_intelligence/data/da2_canonical_reports_and_queries_catalog.json`: **58** Business View (`<BV>` XML) Logical View schemas (`148` Categories), **21** canonical production DA2 queries (`<cclquery version="1.0">` XML + **EJS v3.17.3** Oracle V500 SQL) and Eclipse BIRT 4.4.2 (`.rptdesign`) reports.
- `src/discern_intelligence/data/da2_filter_value_catalog.json`: **61** priority-domain Qualification Filter Lists and **5,617** live-harvested `DISPLAY` <-> `CODE_VALUE` pairs across **80** DA2 fields (including **1,546** `CE Event` Code Set 72 and **1,004** `Order Catalog` Code Set 200 mappings).
- `src/discern_intelligence/data/forms_and_outputs_catalog.json`: **13** end-to-end clinical form and financial output architectures.
- `src/discern_intelligence/data/codeset_catalog.json`: **363** Millennium Code Sets (`CODE_SET`) with `CDF_MEANING` and `DISPLAY_KEY` constants (enriched with Code Sets `2`, `19`, `27`, `36`, and `282`).
- `src/discern_intelligence/data/oracle_som_summary.json`: **177** Oracle Health EHI / Semantic Object Model (`v2026.4.01`) entities.
- `data/sft_train_v1.jsonl`, `data/dpo_contrastive_v1.jsonl`, `data/eval_frozen_v1.jsonl`, `data/eval_holdout_filter_values_v1.jsonl`, and `data/eval_holdout_queries_v1.jsonl`: Reproducible zero-leakage 80/20 multi-task SFT (**3,637** examples), DPO contrastive (**40** pairs across 4 failure modes), frozen benchmark (**14** scenarios), and held-out evaluation (**140** filter values + **4** production queries) corpora generated via `python3 scripts/build_training_corpus.py --verify` and verified via `python3 scripts/evaluate_corpus_and_verifiers.py --verify`.

## Tenant-Agnostic Configuration (`--owner-group`, `--facility`, `--tenant-config`)

Endexis is completely tenant-agnostic across all **Discern Analytics 2.0 (DA2)** deployments (`CommunityWorks`, `Continuum`, and `Standalone_RHO`). By default, `.daquery` (`xmlversion="2"`) and `.dareport` (`.rpt` + `.rptdesign`) bundles populate `owner_group_display="TENANT_OWNER_GROUP"` and contain zero hardcoded `Logical Domain ID` or tenant-specific facility filters.

Specify your target tenant's DA2 owner group or facility via CLI flags or a JSON profile:

```bash
# Override DA2 owner_group_display and facility directly on the CLI:
endexis generate-da2 "Active inpatient encounter census with FIN, MRN, Nurse Unit, Attending Physician, and Admit Date" \
  --tenancy CommunityWorks \
  --owner-group "YOUR_DA2_OWNER_GROUP" \
  --facility "General Hospital Campus" \
  --output-dareport examples/active_inpatient_census.dareport

# Or pass a reusable tenant JSON profile:
endexis generate-da2 "Rehab therapy minutes by discipline" \
  --tenant-config tenant_profile.json \
  --output-dareport examples/rehab_therapy_minutes.dareport
```

See [LEGAL.md](LEGAL.md) for interoperability and trademark notices.
