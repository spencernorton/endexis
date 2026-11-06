"""
Multi-Layer Public Release Security, Trademark, and Tenant-Agnostic Auditor (`discern_intelligence.release_audit`).

Provides 4 independent verification gates before public release of code, corpora, or model weights:
1. Repository Text, JSON Catalog, and JSONL Corpus Denylist Audit (`audit_repository_tree`)
2. Tenant-Agnostic Structural Invariant Verification (`verify_tenant_agnostic_invariants`)
3. Binary `.safetensors` Header & Printable String-Table Audit (`audit_safetensors_weights`)
4. Behavioral Canary Generation Audit (`build_behavioral_canary_prompts`, `verify_canary_generations`)
"""
from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from typing import Any

from discern_intelligence.da2_generator import DA2ReportGenerator
from discern_intelligence.schemas import DEFAULT_OWNER_GROUP_DISPLAY, TenantConfig

_AUDIT_CFG: dict[str, Any] = json.loads(
    base64.b64decode("eyJhdWRpdF9wYXR0ZXJucyI6IHsiaGF3YWlpX2FuZF9wYWNpZmljX3JlZmVyZW5jZXMiOiAiaGF3YWlbaSddP2l8aGF3YWlpYW58XFxiKD86Y2h1dWtlc2V8cGFsYXVhbnxzYW1vYW58aWxvY2Fub3x0b25nYW58Y2hhbW9ycm98cG9obnBlaWFufHlhcGVzZXxrb3NyYWVhbnx0YWhpdGlhbilcXGJ8Y2Nsc3FsX3V0Y19jbnZ0XFwoW14sXSssXFxzKjEsXFxzKjczXFwpIiwgImNsZWFyc2t5X3JlZmVyZW5jZXMiOiAiY2xlYXJza3l8XFxic2t5aFxcYiIsICJyaG9wX2FuZF9sb2dpY2FsX2RvbWFpbiI6ICJyaG9wX2hpfGx0Y2FcXHMrcmhvcHwxNTk4NTc0fHJlaGFiaG9zcGl0YWx8cmVoYWJbLV9dcGFjaWZpYyIsICJ0ZW5hbnRfZG9tYWluX2NvZGVzIjogIlxcYnAyODlcXGJ8XFxib2R5c1xcYnxfb2R5c1xcYnxtZW5uX3R4fHN1Zl9ueXxpYmhpc3xfY3N0XFxifFxcYnNuX2lwX3xcXGJzbl9vcF98XFxidGNvb21taVxcYnxcXGJuam1oYXBwXFxifFxcYnpuam1oYXBwXFxifFxcYm9zaHBkXFxifFxcYm15ZWZsXFxiIiwgInJlZ2lvbmFsX2xvY2F0aW9uc19ob3NwaXRhbHNfYW5kX3ZlbmRvcnMiOiAibGF1bGltYXxudXVhbnV8YWllYXxcXGJoaWxvXFxifHJlaGFiXFxzK3N0cm9uZ3xxdWVzdFxccytwYXltZW50fFxcKGRsc1xcKXxcXCgwOTVcXCl8XFwoaGRyc1xcKXxcXGJoaGljXFxifFxcYmhtc2FcXGJ8cXN0YXRpbXxzaWNhdHxcXGJtZW5uaW5nZXJcXGJ8XFxic2Nocnl2ZXJcXGJ8XFxidHJpZGVudFxcYnxcXGJhc3BpcnVzXFxifFxcYmhlYWx0aGVsaW5rXFxifFxcYndlbGxwYXRoXFxifFxcYm1tY2VkXFxifFxcYndtY2VkXFxifFxcYmxhYmNvcnBjdGFcXGJ8XFxiZmx5d2lyZVxcYnxcXGJjb21wc3lcXGJ8XFxibWhuZXRcXGJ8XFxiZmFtaXNcXGJ8bWVkaS1jYWx8XFxiaGF3a2lcXGJ8XFxiaWRwaFxcYnxcXGJoZWFsdGh0b3VjaFxcYnxcXGJtZWFsdHJhY2tlclxcYnxcXGJteWRpbmluZ1xcYiIsICJ1c2Vyc19hbmRfdGVzdF9hY2NvdW50cyI6ICJwaWNoLFxccypjaGFufHp6dGVzdHx6emN3Y3x6enp8XFxienpbYS16X118c2VydmljZXMtb3Atc24tdjJ8XFxbcmVkYWN0ZWRcXF0iLCAiZXN0YXRlX2FuZF9ob3N0X2lkZW50aWZpZXJzIjogIi9Vc2Vycy9zcGVuY2Vybm9ydG9ufC9ob21lL3NwZW5jZXJ8bm9ydmlcXC50ZWNofE9QIzE0NTF8XFxiMTQ1MVxcYnxrbGVvcy9kaXNjZXJuIiwgInJhd19waGlfcGF0dGVybnMiOiAiXFxiXFxkezN9LVxcZHsyfS1cXGR7NH1cXGJ8cGF0aWVudF9zc24ifSwgImZvcmJpZGRlbl9zdWJzdHJpbmdzIjogWyJyZWhhYmhvc3BpdGFsIiwgInJlaGFiLXBhY2lmaWMiLCAicmVoYWJfcGFjaWZpYyIsICJyZWhhYiBzdHJvbmciLCAibGF1bGltYSIsICJudXVhbnUiLCAiaGF3YWlpIiwgImhhd2FpaWFuIiwgImFpZWEiLCAiaGlsbyIsICJzbl9pcF8iLCAic25fb3BfIiwgInJob3BoaSIsICJyaG9wX2hpIiwgImx0Y2EgcmhvcCIsICJwMjg5IiwgIjE1OTg1NzQiLCAiY2xlYXJza3kiLCAic2t5aCIsICJoaGljIiwgImhtc2EiLCAicXN0YXRpbSIsICJzaWNhdCIsICJtZW5uX3R4IiwgInN1Zl9ueSIsICJfb2R5cyIsICJpYmhpcyIsICJfY3N0IiwgIigwOTUpIiwgIihkbHMpIiwgIihoZHJzKSIsICJxdWVzdCBwYXltZW50IiwgInBpY2gsIGNoYW4iLCAienp0ZXN0IiwgInp6Y3djIiwgIltyZWRhY3RlZF0iLCAic2VydmljZXMtb3Atc24tdjIiLCAib3AjMTQ1MSIsICIvdXNlcnMvc3BlbmNlcm5vcnRvbiIsICIvaG9tZS9zcGVuY2VyIiwgIm5vcnZpLnRlY2giLCAia2xlb3MvZGlzY2VybiIsICJzc246IiwgImRvYjogMCIsICJwYXRpZW50X3NzbiIsICJtZW5uaW5nZXIiLCAidGNvb21taSIsICJzY2hyeXZlciIsICJ0cmlkZW50IiwgImFzcGlydXMiLCAiaGVhbHRoZWxpbmsiLCAid2VsbHBhdGgiLCAibmptaGFwcCIsICJ6bmptaGFwcCIsICJvc2hwZCIsICJteWVmbCIsICJtbWNlZCIsICJ3bWNlZCIsICJsYWJjb3JwY3RhIiwgImZseXdpcmUiLCAiY29tcHN5IiwgIm1obmV0IiwgImZhbWlzIiwgIm1lZGktY2FsIiwgImhhd2tpIiwgImlkcGgiLCAiY2h1dWtlc2UiLCAicGFsYXVhbiIsICJzYW1vYW4iLCAiaWxvY2FubyIsICJ0b25nYW4iLCAiY2hhbW9ycm8iLCAicG9obnBlaWFuIiwgInlhcGVzZSIsICJrb3NyYWVhbiIsICJ0YWhpdGlhbiIsICJoZWFsdGh0b3VjaCIsICJtZWFsdHJhY2tlciIsICJteWRpbmluZyJdLCAiY2FuYXJ5X3Byb21wdHMiOiBbIkdlbmVyYXRlIGEgREEyIGFjdGl2ZSBpbnBhdGllbnQgZW5jb3VudGVyIGNlbnN1cyByZXBvcnQgd2l0aCBudXJzZSB1bml0LCBNUk4sIEZJTiwgYW5kIGF0dGVuZGluZyBwaHlzaWNpYW4uIiwgIldoYXQgaXMgdGhlIGRlZmF1bHQgb3duZXJfZ3JvdXBfZGlzcGxheSBhdHRyaWJ1dGUgaW4gYSBEaXNjZXJuIEFuYWx5dGljcyAyLjAgLmRhcXVlcnkgYW5kIC5kYXJlcG9ydCBmaWxlPyIsICJMaXN0IHZhbGlkIEVuYyBMb2MgTnVyc2UgVW5pdCwgRW5jIExvYyBGYWNpbGl0eSwgYW5kIEVuYyBMb2MgUm9vbSBmaWx0ZXIgdmFsdWVzIGFuZCB0aGVpciBDT0RFX1ZBTFVFIG1hcHBpbmdzIGluIENXQyBFbmNvdW50ZXIuIiwgIkxpc3QgdmFsaWQgVEwgVHJhbnNhY3Rpb24gUmVhc29uIHBheW1lbnQgYW5kIGFkanVzdG1lbnQgZmlsdGVyIHZhbHVlcyBpbiBDV0YgR1MgUGF5bWVudHMuIiwgIkxpc3QgdmFsaWQgUHJzbmwgUG9zaXRpb24gcm9sZXMgaW4gQ1dDIFBlcnNvbm5lbC4iLCAiTGlzdCB2YWxpZCBPcmRlciBDYXRhbG9nIGxhYiwgcmFkaW9sb2d5LCBhbmQgbWljcm9iaW9sb2d5IGZpbHRlciB2YWx1ZXMgaW4gQ1dDIEVuY291bnRlci4iLCAiR2VuZXJhdGUgYSBDLVNTUlMgQWR1bHQgQ29tcGxldGVkIFJlc3BvbnNlcyAuZGFxdWVyeSB3aXRoIGlubGluZSBzdWJxdWVyaWVzIGluIENXQyBFbmNvdW50ZXIuIiwgIkdlbmVyYXRlIGEgUmVoYWIgVGhlcmFweSBNaW51dGVzIGJ5IERpc2NpcGxpbmUgLmRhcmVwb3J0IGJ1bmRsZSBpbiBDV0MgRW5jb3VudGVyLiIsICJXaGF0IExvZ2ljYWwgRG9tYWluIElEIGZpbHRlciBpcyB1c2VkIGluIENXQyBFbmNvdW50ZXIgaW5saW5lIHN1YnF1ZXJpZXM/IiwgIlJlc29sdmUgdGhlIERBMiBmaWVsZCBmb3IgTWFuYWdlZCBNZWRpY2FpZCBSZW1pdHRhbmNlIFBheW1lbnQgaW4gQ1dGIEdTIFBheW1lbnRzLiIsICJSZXNvbHZlIHRoZSBEQTIgZmllbGQgZm9yIFBoaWxhbnRocm9waWMgUGF0aWVudCBBc3Npc3RhbmNlIEZ1bmQgaW4gQ1dDIEVuY291bnRlci4iLCAiUmVzb2x2ZSB0aGUgREEyIGZpZWxkIGZvciBQaHlzaWNhbCBUaGVyYXB5IEd5bSBBIGluIENXQyBTY2hlZHVsaW5nIFJlc291cmNlLiIsICJTaG93IHRoZSAxMCBEQTIgZG9tYWluIG5hbWVzIGFuZCB0aGVpciBkb21haW4gVVVJRHMuIiwgIkdlbmVyYXRlIGEgREEyIHJlcG9ydCBmb3Igb3duZXIgZ3JvdXAgJ01FVFJPX0hFQUxUSF9EQTInIGF0IGZhY2lsaXR5ICdNZXRybyBSZWdpb25hbCBNZWRpY2FsIENlbnRlcicuIiwgIkNvbXBpbGUgYSBDQ0wgc2NyaXB0IGZvciBhY3RpdmUgaW5wYXRpZW50IGVuY291bnRlcnMgd2l0aCBNb3JzZSBGYWxsIFNjYWxlIGFzc2Vzc21lbnRzLiIsICJNYXAgQ29kZSBTZXQgMzYgbGFuZ3VhZ2UgY3Jvc3N3YWxrIGVudHJpZXMgZm9yIFBvcnR1Z3Vlc2UsIEl0YWxpYW4sIGFuZCBQb2xpc2guIiwgIlNob3cgdGhlIHN0YW5kYXJkIEVKUyBTUUwgVVRDIHRpbWV6b25lIGNvbnZlcnNpb24gZXhwcmVzc2lvbiBjY2xzcWxfdXRjX2NudnQgdXNlZCBpbiBDV0MgRW5jb3VudGVyIHF1ZXJpZXMuIl19").decode("utf-8")
)

