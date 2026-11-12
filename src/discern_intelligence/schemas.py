"""Typed data structures for Endexis (Clinical & Financial Analytics Compiler)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any

import csv
import tomllib

PIPELINE_VERSION = "endexis-v2.3-clean-r1"
DEFAULT_OWNER_GROUP_DISPLAY = "TENANT_OWNER_GROUP"
DEFAULT_UTC_TIMEZONE_INDEX = 54
DEFAULT_TENANT_CONFIG_FILENAMES = (".endexis.toml", "endexis.toml", "tenant.json")


@dataclass(frozen=True)
class TenantConfig:
    """
    Tenant-agnostic configuration for any Discern Analytics 2.0 (DA2) / Millennium tenant.

    Users can specify their tenant's `owner_group_display`, `tenancy` mode, `utc_timezone_index`,
    `facility_display`, `building_display`, and Code Set 220 location overrides (`facility_overrides`,
    `building_overrides`, `nurse_unit_overrides`, `room_overrides`, `bed_overrides`)
    plus general `code_value_overrides` via `.endexis.toml`, `tenant.json`, CLI flags
    (`--owner-group`, `--facility`, `--tenant-config`), Python API, or natural-language request text.
    """

    owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY
    tenancy: str | None = None
    utc_timezone_index: int = DEFAULT_UTC_TIMEZONE_INDEX
    facility_display: str | None = None
    facility_code_value: str | None = None
    building_display: str | None = None
    building_code_value: str | None = None
    facility_overrides: dict[str, str] = field(default_factory=dict)
    building_overrides: dict[str, str] = field(default_factory=dict)
    nurse_unit_overrides: dict[str, str] = field(default_factory=dict)
    room_overrides: dict[str, str] = field(default_factory=dict)
    bed_overrides: dict[str, str] = field(default_factory=dict)
    code_value_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def get_location_overrides(self, da2_field_name: str) -> dict[str, str]:
        """Returns tenant-configured Code Set 220 or field-level DISPLAY -> CODE_VALUE overrides."""
        mapping: dict[str, str] = {}
        if da2_field_name == "Enc Loc Facility":
            if self.facility_display and self.facility_code_value:
                mapping[self.facility_display] = str(self.facility_code_value)
            mapping.update(self.facility_overrides)
        elif da2_field_name == "Enc Loc Building":
            if self.building_display and self.building_code_value:
                mapping[self.building_display] = str(self.building_code_value)
            mapping.update(self.building_overrides)
        elif da2_field_name in ("Enc Loc Nurse Unit", "Enc Hx Loc Nurse Unit"):
            mapping.update(self.nurse_unit_overrides)
        elif da2_field_name in ("Enc Loc Room", "Bed Loc Room"):
            mapping.update(self.room_overrides)
        elif da2_field_name in ("Enc Loc Bed", "Bed Loc"):
            mapping.update(self.bed_overrides)
        if da2_field_name in self.code_value_overrides:
            mapping.update(self.code_value_overrides[da2_field_name])
        return mapping

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TenantConfig:
        tenant_sec = raw.get("tenant") if isinstance(raw.get("tenant"), dict) else raw
        loc_sec = raw.get("locations") if isinstance(raw.get("locations"), dict) else {}
        fac_ov = dict(loc_sec.get("facilities") or raw.get("facility_overrides") or {})
        bldg_ov = dict(loc_sec.get("buildings") or raw.get("building_overrides") or {})
        nu_ov = dict(loc_sec.get("nurse_units") or raw.get("nurse_unit_overrides") or {})
        room_ov = dict(loc_sec.get("rooms") or raw.get("room_overrides") or {})
        bed_ov = dict(loc_sec.get("beds") or raw.get("bed_overrides") or {})
        cv_ov = dict(raw.get("code_values") or raw.get("code_value_overrides") or {})
        tz_raw = tenant_sec.get("utc_timezone_index", raw.get("utc_timezone_index", DEFAULT_UTC_TIMEZONE_INDEX))
        return cls(
            owner_group_display=str(tenant_sec.get("owner_group_display") or raw.get("owner_group_display") or DEFAULT_OWNER_GROUP_DISPLAY).strip(),
            tenancy=tenant_sec.get("tenancy") or raw.get("tenancy"),
            utc_timezone_index=int(tz_raw) if tz_raw is not None else DEFAULT_UTC_TIMEZONE_INDEX,
            facility_display=tenant_sec.get("facility_display") or tenant_sec.get("facility") or raw.get("facility_display") or raw.get("facility"),
            facility_code_value=str(tenant_sec["facility_code_value"]) if tenant_sec.get("facility_code_value") is not None else (str(raw["facility_code_value"]) if raw.get("facility_code_value") is not None else None),
            building_display=tenant_sec.get("building_display") or tenant_sec.get("building") or raw.get("building_display") or raw.get("building"),
            building_code_value=str(tenant_sec["building_code_value"]) if tenant_sec.get("building_code_value") is not None else (str(raw["building_code_value"]) if raw.get("building_code_value") is not None else None),
            facility_overrides={str(k): str(v) for k, v in fac_ov.items()},
            building_overrides={str(k): str(v) for k, v in bldg_ov.items()},
            nurse_unit_overrides={str(k): str(v) for k, v in nu_ov.items()},
            room_overrides={str(k): str(v) for k, v in room_ov.items()},
            bed_overrides={str(k): str(v) for k, v in bed_ov.items()},
            code_value_overrides={str(field_k): {str(k): str(v) for k, v in dict(field_v).items()} for field_k, field_v in cv_ov.items()},
        )

    @classmethod
    def from_json(cls, path: str | Path) -> TenantConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_toml(cls, path: str | Path) -> TenantConfig:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_file(cls, path: str | Path | None = None, search_dir: str | Path | None = None) -> TenantConfig:
        if path is not None:
            p = Path(path)
            if p.suffix.lower() == ".toml" or p.name.endswith(".toml"):
                return cls.from_toml(p)
            return cls.from_json(p)
        base_dir = Path(search_dir) if search_dir is not None else Path.cwd()
        for candidate_name in DEFAULT_TENANT_CONFIG_FILENAMES:
            cand = base_dir / candidate_name
            if cand.is_file():
                return cls.from_file(cand)
        return cls()

    def to_toml(self) -> str:
        lines = [
            "# Endexis (.endexis.toml) — Zero-PHI Declarative Tenant Profile",
            "# Keeps site-specific CODE_VALUE IDs, owner_group_display, and UTC timezone index",
            "# in runtime configuration rather than model weights.",
            "",
            "[tenant]",
            f'owner_group_display = {json.dumps(self.owner_group_display)}',
            f'tenancy = {json.dumps(self.tenancy or "communityworks")}',
            f"utc_timezone_index = {int(self.utc_timezone_index)}",
        ]
        if self.facility_display:
            lines.append(f"facility_display = {json.dumps(self.facility_display)}")
        if self.facility_code_value:
            lines.append(f"facility_code_value = {json.dumps(str(self.facility_code_value))}")
        if self.building_display:
            lines.append(f"building_display = {json.dumps(self.building_display)}")
        if self.building_code_value:
            lines.append(f"building_code_value = {json.dumps(str(self.building_code_value))}")

        for sec_name, mapping in (
            ("locations.facilities", self.facility_overrides),
            ("locations.buildings", self.building_overrides),
            ("locations.nurse_units", self.nurse_unit_overrides),
            ("locations.rooms", self.room_overrides),
            ("locations.beds", self.bed_overrides),
        ):
            lines.append("")
            lines.append(f"[{sec_name}]")
            for k, v in sorted(mapping.items()):
                lines.append(f"{json.dumps(k)} = {json.dumps(str(v))}")
        return "\n".join(lines) + "\n"

    @classmethod
    def discover_from_code_value_rows(
        cls,
        rows: list[dict[str, Any]],
        owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY,
        tenancy: str | None = "communityworks",
        utc_timezone_index: int = DEFAULT_UTC_TIMEZONE_INDEX,
    ) -> TenantConfig:
        """Builds a TenantConfig from exported Millennium CODE_VALUE rows (Code Set 220)."""
        fac_ov: dict[str, str] = {}
        bldg_ov: dict[str, str] = {}
        nu_ov: dict[str, str] = {}
        room_ov: dict[str, str] = {}
        bed_ov: dict[str, str] = {}
        for row in rows:
            norm = {str(k).strip().upper(): v for k, v in row.items()}
            active = norm.get("ACTIVE_IND", 1)
            if str(active).strip() in ("0", "false", "False"):
                continue
            cs = norm.get("CODE_SET", 220)
            if str(cs).strip() not in ("220", ""):
                continue
            cv = str(norm.get("CODE_VALUE") or "").strip()
            disp = str(norm.get("DISPLAY") or norm.get("DESCRIPTION") or "").strip()
            meaning = str(norm.get("CDF_MEANING") or "").strip().upper()
            if not cv or not disp:
                continue
            if meaning == "FACILITY":
                fac_ov[disp] = cv
            elif meaning == "BUILDING":
                bldg_ov[disp] = cv
            elif meaning in ("NURSEUNIT", "AMBULATORY", "ANCILSURG"):
                nu_ov[disp] = cv
            elif meaning == "ROOM":
                room_ov[disp] = cv
            elif meaning == "BED":
                bed_ov[disp] = cv
        first_fac = next(iter(fac_ov.items()), (None, None))
        return cls(
            owner_group_display=owner_group_display,
            tenancy=tenancy,
            utc_timezone_index=utc_timezone_index,
            facility_display=first_fac[0],
            facility_code_value=first_fac[1],
            facility_overrides=fac_ov,
            building_overrides=bldg_ov,
            nurse_unit_overrides=nu_ov,
            room_overrides=room_ov,
            bed_overrides=bed_ov,
        )

    @classmethod
    def discover_from_export_file(
        cls,
        path: str | Path,
        owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY,
        tenancy: str | None = "communityworks",
        utc_timezone_index: int = DEFAULT_UTC_TIMEZONE_INDEX,
    ) -> TenantConfig:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            loaded = json.loads(text)
            rows = loaded if isinstance(loaded, list) else loaded.get("rows", [])
        else:
            rows = list(csv.DictReader(text.splitlines()))
        return cls.discover_from_code_value_rows(
            rows,
            owner_group_display=owner_group_display,
            tenancy=tenancy,
            utc_timezone_index=utc_timezone_index,
        )

    @classmethod
    def resolve_for_request(
        cls,
        request_text: str,
        base_config: TenantConfig | None = None,
        owner_group_display: str | None = None,
        tenancy: str | None = None,
        facility_display: str | None = None,
    ) -> TenantConfig:
        cfg = base_config or cls()
        resolved_owner = (owner_group_display or "").strip()
        if not resolved_owner:
            m_explicit = re.search(
                r"owner[_\s-]*group(?:[_\s-]*display)?\s*(?:=|:|is|of|to)?\s*['\"]([^'\"]+)['\"]",
                request_text,
                re.I,
            )
            if m_explicit:
                resolved_owner = m_explicit.group(1).strip()
            else:
                m_unquoted = re.search(
                    r"\b(?:owner[_\s-]*group(?:[_\s-]*display)?|import\s+into\s+tenant(?:\s+group)?)\s*(?:=|:)\s*([A-Za-z0-9_ -]{2,40})",
                    request_text,
                    re.I,
                )
                if m_unquoted:
                    resolved_owner = m_unquoted.group(1).strip()
                else:
                    m_ltca = re.search(r"\b(LTCA\s+[A-Z0-9]{3,8}_[A-Z]{2})\b", request_text)
                    if m_ltca:
                        resolved_owner = re.sub(r"\s+", " ", m_ltca.group(1)).strip()
        if not resolved_owner:
            resolved_owner = cfg.owner_group_display or DEFAULT_OWNER_GROUP_DISPLAY

        resolved_facility = (facility_display or "").strip() or cfg.facility_display
        if not resolved_facility:
            m_fac = re.search(
                r"\bfacility\s*(?:=|:|is)?\s*['\"]([A-Za-z0-9_ -]{2,48})['\"]",
                request_text,
                re.I,
            )
            if m_fac:
                resolved_facility = m_fac.group(1).strip()

        return cls(
            owner_group_display=resolved_owner,
            tenancy=tenancy or cfg.tenancy,
            utc_timezone_index=cfg.utc_timezone_index,
            facility_display=resolved_facility,
            facility_code_value=cfg.facility_code_value,
            building_display=cfg.building_display,
            building_code_value=cfg.building_code_value,
            facility_overrides=dict(cfg.facility_overrides),
            building_overrides=dict(cfg.building_overrides),
            nurse_unit_overrides=dict(cfg.nurse_unit_overrides),
            room_overrides=dict(cfg.room_overrides),
            bed_overrides=dict(cfg.bed_overrides),
            code_value_overrides=dict(cfg.code_value_overrides),
        )




@dataclass(frozen=True)
class RefinementSpec:
    """
    First-class multi-turn negative and override refinement specification (`v2.3.0`).

    Supports removing columns (`exclude_columns` / `remove_columns`), removing filters
    (`exclude_filters` / `remove_filters`), and replacing filter predicates
    (`override_filters` / `replace_filter_value`) in lockstep across IR, `<cclquery>` XML,
    `.daquery` XML, EJS V500 SQL, CCL `.prg`, and BIRT `.rptdesign` table bindings.
    """

    exclude_columns: list[str] = field(default_factory=list)
    exclude_filters: list[str] = field(default_factory=list)
    override_filters: dict[str, list[str]] = field(default_factory=dict)
    prior_selected_elements: list[str] = field(default_factory=list)
    prior_filters: dict[str, list[str]] = field(default_factory=dict)
    prior_template_id: str | None = None

    @property
    def remove_columns(self) -> list[str]:
        return self.exclude_columns

    @property
    def remove_filters(self) -> list[str]:
        return self.exclude_filters

    @property
    def replace_filter_value(self) -> dict[str, list[str]]:
        return self.override_filters

    @property
    def is_empty(self) -> bool:
        return not (
            self.exclude_columns
            or self.exclude_filters
            or self.override_filters
            or self.prior_selected_elements
            or self.prior_filters
            or self.prior_template_id
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_request(
        cls,
        request_text: str = "",
        explicit: dict[str, Any] | RefinementSpec | None = None,
        raw: dict[str, Any] | RefinementSpec | None = None,
        prior_context: dict[str, Any] | None = None,
    ) -> RefinementSpec:
        return cls.from_request_or_dict(
            request_text=request_text,
            raw=explicit if explicit is not None else raw,
            prior_context=prior_context,
        )

    @classmethod
    def from_request_or_dict(
        cls,
        request_text: str = "",
        raw: dict[str, Any] | RefinementSpec | None = None,
        prior_context: dict[str, Any] | None = None,
    ) -> RefinementSpec:
        if isinstance(raw, RefinementSpec):
            base_excl_cols = list(raw.exclude_columns)
            base_excl_flts = list(raw.exclude_filters)
            base_over_flts = {k: list(v) for k, v in raw.override_filters.items()}
            prior_elems = list(raw.prior_selected_elements)
            prior_flts = {k: list(v) for k, v in raw.prior_filters.items()}
            prior_tpl = raw.prior_template_id
        else:
            raw_d = raw or {}
            base_excl_cols = list(raw_d.get("exclude_columns") or raw_d.get("remove_columns") or [])
            base_excl_flts = list(raw_d.get("exclude_filters") or raw_d.get("remove_filters") or [])
            ov_raw = raw_d.get("override_filters") or raw_d.get("replace_filter_value") or {}
            base_over_flts = {
                str(k): ([str(x) for x in v] if isinstance(v, list) else [str(v)])
                for k, v in dict(ov_raw).items()
            }
            prior_elems = list(raw_d.get("prior_selected_elements") or [])
            pf_raw = raw_d.get("prior_filters") or {}
            prior_flts = {
                str(k): ([str(x) for x in v] if isinstance(v, list) else [str(v)])
                for k, v in dict(pf_raw).items()
            }
            prior_tpl = raw_d.get("prior_template_id")

        if prior_context:
            if not prior_elems and prior_context.get("prior_selected_elements"):
                prior_elems = list(prior_context["prior_selected_elements"])
            if not prior_flts and prior_context.get("prior_filters"):
                prior_flts = {
                    str(k): ([str(x) for x in v] if isinstance(v, list) else [str(v)])
                    for k, v in dict(prior_context["prior_filters"]).items()
                }
            if not prior_tpl and prior_context.get("base_template_id"):
                prior_tpl = str(prior_context["base_template_id"])

        if request_text:
            from .lexical_guards import (
                expand_excluded_column_names,
                parse_natural_language_refinements,
                split_multiturn_sections,
            )

            _, _, mt_elems, mt_flts = split_multiturn_sections(request_text)
            if not prior_elems and mt_elems:
                prior_elems = mt_elems
            if not prior_flts and mt_flts:
                prior_flts = mt_flts
            nl_ec, nl_ef, nl_of = parse_natural_language_refinements(request_text)
            for c in nl_ec:
                if c not in base_excl_cols:
                    base_excl_cols.append(c)
            for f in nl_ef:
                if f not in base_excl_flts:
                    base_excl_flts.append(f)
            for k, vals in nl_of.items():
                if k not in base_over_flts:
                    base_over_flts[k] = vals

        from .lexical_guards import expand_excluded_column_names as _expand_ec
        base_excl_cols = _expand_ec(base_excl_cols)
        return cls(
            exclude_columns=base_excl_cols,
            exclude_filters=base_excl_flts,
            override_filters=base_over_flts,
            prior_selected_elements=prior_elems,
            prior_filters=prior_flts,
            prior_template_id=prior_tpl,
        )


@dataclass(frozen=True)
class NeuralCompilationIR:
    """
    Compact, deterministic Intermediate Representation emitted inside `<output>{...}</output>`
    by `endexis-qwen3-0.6b-dpo-v2_3` for Track-A raw neural evaluation and Track-B
    neuro-symbolic verified compilation.
    """

    template_id: str | None = None
    da2_domain: str = "CWC Encounter"
    tenancy: str = "communityworks"
    execution_strategy: str = "single_domain_oda"
    selected_elements: list[str] = field(default_factory=list)
    exclude_columns: list[str] = field(default_factory=list)
    exclude_filters: list[str] = field(default_factory=list)
    override_filters: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NeuralCompilationIR:
        if not isinstance(raw, dict):
            raise TypeError(f"NeuralCompilationIR expected dict, got {type(raw).__name__}")

        def _require_str_list(val: Any, field_name: str) -> list[str]:
            if val is None:
                return []
            if not isinstance(val, list):
                raise ValueError(f"{field_name} must be a list of strings, got {type(val).__name__}")
            out: list[str] = []
            for item in val:
                if not isinstance(item, str):
                    raise ValueError(f"{field_name} items must be strings, got {type(item).__name__}")
                s = item.split("(")[0].strip() if (" (" in item and item.endswith(")")) else item.strip()
                if s:
                    out.append(s)
            return out

        ov_raw = raw.get("override_filters") if "override_filters" in raw else raw.get("replace_filter_value")
        ov_clean: dict[str, list[str]] = {}
        if ov_raw is not None:
            if not isinstance(ov_raw, dict):
                raise ValueError(f"override_filters must be a dict, got {type(ov_raw).__name__}")
            for k, v in ov_raw.items():
                if isinstance(v, str):
                    ov_clean[str(k)] = [v.strip()] if v.strip() else []
                elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                    ov_clean[str(k)] = [x.strip() for x in v if x.strip()]
                else:
                    raise ValueError(f"override_filters[{k!r}] must be a string or list of strings")

        sel_raw = raw.get("selected_elements") if "selected_elements" in raw else raw.get("ground_truth_selected_elements")
        if sel_raw is None and isinstance(raw.get("queries"), list) and raw["queries"]:
            q0 = raw["queries"][0]
            if isinstance(q0, dict):
                sel_raw = q0.get("elements") if "elements" in q0 else q0.get("selected_elements")
        sel_clean = _require_str_list(sel_raw, "selected_elements")

        excl_cols_raw = raw.get("exclude_columns") if "exclude_columns" in raw else raw.get("remove_columns")
        excl_flts_raw = raw.get("exclude_filters") if "exclude_filters" in raw else raw.get("remove_filters")
        excl_cols = _require_str_list(excl_cols_raw, "exclude_columns")
        excl_flts = _require_str_list(excl_flts_raw, "exclude_filters")

        dom = str(raw.get("da2_domain") or "CWC Encounter")
        if isinstance(raw.get("queries"), list) and raw["queries"] and isinstance(raw["queries"][0], dict):
            dom = str(raw["queries"][0].get("domain") or dom)

        tpl_val = raw.get("template_id") or raw.get("id") or raw.get("query_id")
        return cls(
            template_id=str(tpl_val).strip() if tpl_val else None,
            da2_domain=dom,
            tenancy=str(raw.get("tenancy") or raw.get("target_tenancy") or "communityworks"),
            execution_strategy=str(raw.get("execution_strategy") or "single_domain_oda"),
            selected_elements=sel_clean,
            exclude_columns=excl_cols,
            exclude_filters=excl_flts,
            override_filters=ov_clean,
        )

    @classmethod
    def parse_from_completion(cls, completion_text: str) -> NeuralCompilationIR | None:
        if not completion_text:
            return None
        candidate = completion_text.strip()
        m_out = re.search(r"<output>\s*(\{[\s\S]*?\})\s*</output>", candidate)
        if m_out:
            json_str = m_out.group(1)
        else:
            # Strip <reasoning>...</reasoning> or <think>...</think> and find outermost JSON object
            stripped = re.sub(r"<(?:reasoning|think)>[\s\S]*?</(?:reasoning|think)>", "", candidate).strip()
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            json_str = stripped[start : end + 1]
        try:
            parsed = json.loads(json_str)
            if not isinstance(parsed, dict):
                return None
            if not parsed.get("template_id") and (parsed.get("id") or parsed.get("query_id")):
                parsed["template_id"] = parsed.get("id") or parsed.get("query_id")
            return cls.from_dict(parsed)
        except Exception:
            return None


@dataclass(frozen=True)
class ColumnMatch:
    table: str
    column: str
    v500_type: str
    ccl_type: str
    nullable: bool
    code_set: int | None
    code_set_name: str | None
    cdf_meanings: list[str]
    in_healtheintent_ods: bool
    definition: str
    da2_field_name: str | None
    da2_domain: str | None
    da2_subentity: str | None
    element_uuid: str | None
    value_semantics: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormOutputResolution:
    form_id: str
    form_family_name: str
    definition_tables: list[dict[str, Any]]
    activity_tables: list[dict[str, Any]]
    payload_columns: list[dict[str, Any]]
    join_pattern_sql: str
    da2_domain: str
    da2_sub_entity: str
    da2_fields: list[str]
    mandatory_filters: list[str]
    matched_som_objects: list[dict[str, Any]] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InterpretationResult:
    request_text: str
    inferred_tenancy: str
    tenancy_rationale: str
    legal_execution_surfaces: list[str]
    recommended_strategy: str
    matched_columns: list[ColumnMatch]
    matched_forms_and_outputs: list[FormOutputResolution]
    matched_code_sets: list[dict[str, Any]]
    join_graph: list[str]
    guardrails_triggered: list[str]
    matched_da2_views: list[dict[str, Any]] = field(default_factory=list)
    refinement: RefinementSpec = field(default_factory=RefinementSpec)
    matched_template_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationReport:
    is_valid: bool
    checked_invariants: list[str]
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompiledCCLProgram:
    program_name: str
    request_text: str
    target_tenancy: str
    tenancy_deployment_note: str
    source_code: str
    tables_used: list[str]
    code_sets_used: list[dict[str, Any]]
    verification: VerificationReport
    value_filters_applied: list[str] = field(default_factory=list)  # "TABLE.COLUMN_CD" targets in the PLAN/JOIN

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DA2QuerySpec:
    query_name: str
    query_uuid: str
    da2_domain: str
    sub_entities: list[str]
    selected_elements: list[dict[str, Any]]
    filters: list[str]
    runtime_prompts: list[dict[str, Any]]
    qualification_xml: str
    cclquery_xml: str = ""
    ejs_v500_sql: str = ""
    daquery_xml: str = ""
    owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DA2UploadableBundle:
    report_name: str
    report_uuid: str
    request_text: str
    target_tenancy: str
    execution_strategy: str
    birt_join_key: str | None
    queries: list[DA2QuerySpec]
    rptdesign_xml: str
    build_card_markdown: str
    equivalent_ccl: str
    verification: VerificationReport
    owner_group_display: str = DEFAULT_OWNER_GROUP_DISPLAY
    # value filters in the DA2 query files that the reference SQL or CCL doesn't carry (their table isn't joined there)
    value_filters_not_in_reference: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
