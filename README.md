# Endexis

A tenant-agnostic SQL, CCL and report compiler for Oracle Health analytics.

[![CI](https://github.com/spencernorton/endexis/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/spencernorton/endexis/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/spencernorton/endexis?label=release)](https://github.com/spencernorton/endexis/releases)
[![Apache-2.0 licence](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)
[![Hugging Face model](https://img.shields.io/badge/Hugging_Face-model-FFD21E.svg)](https://huggingface.co/NorviTech/endexis-qwen3-0.6b-dpo-merged)

Endexis translates reporting requests into Cerner Command Language (CCL), Discern
Analytics 2.0 (DA2) queries and Eclipse BIRT 4.4.2 reports. It maps requests to
Cerner Millennium V500 tables and coded values, resolves clinical forms and
outputs, and checks the generated artifacts with deterministic verifiers. The
command-line compiler runs locally. An optional Qwen3 model can propose candidates
for the same verifiers.

![Endexis workflow from clinical forms through CCL and DA2 report generation](docs/da2_v1_forms_ccl_and_rptdesign_pipeline.png)

## Get started

Use Python 3.11 or later. Install the [current release](https://github.com/spencernorton/endexis/releases/latest)
from source in a virtual environment.

```sh
git clone --branch v1.0.0 https://github.com/spencernorton/endexis.git
cd endexis
python -m venv .venv
. .venv/bin/activate
python -m pip install .
endexis --help
```

Try the schema interpreter or compile a synthetic reporting request.

```sh
endexis interpret "Show active inpatient encounters with a Morse Fall Scale score"
endexis compile-ccl "Active inpatient encounters with charted Morse Fall Scale results" \
  --name cust_r_morse_fall_v1 \
  --out morse_fall.prg
```

## What you can build

| Goal | Command | Output |
| --- | --- | --- |
| Find fields for a reporting request | `interpret` | V500 tables, columns, code sets and DA2 elements |
| Trace how forms and results are stored | `forms` | Reference tables, activity bridges and result fields |
| Compile a CCL report | `compile-ccl` | Verified `.prg` source |
| Build a DA2 query | `generate-query` | DA2 query XML and Oracle V500 SQL |
| Generate a DA2 report | `generate-da2` | BIRT `.rptdesign` plus optional DA2 query or report bundles |

The [compiler reference](docs/compiler-reference.md) covers the output formats,
clinical form mappings, source catalogs and tenant configuration. The
[model card](MODEL_CARD.md) covers the separate neural model and its evaluation.
Validate generated artifacts against the schema and permissions of the target
DA2 environment before uploading them.

## Project links

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Support](SUPPORT.md) ·
[Security](SECURITY.md) · [Legal and interoperability notice](LEGAL.md)

---

<p align="center">
  <a href="https://norvitech.com"><img alt="Part of the NorviTech Suite" src="https://norvitech.com/assets/banner.svg" width="640"></a>
</p>

<p align="center">
  <a href="https://github.com/spencernorton/helios">Helios</a> ·
  <a href="https://github.com/spencernorton/DH-Waypoint">DH-Waypoint</a> ·
  <a href="https://github.com/spencernorton/armiger">Armiger</a> ·
  <a href="https://github.com/spencernorton/arrmes">Arrmes</a> ·
  <a href="https://github.com/spencernorton/endexis">Endexis</a> ·
  <a href="https://github.com/spencernorton/xnote">XNote</a> ·
  <a href="https://github.com/spencernorton/xnote-placement">XNote Placement</a> ·
  <a href="https://github.com/spencernorton/snipsnap">SnipSnap</a> ·
  <a href="https://github.com/spencernorton/conductor">Conductor</a> ·
  <a href="https://github.com/spencernorton/norvi-os">NorviOS</a> ·
  <a href="https://github.com/spencernorton/indigo">Indigo</a> ·
  <a href="https://github.com/spencernorton/roadtrack">Road Track</a> ·
  <a href="https://norvitech.com">norvitech.com</a>
</p>