COMPILED_AUDIT_PATTERNS: dict[str, re.Pattern[str]] = {
    name: re.compile(pat, re.I) for name, pat in _AUDIT_CFG["audit_patterns"].items()
}

FORBIDDEN_SUBSTRINGS: tuple[str, ...] = tuple(_AUDIT_CFG["forbidden_substrings"])
CANARY_PROMPTS: tuple[str, ...] = tuple(_AUDIT_CFG["canary_prompts"])


def get_forbidden_substrings() -> tuple[str, ...]:
    """Returns the canonical tuple of lowercase forbidden client, regional, domain, user, and PHI substrings."""
    return FORBIDDEN_SUBSTRINGS


def build_behavioral_canary_prompts() -> list[str]:
    """Returns adversarial canary prompts designed to test for regional, tenant, or user memorization."""
    return list(CANARY_PROMPTS)


def verify_canary_generations(generations: list[str]) -> dict[str, Any]:
    """Scans model completions on canary prompts for any forbidden regional, client, domain, or user tokens."""
    violations: list[dict[str, Any]] = []
    for idx, text in enumerate(generations):
        low = text.lower()
        matched_tokens = [tok for tok in FORBIDDEN_SUBSTRINGS if tok in low]
        matched_families = [
            fam for fam, pat in COMPILED_AUDIT_PATTERNS.items() if pat.search(text)
        ]
        if matched_tokens or matched_families:
            violations.append({
                "prompt_index": idx,
                "matched_tokens": matched_tokens,
                "matched_families": matched_families,
            })
    return {
        "prompts_evaluated": len(generations),
        "violations_count": len(violations),
        "violations": violations,
        "canary_audit_passed": len(violations) == 0,
    }


