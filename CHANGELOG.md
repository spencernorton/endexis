# Changelog

## 1.0.0 — 2026-11-06

### Added
- Initial public release of **Endexis** (`endexis` v1.0.0) and the merged **Qwen3-0.6B** Stage-2 RPO-DPO neural compiler (`NorviTech/endexis-qwen3-0.6b-dpo-merged`).
- Tenant-agnostic **Discern Analytics 2.0 (DA2)** `.daquery` and `.dareport` XML/SQL generator with runtime `TenantConfig` overrides (`owner_group_display`, `timezone_index`, `Enc Loc Facility`, `Enc Loc Building`, `Enc Loc Nurse Unit`, `Enc Loc Room`, `Enc Loc Bed`, and custom filter overrides).
- **Cerner Command Language (CCL)** `.prg` compiler and **Eclipse BIRT 4.4.2** `.rptdesign` multi-dataset join generator.
- 13 client-neutral V500, DA2, Code Set (`363` code sets), Event Set/Language crosswalk, PowerForm/IView/SmartTemplate, and HealtheIntent EDW reference catalogs (`2,600` DA2 elements, `6,458` coded value mappings across `90` fields).
- 4-Gate Public Release Auditor (`discern_intelligence.release_audit`) verifying repository/corpus text, tenant-agnostic structural invariants, binary `.safetensors` headers/strings, and adversarial behavioral canary prompts.
