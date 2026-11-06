# Contributing to Endexis

Contributions are welcome via GitHub pull requests against `main`.

## Development & Verification

1. Install the package and test dependencies:
   ```bash
   python -m pip install -e ".[dev]"
   ```
2. Run the full unit, structural, and release-audit test suite:
   ```bash
   python -m pytest -q
   python scripts/release_pipeline.py audit
   ```
3. Ensure zero client, regional, domain, or PHI identifiers are introduced into any source file, catalog, or test fixture.