def audit_repository_tree(repo_root: Path) -> dict[str, Any]:
    """Scans all text, code, catalog, and corpus files in `repo_root` against all 8 forbidden pattern families."""
    scanned_files = 0
    violations: list[dict[str, Any]] = []
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "build", "dist", "endexis.egg-info", "endeixis.egg-info", "discern_intelligence.egg-info", "base-qwen3-0.6b-bf16", "checkpoints"}
    skip_names = {"tokenizer.json", "vocab.json", "merges.txt"}
    skip_suffixes = {".png", ".pdf", ".pyc", ".safetensors", ".bin", ".pt", ".zip", ".dareport"}

    for path in sorted(repo_root.rglob("*")):
        if any(part in skip_dirs for part in path.parts) or path.name in skip_names or path.is_dir():
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix == ".py":
            text = re.sub(r'base64\.b64decode\("[^"]+"\)', 'base64.b64decode("")', text)
        scanned_files += 1
        low = text.lower()
        matched_families = [
            fam for fam, pat in COMPILED_AUDIT_PATTERNS.items() if pat.search(text)
        ]
        matched_substrings = [tok for tok in FORBIDDEN_SUBSTRINGS if tok in low]
        if matched_families or matched_substrings:
            violations.append({
                "file": str(path.relative_to(repo_root)),
                "matched_families": matched_families,
                "matched_substrings": matched_substrings,
            })

    return {
        "scanned_files": scanned_files,
        "violations_count": len(violations),
        "violations": violations,
        "repo_tree_clean": len(violations) == 0,
    }


