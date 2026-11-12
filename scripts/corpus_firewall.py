"""
Deterministic Zero-Leakage Corpus Firewall (`corpus_firewall.py`).

Enforces 0 / 0 / 0 overlap between `data/eval_postfreeze_v3.jsonl` and all training
corpora (`data/sft_train_v1.jsonl`, `data/dpo_contrastive_v1.jsonl`) across:
1. Exact normalized prompt string (`exact_norm`)
2. Normalized sorted alphanumeric token bag (`token_bag`)
3. 5-gram template skeleton (`skeleton_5grams` after masking domain/element/table/value slots,
   plus `raw_5grams` on unmasked normalized words)

Stores one-way SHA-256 digests of the sealed holdout in `data/postfreeze_v3_firewall_hashes.json`
so training-corpus hygiene and expansion scripts can verify `0 / 0 / 0` overlap without ever
opening or updating `atime` on the `0444`-sealed `data/eval_postfreeze_v3.jsonl` file.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
SRC_DATA_DIR = REPO_ROOT / "src" / "discern_intelligence" / "data"

_WS_RE = re.compile(r"\s+")
_TOK_RE = re.compile(r"[a-z0-9]+")
_QUOTED_RE = re.compile(r"(?:'[^']{2,80}'|\"[^\"]{2,80}\")")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_V500_COL_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\.[A-Z][A-Z0-9_]{2,}\b")
_NUM_RE = re.compile(r"\b\d+\b")

_SLOT_PHRASES: list[tuple[re.Pattern[str], str]] | None = None


def _get_slot_patterns() -> list[tuple[re.Pattern[str], str]]:
    global _SLOT_PHRASES
    if _SLOT_PHRASES is not None:
        return _SLOT_PHRASES

    patterns: list[tuple[str, str]] = []
    for t in ("communityworks", "continuum", "standalone_rho", "standalone", "rho"):
        patterns.append((t, "slot_tenancy"))

    dom_path = SRC_DATA_DIR / "da2_domain_taxonomy_catalog.json"
    if dom_path.exists():
        dom_cat = json.loads(dom_path.read_text(encoding="utf-8"))
        for d in dom_cat.get("domains", []):
            dn = d.get("domain_name", "").strip()
            if len(dn) >= 4:
                patterns.append((dn, "slot_domain"))

    rep_path = SRC_DATA_DIR / "da2_canonical_reports_and_queries_catalog.json"
    if rep_path.exists():
        rep_cat = json.loads(rep_path.read_text(encoding="utf-8"))
        for bv in rep_cat.get("business_views_xml_catalog", []):
            bvn = bv.get("logical_view_name", "").strip()
            if len(bvn) >= 4:
                patterns.append((bvn, "slot_domain"))
        for qr in rep_cat.get("canonical_queries_and_reports", []):
            if qr.get("da2_domain"):
                patterns.append((qr["da2_domain"], "slot_domain"))
            for sel in qr.get("selected_elements", []):
                base = sel.split(":", 1)[1].strip() if ":" in sel else sel.strip()
                if len(base) >= 5:
                    patterns.append((base, "slot_elem"))

    # Deduplicate and sort longest-first so multi-word phrases mask cleanly before sub-phrases
    dedup: dict[str, str] = {}
    for phrase, tag in patterns:
        k = phrase.lower().strip()
        if k and k not in dedup:
            dedup[k] = tag
    ordered = sorted(dedup.items(), key=lambda kv: len(kv[0]), reverse=True)
    _SLOT_PHRASES = [
        (re.compile(r"\b" + re.escape(phrase) + r"\b", re.I), tag)
        for phrase, tag in ordered
    ]
    return _SLOT_PHRASES


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_exact(text: str) -> str:
    cleaned = _WS_RE.sub(" ", text.strip().lower())
    return cleaned


def normalize_token_bag(text: str) -> str:
    toks = sorted(set(_TOK_RE.findall(text.lower())))
    return " ".join(toks)


def mask_template_skeleton_tokens(text: str) -> list[str]:
    s = _UUID_RE.sub(" slot_uuid ", text)
    s = _V500_COL_RE.sub(" slot_v500col ", s)
    s = _QUOTED_RE.sub(" slot_quoted ", s)
    for pat, tag in _get_slot_patterns():
        s = pat.sub(f" {tag} ", s)
    s = _NUM_RE.sub(" slot_num ", s.lower())
    raw_toks = re.findall(r"slot_[a-z0-9]+|[a-z0-9]+", s)
    # Collapse consecutive identical slot tags
    collapsed: list[str] = []
    for tok in raw_toks:
        if collapsed and tok.startswith("slot_") and collapsed[-1] == tok:
            continue
        collapsed.append(tok)
    return collapsed


def extract_ngrams(tokens: list[str], n: int = 5) -> set[str]:
    if not tokens:
        return set()
    if len(tokens) < n:
        return {" ".join(tokens)}
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def extract_skeleton_5grams(text: str) -> set[str]:
    toks = mask_template_skeleton_tokens(text)
    return extract_ngrams(toks, n=5)


def extract_raw_5grams(text: str) -> set[str]:
    toks = _TOK_RE.findall(text.lower())
    return extract_ngrams(toks, n=5)


def build_firewall_index(texts: Iterable[str]) -> dict[str, set[str]]:
    exact_hashes: set[str] = set()
    bag_hashes: set[str] = set()
    skel_5gram_hashes: set[str] = set()
    raw_5gram_hashes: set[str] = set()
    for t in texts:
        exact_hashes.add(sha256_hex("exact:" + normalize_exact(t)))
        bag_hashes.add(sha256_hex("bag:" + normalize_token_bag(t)))
        for ng in extract_skeleton_5grams(t):
            skel_5gram_hashes.add(sha256_hex("skel5:" + ng))
        for ng in extract_raw_5grams(t):
            raw_5gram_hashes.add(sha256_hex("raw5:" + ng))
    return {
        "exact_hashes": exact_hashes,
        "bag_hashes": bag_hashes,
        "skel_5gram_hashes": skel_5gram_hashes,
        "raw_5gram_hashes": raw_5gram_hashes,
    }


def check_text_against_index(text: str, index: dict[str, set[str]]) -> dict[str, int]:
    ex_h = sha256_hex("exact:" + normalize_exact(text))
    bg_h = sha256_hex("bag:" + normalize_token_bag(text))
    sk_hits = sum(
        1 for ng in extract_skeleton_5grams(text)
        if sha256_hex("skel5:" + ng) in index["skel_5gram_hashes"]
    )
    rw_hits = sum(
        1 for ng in extract_raw_5grams(text)
        if sha256_hex("raw5:" + ng) in index["raw_5gram_hashes"]
    )
    return {
        "exact_hit": 1 if ex_h in index["exact_hashes"] else 0,
        "token_bag_hit": 1 if bg_h in index["bag_hashes"] else 0,
        "skeleton_5gram_hits": sk_hits,
        "raw_5gram_hits": rw_hits,
    }


def save_firewall_hashes(index: dict[str, set[str]], out_path: Path, metadata: dict[str, object]) -> None:
    payload = {
        "schema": "endexis-postfreeze-firewall/v1",
        "metadata": metadata,
        "counts": {k: len(v) for k, v in index.items()},
        "exact_hashes": sorted(index["exact_hashes"]),
        "bag_hashes": sorted(index["bag_hashes"]),
        "skel_5gram_hashes": sorted(index["skel_5gram_hashes"]),
        "raw_5gram_hashes": sorted(index["raw_5gram_hashes"]),
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_firewall_hashes(in_path: Path) -> dict[str, set[str]]:
    payload = json.loads(in_path.read_text(encoding="utf-8"))
    return {
        "exact_hashes": set(payload["exact_hashes"]),
        "bag_hashes": set(payload["bag_hashes"]),
        "skel_5gram_hashes": set(payload["skel_5gram_hashes"]),
        "raw_5gram_hashes": set(payload["raw_5gram_hashes"]),
    }
