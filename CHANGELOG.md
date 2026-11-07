# Changelog

## 2.0.0 — 2026-11-06

### Added
- **Precision Column & DA2 Field Disambiguation (`strict_precision=True`)**: Intent-aware, grain-pruned column and `DA_ELEMENT` selection across 6 temporal anchor surfaces (`CLINICAL_EVENT.EVENT_END_DT_TM` vs. `VERIFIED_DT_TM` vs. `UPDT_DT_TM`; `MED_ADMIN_EVENT` administration start vs. `ORDERS.ORIG_ORDER_DT_TM`; `ENCOUNTER.INPATIENT_ADMIT_DT_TM` vs. `REG_DT_TM`/`ARRIVE_DT_TM`/`DISCH_DT_TM`; `SCH_APPT.BEG_DT_TM` vs. `ACTION_DT_TM`; `CHARGE.SERVICE_DT_TM` vs. `POSTED_DT_TM`), identifier alias disambiguation (`PERSON_ALIAS` MRN Code Set 4 vs. `ENCNTR_ALIAS` FIN Code Set 319 vs. internal `f8` surrogate keys), and multi-1:N child join-explosion guardrails.
- **Clinical & SQL Safety Qualification Filters**: Automatic `Order Status Not In (Canceled, Voided)` (`Code Set 6004: 2542, 2553`) preserving `Discontinued` (`2545`) for historical MAR/BCMA audits, `CE Result Status In (Auth (Verified), Modified, Altered)` (`Code Set 8: 25, 34, 35`) with `VALID_UNTIL_DT_TM >= 31-DEC-2100`, `LEFT OUTER JOIN V500.DIAGNOSIS` for active census admitting diagnosis queries, and test-patient prefix exclusion (`< 'ZZ'`).
- **Context-Conditioned Filter-Value & Code Set Resolver (`resolve_da2_filter_value`)**: Scores `6,458` `DISPLAY <-> CODE_VALUE` pairs across `90` DA2 filter fields conditioned on `(query_intent, domain, subentity, da2_field_name, code_set)`.
- **Zero-Leakage V2 Adversarial Benchmark & SQLite Edge-Case Harness**: 20-scenario V2 adversarial benchmark (`data/eval_adversarial_v2.jsonl`, `evaluate_adversarial_v2_benchmark`: `100.0%` `Pass@1`, mean F1 `0.995`) and 5-scenario SQLite V500 edge-case execution suite (`run_adversarial_sql_edge_case_suite`).
- **Expanded Catalogs & Retrained Stage-2 RPO-DPO Model (`NorviTech/endexis-qwen3-0.6b-dpo-merged`)**: Enriched all `2,606` DA2 elements and `230` V500 tables, rebuilt `4,501` 6-step `<reasoning>` SFT examples and `189` contrastive DPO pairs, and retrained BF16 `.safetensors` + `Q8_0` (`610 MB`) and `Q4_K_M` (`379 MB`) GGUF checkpoints (`val_perplexity = 1.2262`, `dpo_mean_logprob_margin = +23.75`).

## 1.1.0 — 2026-11-06

### Added
- Portable `Q8_0` and `Q4_K_M` GGUF model support (`LocalNeuralCompiler` OpenAI-compatible `/v1/chat/completions` fallback) and `Ollama` `Modelfile`.
- Declarative `.endexis.toml` tenant configuration (`endexis init` and `endexis init-discover <code_set_220.csv>`).
- In-memory synthetic V500 SQLite execution harness (`discern_intelligence.sql_execution_harness`, `endexis verify-sql`).

## 1.0.0 — 2026-11-06

### Added
- Initial public release of **Endexis** (`endexis` v1.0.0) and the merged **Qwen3-0.6B** Stage-2 RPO-DPO neural compiler (`NorviTech/endexis-qwen3-0.6b-dpo-merged`).
- Tenant-agnostic **Discern Analytics 2.0 (DA2)** `.daquery` and `.dareport` XML/SQL generator with runtime `TenantConfig` overrides (`owner_group_display`, `timezone_index`, `Enc Loc Facility`, `Enc Loc Building`, `Enc Loc Nurse Unit`, `Enc Loc Room`, `Enc Loc Bed`, and custom filter overrides).
- **Cerner Command Language (CCL)** `.prg` compiler and **Eclipse BIRT 4.4.2** `.rptdesign` multi-dataset join generator.
- 13 client-neutral V500, DA2, Code Set (`363` code sets), Event Set/Language crosswalk, PowerForm/IView/SmartTemplate, and HealtheIntent EDW reference catalogs (`2,600` DA2 elements, `6,458` coded value mappings across `90` fields).
- 4-Gate Public Release Auditor (`discern_intelligence.release_audit`) verifying repository/corpus text, tenant-agnostic structural invariants, binary `.safetensors` headers/strings, and adversarial behavioral canary prompts.