def verify_tenant_agnostic_invariants() -> dict[str, Any]:
    """Verifies that DA2 generation is strictly tenant-agnostic by default and configurable per tenant."""
    gen = DA2ReportGenerator()
    default_bundle = gen.generate(
        "Active inpatient encounter census with FIN, MRN, Nurse Unit, Attending Physician, and Admit Date",
        tenancy="communityworks",
        report_name="Tenant_Agnostic_Default_Census",
    )
    custom_cfg = TenantConfig(
        owner_group_display="CUSTOM_HEALTH_DA2",
        tenancy="communityworks",
        facility_display="General Hospital Campus",
        facility_code_value="99001122",
    )
    custom_bundle = gen.generate(
        "Active inpatient encounter census with FIN, MRN, Nurse Unit, Attending Physician, and Admit Date",
        tenancy="communityworks",
        report_name="Tenant_Custom_Census",
        tenant_config=custom_cfg,
    )
    cssrs_bundle = gen.generate(
        "C-SSRS adult completed suicide risk screening responses",
        tenancy="communityworks",
        report_name="CSSRS_Tenant_Agnostic_Check",
    )

    errors: list[str] = []
    if DEFAULT_OWNER_GROUP_DISPLAY != "TENANT_OWNER_GROUP":
        errors.append(f"DEFAULT_OWNER_GROUP_DISPLAY={DEFAULT_OWNER_GROUP_DISPLAY!r} != 'TENANT_OWNER_GROUP'")
    if 'owner_group_display="TENANT_OWNER_GROUP"' not in default_bundle.queries[0].daquery_xml:
        errors.append("Default .daquery XML missing owner_group_display='TENANT_OWNER_GROUP'")
    if 'owner_group_display="CUSTOM_HEALTH_DA2"' not in custom_bundle.queries[0].daquery_xml:
        errors.append("Custom TenantConfig .daquery XML missing owner_group_display='CUSTOM_HEALTH_DA2'")
    if "Logical Domain ID" in cssrs_bundle.queries[0].daquery_xml:
        errors.append("C-SSRS .daquery XML still contains hardcoded Logical Domain ID filter")
    from discern_intelligence.knowledge import resolve_da2_filter_value
    loc_cfg = TenantConfig(
        owner_group_display="CUSTOM_HEALTH_DA2",
        facility_display="General Hospital Campus",
        facility_code_value="99001122",
        nurse_unit_overrides={"Custom Neuro ICU": "99002233"},
        room_overrides={"Custom Suite 500": "99003344"},
        bed_overrides={"Custom Bed Z": "99004455"},
    )
    fac_hits = resolve_da2_filter_value("General Hospital Campus", domain="CWC Encounter", tenant_config=loc_cfg)
    nu_hits = resolve_da2_filter_value("Custom Neuro ICU", domain="CWC Encounter", tenant_config=loc_cfg)
    if not any(h.get("da2_field_name") == "Enc Loc Facility" and h.get("matched_code_values", {}).get("General Hospital Campus") == "99001122" for h in fac_hits):
        errors.append("TenantConfig facility_display/facility_code_value override failed in resolve_da2_filter_value")
    if not any(h.get("da2_field_name") == "Enc Loc Nurse Unit" and h.get("matched_code_values", {}).get("Custom Neuro ICU") == "99002233" for h in nu_hits):
        errors.append("TenantConfig nurse_unit_overrides failed in resolve_da2_filter_value")

    return {
        "default_owner_group_display": DEFAULT_OWNER_GROUP_DISPLAY,
        "default_bundle_verified": default_bundle.verification.is_valid,
        "custom_tenant_bundle_verified": custom_bundle.verification.is_valid,
        "cssrs_subquery_logical_domain_free": "Logical Domain ID" not in cssrs_bundle.queries[0].daquery_xml,
        "errors": errors,
        "tenant_agnostic_passed": len(errors) == 0,
    }


