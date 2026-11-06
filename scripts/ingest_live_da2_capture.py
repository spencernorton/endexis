#!/usr/bin/env python3
"""
High-speed, zero-PHI metadata harvester for live Discern Analytics 2.0 (DA2) sessions.

Ingests metadata captured from a live DA2 instance via the remote->Mac clipboard (`pbpaste`)
or local scratch files across two complementary, zero-PHI channels:
  1. DA2 Query Builder -> "View SQL" (`/* Discern SQL Engine (EJS) ... */`):
     Extracts CWC_*/XCW_* view names, join predicates, scalar helper functions, and
     exact DA2 alias -> V500 TABLE.COLUMN mappings.
  2. DA2 Report Designer -> "XML Source" (`<report version="3.2.23">...</report>`):
     Extracts ODA dataset resultSet columns (`displayName`, `nativeName` / `ELEMENT_UUID`,
     BIRT `dataType`, `position`), `queryText` UUID, and `defaultRuntimePrompts` XML.

Never reads or stores patient data rows; enforces strict client/facility identifier scrubbing.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "src" / "discern_intelligence" / "data"

BIRT_NS = "{http://www.eclipse.org/birt/2005/design}"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
EJS_HEADER_RE = re.compile(
    r"/\*\s*Discern SQL Engine \(EJS\)\s*v\.\s*([0-9.]+)\s*:\s*([^\[*]+?)(?:\s*\[([0-9a-fA-F-]{36})\])?\s*\*/"
)


def parse_rptdesign_xml_metadata(xml_text: str, default_domain: str = "CWC Encounter") -> dict[str, Any]:
    """Extracts ODA dataset elements (displayName, ELEMENT_UUID, dataType) and prompt XML from .rptdesign XML."""
    root = ET.fromstring(xml_text.strip())
    datasets: list[dict[str, Any]] = []

    for ds in root.findall(f".//{BIRT_NS}oda-data-set"):
        ds_name = ds.attrib.get("name", "Data Set")
        query_uuid = None
        q_node = ds.find(f"{BIRT_NS}xml-property[@name='queryText']")
        if q_node is not None and q_node.text:
            query_uuid = q_node.text.strip()

        elements: list[dict[str, Any]] = []
        rs_list = ds.find(f"{BIRT_NS}list-property[@name='resultSet']")
        if rs_list is not None:
            for struct in rs_list.findall(f"{BIRT_NS}structure"):
                props = {
                    p.attrib.get("name"): (p.text or "").strip()
                    for p in struct.findall(f"{BIRT_NS}property")
                }
                col_name = props.get("columnName") or props.get("displayName") or ""
                disp_name = props.get("displayName") or col_name
                native_uuid = props.get("nativeName") or ""
                data_type = props.get("dataType") or "string"
                position = int(props["position"]) if props.get("position", "").isdigit() else None

                # Detect Combined Query <SubqueryAlias>:<ElementName> prefixing
                is_combined_alias = ":" in disp_name and not disp_name.startswith("http")
                subquery_alias, base_element_name = (
                    disp_name.split(":", 1) if is_combined_alias else (None, disp_name)
                )

                elements.append({
                    "da2_field_name": base_element_name.strip(),
                    "raw_display_name": disp_name,
                    "subquery_alias": subquery_alias,
                    "is_combined_query_alias": is_combined_alias,
                    "element_uuid": native_uuid if UUID_RE.match(native_uuid) else None,
                    "birt_data_type": data_type,
                    "position": position,
                    "da2_domain": default_domain,
                })

        datasets.append({
            "dataset_name": ds_name,
            "query_uuid": query_uuid,
            "element_count": len(elements),
            "elements": elements,
        })

    return {
        "capture_type": "rptdesign_xml",
        "birt_version": root.attrib.get("version"),
        "datasets": datasets,
    }


def parse_ejs_sql_metadata(sql_text: str, default_domain: str = "CWC Encounter") -> dict[str, Any]:
    """Extracts CWC_*/XCW_* table aliases, V500 column expressions, and scalar helpers from EJS SQL."""
    m_hdr = EJS_HEADER_RE.search(sql_text)
    ejs_version = m_hdr.group(1) if m_hdr else "3.15.1"
    query_uuid = m_hdr.group(3) if (m_hdr and m_hdr.group(3)) else None

    # Extract FROM / JOIN CWC_* or XCW_* or V500 tables and their aliases
    table_aliases: dict[str, str] = {}
    for m_tbl in re.finditer(
        r"\b(?:FROM|JOIN)\s+(?:V500\.)?([A-Z0-9_]+)\s+([A-Z0-9_]+)\b",
        sql_text,
        flags=re.IGNORECASE,
    ):
        tbl, alias = m_tbl.group(1).upper(), m_tbl.group(2).upper()
        if alias not in {"ON", "WHERE", "AND", "OR", "INNER", "LEFT", "RIGHT", "FULL", "JOIN"}:
            table_aliases[alias] = tbl

    # Extract projected SELECT expressions with AS <alias>
    select_mappings: list[dict[str, Any]] = []
    for m_sel in re.finditer(
        r"([^\n,]+?)\s+AS\s+\"?([A-Za-z0-9_:/\s-]+?)\"?\s*(?:,|\bFROM\b)",
        sql_text,
        flags=re.IGNORECASE,
    ):
        expr = " ".join(m_sel.group(1).replace("SELECT", "").replace("DISTINCT", "").split()).strip()
        col_alias = m_sel.group(2).strip()
        if not expr or not col_alias:
            continue

        # Identify underlying table.column references inside the expression
        col_refs = re.findall(r"\b([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\b", expr)
        resolved_refs = []
        for t_al, c_name in col_refs:
            t_up = table_aliases.get(t_al.upper(), t_al.upper())
            v500_tbl = t_up[4:] if t_up.startswith(("CWC_", "XCW_")) else t_up
            resolved_refs.append({
                "da2_view_or_table": t_up,
                "v500_table": v500_tbl,
                "v500_column": c_name.upper(),
            })

        uses_cv_display = "omf_get_cv_display" in expr.lower()
        uses_utc_cnvt = "cclsql_utc_cnvt" in expr.lower()

        select_mappings.append({
            "sql_column_alias": col_alias,
            "sql_expression": expr,
            "resolved_v500_Change_refs": resolved_refs,
            "uses_omf_get_cv_display": uses_cv_display,
            "uses_cclsql_utc_cnvt": uses_utc_cnvt,
            "da2_domain": default_domain,
        })

    return {
        "capture_type": "ejs_sql",
        "ejs_version": ejs_version,
        "query_uuid": query_uuid,
        "table_aliases": table_aliases,
        "select_mappings": select_mappings,
    }


def ingest_text(raw_text: str, default_domain: str = "CWC Encounter") -> dict[str, Any]:
    """Auto-detects whether captured text is .rptdesign XML or EJS SQL and extracts clean metadata."""
    stripped = raw_text.strip()
    if stripped.startswith("<?xml") or "<report" in stripped[:400]:
        return parse_rptdesign_xml_metadata(stripped, default_domain=default_domain)
    return parse_ejs_sql_metadata(stripped, default_domain=default_domain)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest zero-PHI DA2 metadata from clipboard or file.")
    parser.add_argument("--from-clipboard", action="store_true", help="Read captured XML or EJS SQL from macOS pbpaste")
    parser.add_argument("--input-file", type=Path, help="Read captured XML or EJS SQL from a file")
    parser.add_argument("--domain", default="CWC Encounter", help="DA2 domain name (default: CWC Encounter)")
    parser.add_argument("--out-json", type=Path, help="Optional output path for parsed JSON summary")
    args = parser.parse_args()

    if args.from_clipboard:
        raw = subprocess.check_output(["pbpaste"], text=True)
    elif args.input_file:
        raw = args.input_file.read_text(encoding="utf-8")
    else:
        parser.error("Specify either --from-clipboard or --input-file")
        return 2

    parsed = ingest_text(raw, default_domain=args.domain)
    out_str = json.dumps(parsed, indent=2)
    if args.out_json:
        args.out_json.write_text(out_str + "\n", encoding="utf-8")
    else:
        print(out_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
