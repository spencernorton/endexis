"""Loads and indexes the bundled V500, DA2, Code Set, Forms, View Alias, Event Set, and Tenancy catalogs."""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"


@functools.lru_cache(maxsize=1)
def load_v500_tables() -> dict[str, dict[str, Any]]:
    return json.loads((DATA_DIR / "v500_high_value_tables.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_codeset_catalog() -> dict[int, dict[str, Any]]:
    raw = json.loads((DATA_DIR / "codeset_catalog.json").read_text(encoding="utf-8"))
    return {int(item["code_set"]): item for item in raw}


@functools.lru_cache(maxsize=1)
def load_da2_elements() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "da2_element_catalog.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_forms_catalog() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "forms_and_outputs_catalog.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_som_summary() -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / "oracle_som_summary.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_tenancy_taxonomy() -> dict[str, dict[str, Any]]:
    raw = json.loads((DATA_DIR / "tenancy_taxonomy.json").read_text(encoding="utf-8"))
    return {env["id"]: env for env in raw.get("environments", [])}


@functools.lru_cache(maxsize=1)
def load_da2_view_alias_catalog() -> dict[str, Any]:
    return json.loads((DATA_DIR / "da2_view_alias_join_catalog.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_event_set_and_crosswalks() -> dict[str, Any]:
    return json.loads((DATA_DIR / "event_set_hierarchy_and_crosswalks.json").read_text(encoding="utf-8"))


def resolve_da2_view_or_alias(token: str) -> dict[str, Any] | None:
    """Resolves a DA2 XCW_* view name, DA2 alias, CWC_* EJS alias, or abbreviation to its validated mapping."""
    cat = load_da2_view_alias_catalog()
    q = token.strip().upper()
    if q.startswith("CWC_"):
        q_xcw = "XCW_" + q[4:]
    else:
        q_xcw = q
    for entry in cat.get("da2_views", []) + cat.get("relational_path_variants", []):
        if (
            entry["da2_view_entry"].upper() == q
            or entry["da2_view_name"].upper() == q
            or entry["da2_view_name"].upper() == q_xcw
            or entry["da2_alias"].upper() == q
            or (entry.get("da2_abbreviation") and entry["da2_abbreviation"].upper() == q)
        ):
            return entry
    return None


def lookup_language_crosswalk(query: str | int) -> list[dict[str, Any]]:
    """Looks up Millennium Code Set 36 (Language) / HL7 PID-15 / Press Ganey language code mappings."""
    cat = load_event_set_and_crosswalks()
    entries = cat.get("terminology_crosswalks", {}).get("code_set_36_language_crosswalk", {}).get("entries", [])
    if isinstance(query, int) or (isinstance(query, str) and query.strip().isdigit()):
        code = int(query)
        return [e for e in entries if e["press_ganey_language_code"] == code]
    q_low = str(query).strip().lower()
    return [
        e for e in entries
        if q_low in e["millennium_display"].lower() or q_low == e["millennium_display_key"].lower()
    ]


@functools.lru_cache(maxsize=1)
def load_da2_domain_taxonomy() -> dict[str, Any]:
    """Loads the verified 10-domain Discern Analytics 2.0 (DA2) taxonomy and sub-entity catalog."""
    return json.loads((DATA_DIR / "da2_domain_taxonomy_catalog.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_da2_filter_value_catalog() -> dict[str, Any]:
    """Loads the DA2 Qualification Filter List value catalog across the 6 priority domains and CE Event (Code Set 72) taxonomy."""
    return json.loads((DATA_DIR / "da2_filter_value_catalog.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_healtheintent_edw_catalog() -> dict[str, Any]:
    """Loads the 16 HealtheIntent EDW/Analytics feed schemas and 4 Oracle Health terminology/API specifications."""
    return json.loads((DATA_DIR / "healtheintent_edw_feeds_catalog.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def load_da2_canonical_reports_catalog() -> dict[str, Any]:
    """Loads the canonical DA2 Queries (<cclquery> XML + EJS v3.17.3 V500 SQL), BIRT (.rptdesign) Reports, and 36 Business View (<BV> XML) schemas."""
    return json.loads((DATA_DIR / "da2_canonical_reports_and_queries_catalog.json").read_text(encoding="utf-8"))


def resolve_da2_filter_value(
    value_or_concept: str,
    domain: str | None = None,
    subentity: str | None = None,
    code_set: int | None = None,
    code_value: str | int | None = None,
    tenant_config: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Resolves a specific clinical or financial value (or substring) to the exact DA2 Qualification
    Filter List field(s) (including CE Event Code Set 72 PowerChart values and live <cclquery> CODE_VALUE maps)
    ranked by exact value match > prefix match > substring match > field-name match.
    """
    cat = load_da2_filter_value_catalog()
    q = value_or_concept.strip().lower()
    if not q:
        return []
    dom_low = domain.strip().lower() if domain else None
    scored_matches: list[tuple[float, dict[str, Any]]] = []

    for fl in cat.get("qualification_filter_lists", []):
        if dom_low:
            all_doms = [fl["da2_domain"].lower()] + [d.lower() for d in fl.get("also_in_domains", [])]
            if not any(dom_low in d for d in all_doms):
                continue
        ovr_map = tenant_config.get_location_overrides(fl["da2_field_name"]) if tenant_config is not None and hasattr(tenant_config, "get_location_overrides") else {}
        vals = list(dict.fromkeys(list(ovr_map.keys()) + list(fl.get("filter_list_values", []))))
        exact_vals = [v for v in vals if v.lower() == q]
        prefix_vals = [v for v in vals if v.lower().startswith(q) and v.lower() != q]
        sub_vals = [v for v in vals if q in v.lower() and not v.lower().startswith(q)]
        matched_vals = exact_vals + prefix_vals + sub_vals
        field_hit = q == fl["da2_field_name"].lower() or q in fl["da2_field_name"].lower() or any(
            q in s.lower() for s in fl.get("da2_synonyms", [])
        )
        if matched_vals or field_hit:
            score = (
                (100.0 if exact_vals else 0.0)
                + (40.0 if prefix_vals else 0.0)
                + (15.0 if sub_vals else 0.0)
                + (25.0 if q == fl["da2_field_name"].lower() else (5.0 if field_hit else 0.0))
            )
            live_sample = {**(fl.get("live_code_value_sample") or {}), **ovr_map}
            matched_cvs = {k: str(v) for k, v in live_sample.items() if q in k.lower() or q == str(v)}
            entry: dict[str, Any] = {
                "match_type": "qualification_filter_list",
                "da2_domain": fl["da2_domain"],
                "da2_subentity": fl["da2_subentity"],
                "da2_field_name": fl["da2_field_name"],
                "v500_target": fl["v500_target"],
                "code_set": fl.get("code_set"),
                "matched_filter_values": matched_vals[:15] if matched_vals else vals[:10],
                "disambiguation_note": fl.get("disambiguation_note", ""),
            }
            if matched_cvs:
                entry["matched_code_values"] = matched_cvs
            scored_matches.append((score, entry))

    # Also search CE Event (Code Set 72) PowerChart taxonomy
    ce_tax = cat.get("ce_event_powerchart_taxonomy", {})
    if not dom_low or "encounter" in dom_low or "clinical event" in dom_low or "ce" in dom_low:
        for cat_entry in ce_tax.get("clinical_categories", []):
            rvals = cat_entry.get("representative_ce_event_values", [])
            exact_ce = [v for v in rvals if v.lower() == q]
            prefix_ce = [v for v in rvals if v.lower().startswith(q) and v.lower() != q]
            sub_ce = [v for v in rvals if q in v.lower() and not v.lower().startswith(q)]
            ce_hits = exact_ce + prefix_ce + sub_ce
            cat_hit = q in cat_entry["category_name"].lower() or any(
                q in es.lower() for es in cat_entry.get("parent_event_sets", [])
            )
            if ce_hits or cat_hit:
                score = (95.0 if exact_ce else 0.0) + (38.0 if prefix_ce else 0.0) + (14.0 if sub_ce else 0.0) + (4.0 if cat_hit else 0.0)
                cv_map = cat_entry.get("live_code_value_map", {})
                matched_cvs = {k: str(v) for k, v in cv_map.items() if q in k.lower() or q == str(v)}
                ce_item: dict[str, Any] = {
                    "match_type": "ce_event_powerchart_code_set_72",
                    "da2_domain": "CWC Encounter",
                    "da2_subentity": "Clinical Event",
                    "da2_field_name": "CE Event",
                    "v500_target": "CLINICAL_EVENT.EVENT_CD",
                    "code_set": 72,
                    "clinical_category": cat_entry["category_name"],
                    "powerchart_surface": cat_entry["powerchart_surface"],
                    "parent_event_sets": cat_entry["parent_event_sets"],
                    "matched_filter_values": ce_hits[:15] if ce_hits else rvals[:12],
                    "v500_payload_columns": cat_entry["v500_payload_columns"],
                }
                if matched_cvs:
                    ce_item["matched_code_values"] = matched_cvs
                scored_matches.append((score, ce_item))

    # Also search live harvested DISPLAY <-> CODE_VALUE pairs from <cclquery> XML
    live_fields = cat.get("live_harvested_code_value_mappings", {}).get("fields", {})
    for field_name, finfo in live_fields.items():
        dmap = finfo.get("display_to_code_value", {})
        exact_hits = {d: cv for d, cv in dmap.items() if d.lower() == q or str(cv) == q}
        sub_hits = {d: cv for d, cv in dmap.items() if (q in d.lower() or q == str(cv)) and d not in exact_hits}
        hits = {**exact_hits, **sub_hits}
        if hits or q in field_name.lower():
            score = (98.0 if exact_hits else 0.0) + (18.0 if sub_hits else 0.0) + (6.0 if q in field_name.lower() else 0.0)
            scored_matches.append((
                score,
                {
                    "match_type": "live_cclquery_code_value_map",
                    "da2_domain": "CWC Encounter",
                    "da2_field_name": field_name,
                    "matched_code_values": dict(list(hits.items())[:20]) if hits else dict(list(dmap.items())[:10]),
                },
            ))

    sub_low = subentity.strip().lower() if subentity else None
    cv_str = str(code_value).strip() if code_value is not None else None
    rescored: list[tuple[float, dict[str, Any]]] = []
    for sc, item in scored_matches:
        bonus = 0.0
        if dom_low and item.get("da2_domain", "").lower() == dom_low:
            bonus += 35.0
        if sub_low and item.get("da2_subentity", "").lower() == sub_low:
            bonus += 45.0
        if code_set is not None and item.get("code_set") == int(code_set):
            bonus += 90.0
        if cv_str and cv_str in [str(v) for v in item.get("matched_code_values", {}).values()]:
            bonus += 80.0
        rescored.append((sc + bonus, item))
    rescored.sort(key=lambda item: item[0], reverse=True)
    return [m for _, m in rescored]
