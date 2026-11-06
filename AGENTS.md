# Endexis (`endexis`)

Tenant-agnostic Millennium V500 Data Model interpreter, clinical forms/output resolver,
CCL (`.prg`) compiler, and Discern Analytics 2.0 (DA2) / Eclipse BIRT 4.4.2 (`.rptdesign` / `.dareport`) report generator.

- Chronology: [AGENTS/journal.md](AGENTS/journal.md).
- Legal & Interoperability Notice: [LEGAL.md](LEGAL.md).
- Tests: `python -m pytest -q`; zero external network calls, zero PHI, zero client or tenant-specific data.
- Release Pipeline & Audit: `python3 scripts/release_pipeline.py audit`.
- Strict Isolation: Never place PHI, MRNs, facility names, domain codes, or client-identifiable strings in code, catalogs, tests, or training seeds.
