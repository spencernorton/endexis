# Security Policy

## Reporting a Vulnerability

Report security vulnerabilities privately through [GitHub Security Advisories](https://github.com/spencernorton/endexis/security/advisories/new). Do not open a public issue for suspected vulnerabilities. There is no e-mail address; that is deliberate. Reports receive an acknowledgement within seven days. Only the latest tagged release is supported.

## Trust & Tenant-Agnostic Isolation Model

- **Zero PHI & Zero Client/Tenant Memorization.** Endexis is trained and verified exclusively on client-neutral, synthetic, and public schema metadata (`CWC_*`, `CWF_*`, `V500.*`).
- **Deterministic Verifier Gate.** Model outputs are treated as untrusted proposals and validated through deterministic XML, SQL, CCL, and Eclipse BIRT 4.4.2 static verifiers before emitting artifacts.
- **Explicit Runtime Tenant Configuration.** By default, Endexis emits `owner_group_display="TENANT_OWNER_GROUP"` and `timezone_index=54` with zero hardcoded `Logical Domain ID` predicates. Tenant-specific facility, building, nurse unit, room, bed, or filter overrides are injected strictly at runtime via `TenantConfig`.
