# Changelog

## 2.3.0 — 2026-11-12

### Added
- **Structured & Natural-Language Multi-Turn Refinements (`RefinementSpec`, `NeuralCompilationIR`, `compile_from_neural_ir`)**: Added negative, prior-context, and override editing (`remove_columns` / `exclude_columns`, `remove_filters` / `exclude_filters`, `prior_filters`, `replace_filter_value` / `override_filters` across the 95-field `da2_filter_value_catalog`) propagated in lockstep across `<cclquery version="1.0">` XML, `.daquery` (`xmlversion="2"`), EJS V500 Oracle SQL, CCL `.prg` scripts, and Eclipse BIRT 4.4.2 `.rptdesign` groupings.
- **Sealed Post-Freeze Qualification Holdout (`data/eval_postfreeze_v3.jsonl`, `N = 640`) & 5-Fold Template-Family GroupKFold OOF (`data/oof_eval_v2_3.json`, `N = 132`)**: Added zero-overlap (`0` exact, `0` token-bag, `0` normalized 5-gram skeleton overlap; `19` generic boilerplate surface 5-grams) evaluation suites verified end-to-end against SQLite V500 execution, BIRT 4.4.2 XML DOM/UUID checks, and CCL static verification, alongside strict zero-rescue `Track A` (`compile_track_a_raw_ir`) and separated `Track B` (`raw_neural_ir`, `symbolically_repaired_ir`, `symbolic_fallback`) evaluation tracks.
- **14x Corpus Retraining & Calibrated NLL-Anchored RPO-DPO (`NorviTech/endexis-qwen3-0.6b-dpo-merged` `v2.3.0`)**: Cleaned all `4,509` SFT rows (`0` placeholder business-view UUIDs, `124` synthetic `UUIDv5` -> live `CWC Encounter` `UUIDv4` upgrades), added `768` verified `compile_nl_to_neural_ir` single-turn, collision-guard, and multi-turn examples (`5,277` total SFT rows), expanded `data/dpo_contrastive_v1.jsonl` (`192 -> 648` contrastive preference pairs across 4 failure families), trained Stage-1 BF16 LoRA SFT + Stage-2 NLL-anchored RPO-DPO (`beta = 0.10`, `lambda_nll = 0.15`), exported standalone BF16 `safetensors` with a clean non-thinking ChatML `chat_template`, and quantized `Q8_0` (`99.24%` CUDA `BF16` parity) and `Q4_K_M` (`191.81 ms` p50) GGUF artifacts.

### Fixed
- **Clinical-vs-Billing Concept Isolation, Base64-Free Release Auditing & Strict Schema Validation (`lexical_guards.py`, `release_audit.py`, `sanitize_catalogs.py`, `schemas.py`, `da2_generator.py`, `ccl_compiler.py`)**: Eliminated clinical `"attending physician"` / `"ordering physician"` / `"provider"` injection of `Adj Post Prsnl`, `Prsnl Position`, `Adj Trans Sub Type`, `Adj Trans Reason`, and `Adj Post Dt`; isolated `"discharged"` from `CHARGE`, clinical `"dose/rate adjustment"` from `CWF GS Adjustments`, `"also"` from `Enc LOS`, `"borderline"`/`"disorder"` from `ORDERS`, and pronoun `"for him"` from `HIM`; preserved affirmative clauses trailing negated clauses (`"without billing columns and include LOS"`); removed reversible Base64 private blobs from `release_audit.py` and `sanitize_catalogs.py` while adding decoded Base64 literal auditing; and sanitized `_derive_report_name()` against conversational extras.

## 2.2.2 — 2026-11-11

### Added
- Added `DA2ReportGenerator.generate(like_filters={field: pattern})`, `LIKE_TEXT_FIELDS` (`14` verified free-text `CWC Encounter` fields), and `like_pattern()` for native DA2 `Is Like` (`.daquery` `<operator type="LIKE"/>`) qualifications, normalizing `Person Name Last` against `CWC_PERSON.NAME_LAST_KEY` and listing `.daquery`-only predicates in `DA2UploadableBundle.value_filters_not_in_reference`.