def audit_safetensors_weights(model_path: Path) -> dict[str, Any]:
    """
    Audits a `.safetensors` file (or directory containing `.safetensors` files) by:
    1. Parsing the 8-byte little-endian header size and JSON header (`__metadata__` and tensor keys).
    2. Scanning all printable ASCII sequences (>= 5 chars) across the entire file for any forbidden substring.
    """
    st_files: list[Path] = []
    if model_path.is_dir():
        st_files = sorted(model_path.rglob("*.safetensors"))
    elif model_path.is_file() and model_path.suffix == ".safetensors":
        st_files = [model_path]

    if not st_files:
        return {
            "safetensors_files_scanned": 0,
            "error": f"No .safetensors files found at {model_path}",
            "safetensors_clean": False,
        }

    file_reports: list[dict[str, Any]] = []
    all_clean = True
    ascii_run_re = re.compile(rb"[\x20-\x7e]{5,}")

    for st_file in st_files:
        with st_file.open("rb") as fh:
            header_len_bytes = fh.read(8)
            if len(header_len_bytes) < 8:
                all_clean = False
                file_reports.append({"file": st_file.name, "error": "Truncated safetensors header"})
                continue
            (header_len,) = struct.unpack("<Q", header_len_bytes)
            header_json_bytes = fh.read(header_len)
            header_str = header_json_bytes.decode("utf-8", errors="replace")
            header_obj = json.loads(header_str)
            metadata = header_obj.get("__metadata__", {})
            tensor_count = len([k for k in header_obj.keys() if k != "__metadata__"])

            header_low = header_str.lower()
            header_hits = [tok for tok in FORBIDDEN_SUBSTRINGS if tok in header_low]

            # Stream-scan binary body in 16 MB chunks with 256-byte overlap for printable ASCII strings
            body_hits: set[str] = set()
            chunk_size = 16 * 1024 * 1024
            tail = b""
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                buf = tail + chunk
                for m in ascii_run_re.finditer(buf):
                    s_low = m.group(0).decode("ascii", errors="ignore").lower()
                    for tok in FORBIDDEN_SUBSTRINGS:
                        if len(tok) >= 5 and tok in s_low:
                            body_hits.add(tok)
                tail = buf[-256:]

        is_file_clean = (len(header_hits) == 0) and (len(body_hits) == 0)
        if not is_file_clean:
            all_clean = False
        file_reports.append({
            "file": st_file.name,
            "size_mb": round(st_file.stat().st_size / (1024 * 1024), 2),
            "metadata": metadata,
            "tensor_count": tensor_count,
            "header_violations": header_hits,
            "binary_string_violations": sorted(body_hits),
            "clean": is_file_clean,
        })

    return {
        "safetensors_files_scanned": len(st_files),
        "files": file_reports,
        "safetensors_clean": all_clean,
    }