## 2.2.1 — 2026-11-11

### Added
- Added `DA2ReportGenerator.generate(value_filters={field: [display values]})` and `CCLCompiler.compile(value_filters=[...])` (`CompiledCCLProgram.value_filters_applied`, `DA2UploadableBundle.value_filters_not_in_reference`) for verified `IN`-list code filters across `.daquery`, `<cclquery>`, EJS V500 SQL, and CCL `PLAN`/`JOIN` blocks, plus word-start form matching (`_mentions()`) and whole-word `MAR`/`MARS` token checks.

## 2.2.0 — 2026-11-08

### Added
- Expanded canonical DA2 report blueprints from `29` to `36` (`Bathing_Hygiene_Documentation_Report`, `Inpatient_Nutrition_Assessment_Report`, `Pharmacy_Clinical_Interventions_Report`, `Peripheral_Central_IV_Catheter_Report`, `Inpatient_Precaution_Orders_Report`, `One_To_One_Observation_Orders_Report`, `Wound_Care_Consult_Orders_Report`, and `Diet_And_Nutrition_Orders_Report`).
- Expanded `da2_filter_value_catalog.json` to `6,678` coded value pairs across `CE Event` (Code Set 72), `Order Catalog` (Code Set 200), and `HP E R Verify Status` (Code Set 27137), and added `bench-receipt/v1` chart export (`scripts/export_bench_receipt.py`).

### Fixed
- Added targeted `Order Catalog` and `Order Catalog Type` EJS V500 SQL predicates, preserved MAR/task elements across medication-audit aliases, and isolated `"Suicide Precautions"` order audits from C-SSRS 7-query inline subqueries.

## 2.1.0 — 2026-11-07

### Added
- Added `Oral_Care_Documentation_Report` (`21` verified Code Set 72 `CE Event` mappings) and `Bladder_Bowel_Management_Report` (`55` verified Code Set 72 `CE Event` mappings), expanding `CE Event` mappings by `201` entries (`2,331 -> 2,532`), plus generic `LTCA <MNEMONIC>` owner-group resolution.

## 2.0.2 — 2026-11-07

### Fixed
- Promoted `236` live-verified DA2 element `UUIDv4`s into `da2_element_catalog.json`, expanded `LIVE_CWC_ELEMENT_MAP` to `377` verified `CWC Encounter` `UUIDv4` entries, and enforced `DA2_VERIFIED_DOMAIN_ELEMENT_UUIDS` in `.daquery` generation and bundle verification to prevent `ColumnDomainException` on upload.

## 2.0.1 — 2026-11-07

### Fixed
- Upgraded `DA2ReportGenerator._derive_report_name()` to synthesize domain-standard report identifiers, aligned scheduling `CONCEPT_SIGNALS` 1:1 with DA2 elements, and deduplicated appointment summary projections.

## 2.0.0 — 2026-11-06

### Added
- Precision temporal anchor and MRN/FIN/surrogate-key disambiguation (`strict_precision=True`), clinical MAR/CE qualification safety predicates, context-conditioned `resolve_da2_filter_value()`, enriched `2,606`-element and `230`-table V500 catalogs, and the 20-scenario V2 adversarial benchmark (`data/eval_adversarial_v2.jsonl`).

## 1.1.0 — 2026-11-06

### Added
- Portable `Q8_0` and `Q4_K_M` GGUF quantization support, OpenAI-compatible `/v1/chat/completions` client routing in `LocalNeuralCompiler`, `.endexis.toml` declarative tenant profile discovery (`endexis init`, `endexis init-discover`), and the synthetic SQLite V500 execution harness (`discern_intelligence.sql_execution_harness`).

## 1.0.0 — 2026-11-06

### Added
- Initial public release of **Endexis** (`endexis` v1.0.0) and `NorviTech/endexis-qwen3-0.6b-dpo-merged` with Millennium V500 interpretation, 13 clinical form/output architectures, CCL `.prg` compilation, DA2 `.daquery`/`.dareport`/BIRT 4.4.2 `.rptdesign` generation, and the 4-gate `release_audit` security/trademark auditor.
