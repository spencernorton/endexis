#!/usr/bin/env python3
"""Made-up change requests on uploaded DA2 files, labelled by endexis-chat's own rules.

Each case is a synthetic report or query (written by DA2ReportGenerator, no real export), the earlier messages of its
chat, the latest message, and what the chat's rules do with it. The model learns to reword a message the rules hold as
the plain ask it meant (or null when it asks for no change); the chat then puts that rewording through the same rules
before it offers it to the user. So every target here is checked: its plain ask, sent to the rules on the same file and
history, makes exactly the intended changes.

Slices: `accepted` (the rules made the intended changes from the message itself), `held_meant` (the rules made no
change, and the message meant one), `held_none` (no change, and none was meant). A message the rules read as some other
change is left out and counted as `misread` in the receipt.

Writes (rerunnable; the holdout is sealed 0444: a rerun checks it would build the same file and leaves it, and only
--reseal rewrites it):
  data/sft_train_v1.jsonl          rows of task `rewrite_upload_edit` replace any from an earlier run
  data/dpo_contrastive_v1.jsonl    pairs of provenance `endexis:synthetic_upload_edits` likewise
  data/eval_upload_edits_v1.jsonl  the sealed holdout (other phrasings, other reports)
  data/upload_edits_v1_firewall_hashes.json, data/upload_edits_v1_build_receipt.json

Usage: python scripts/build_upload_edit_cases.py --chat-app ../endexis-chat/app [--reseal]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
sys.path[:0] = [str(REPO / "src"), str(REPO / "scripts")]

from corpus_firewall import build_firewall_index, check_text_against_index, save_firewall_hashes  # noqa: E402
from discern_intelligence.da2_generator import DA2ReportGenerator  # noqa: E402

TASK = "rewrite_upload_edit"
PROVENANCE = "endexis:synthetic_upload_edits"
HOLDOUT = DATA / "eval_upload_edits_v1.jsonl"
FIREWALL = DATA / "upload_edits_v1_firewall_hashes.json"
RECEIPT = DATA / "upload_edits_v1_build_receipt.json"
SFT, DPO = DATA / "sft_train_v1.jsonl", DATA / "dpo_contrastive_v1.jsonl"
POSTFREEZE_FIREWALL = DATA / "postfreeze_v3_firewall_hashes.json"

# Reports to make files from: training and holdout files come from different requests.
TRAIN_REPORTS = [
    "Active inpatient census with attending physician and nurse unit, prompt on admit date",
    "Rehab therapy productivity report", "lab results for inpatients", "daily appointments by unit with appointment state",
    "medication administration record for the unit", "charges posted yesterday by revenue code",
    "emergency department arrivals today", "length of stay by nurse unit", "Braden scores for inpatients",
    "orders placed by physician with order status", "admissions by admit source", "vital signs for inpatients",
]
HOLD_REPORTS = [
    "discharges last month with discharge disposition and payer", "fall risk assessments on the unit",
    "diagnosis list for current inpatients", "patients by payer and financial class", "observation patients by unit",
    "physical therapy minutes this week",
]
OWNERS = ["Clinical_Reporting", "Quality_Analytics", "Rehab_Reporting", "Nursing_Informatics", "Therapy_Operations",
          "Case_Management", "Decision_Support", "Revenue_Integrity", "Patient_Flow", "Pharmacy_Analytics"]
STAND_INS = ["TBD", "to be confirmed", "not decided yet", "whoever owns it"]

# Plain words for DA2 columns (generic Cerner names only). Keys must be catalog names.
ALIASES = {
    "Enc Admit Source": ["admit source", "admission source", "source of admission"],
    "Enc Admit Type": ["admit type", "admission type"],
    "Enc Medical Service": ["medical service", "service line"],
    "Enc Financial Class": ["financial class"],
    "Enc Reason for Visit": ["reason for visit", "visit reason"],
    "Enc Loc Room": ["room", "room number"],
    "Enc Loc Bed": ["bed", "bed number"],
    "Enc Loc Facility": ["facility", "hospital"],
    "Enc Loc Nurse Unit": ["nurse unit", "nursing unit"],
    "Person Abs Birth Dt/Tm": ["birth date", "date of birth", "DOB"],
    "Person Sex": ["sex", "gender"],
    "Person Language": ["language", "preferred language"],
    "Person MRN": ["MRN", "medical record number"],
    "Enc Arrive Dt/Tm": ["arrival time", "arrival date"],
    "Enc Discharge Loc": ["discharge location"],
    "Enc Discharge Disp": ["discharge disposition", "disposition"],
    "Enc Attending Phys": ["attending", "attending physician", "attending doctor"],
    "Enc Admitting Phys": ["admitting physician", "admitting doctor"],
    "HP Plan Name": ["plan name", "health plan", "insurance plan"],
    "HP Financial Class": ["plan financial class"],
    "HP Plan Type": ["plan type"],
    "Dx 1 Desc": ["diagnosis description"],
    "Dx Nom Source Code": ["diagnosis code", "ICD code"],
    "CE Event": ["event name", "clinical event"],
    "CE Result Value": ["result value", "result"],
    "CE Result Units": ["result units"],
    "CE Normalcy": ["normalcy", "abnormal flag"],
    "Order Catalog": ["order name", "orderable"],
    "Order Status": ["order status"],
    "Order Dt/Tm": ["order date", "order time"],
    "Enc Type": ["encounter type", "visit type"],
    "Enc Status": ["encounter status", "visit status"],
    "Enc Inpatient Admit Dt/Tm": ["inpatient admit date", "admit date"],
    "Person Race": ["race"],
    "Per Addr City": ["city", "home city"],
    "Per Addr Zip": ["zip code", "postal code"],
    "Sch App Beg Dt/Tm": ["appointment time", "appointment start"],
    "Sch App State": ["appointment state", "appointment status"],
    "Sch Ev App Type": ["appointment type"],
    "Chg Description": ["charge description"],
    "Chg Service Dt/Tm": ["service date"],
    "Alg Substance Type": ["allergy type", "substance type"],
    "Enc Registration Dt/Tm": ["registration date", "reg date"],
    "Enc Discharge Dt/Tm": ["discharge date", "discharge time"],
    "Enc Financial Num": ["FIN", "financial number"],
    "Person Name Full Format": ["patient name", "full name"],
    "Enc Id": ["encounter id"],
    "Person Id": ["person id"],
    "Enc Active Ind": ["active indicator", "active flag"],
    "CE Result Status": ["result status"],
    "Prsnl Name Full Format": ["staff name", "provider name"],
    "Enc Pl Relationship Type": ["relationship type"],
    "CE Performed Prsnl Name": ["performed by"],
}
FILTER_ALIASES = {
    "Person Name Last": ["test patient", "last name"],
    "Enc Status": ["encounter status", "status"],
    "CE Result Status": ["result status"],
    "CE Valid Until Dt/Tm": ["valid until"],
    "Enc Type": ["encounter type", "visit type"],
    "CE Event": ["event"],
}
# "only" filters: field -> (plain words for the field, [(DA2 value, plain words for it)])
ONLY = {
    "Enc Type": (["encounter type", "visit type", ""], [("Inpatient", "inpatient"), ("Observation", "observation"),
                                                         ("Outpatient", "outpatient"), ("Emergency", "emergency")]),
    "Enc Admit Type": (["admit type", "admission type"], [("Elective", "elective"), ("Urgent", "urgent"),
                                                          ("Emergency", "emergency"), ("Trauma", "trauma")]),
    "Enc Financial Class": (["financial class"], [("Charity", "charity"), ("Commercial Insurance", "commercial insurance")]),
    "Enc Discharge Disp": (["discharge disposition"], [("Expired", "expired"),
                                                       ("Against Medical Advice", "against medical advice")]),
}

# Phrasings: (family, style, template). Family T is for training, H for the holdout only. Slots: {a} a plain name for
# the target column, {n} its DA2 name, {a2}/{n2} a second column, {f} a filter's DA2 field and {fa} a plain name for it,
# {vals} plain values, {fd} the only-filter's plain field words, {og} an owner group, {x} a stand-in.
T, H = "T", "H"
PHRASES = [
    # add a column that isn't there
    (T, "add", "add {n}"), (T, "add", "please add {n}"), (T, "add", "Could you add the {a}?"), (H, "add", "include {n} too"),
    (H, "add", "can you put {n} in?"), (T, "add", "Add {n}, please."), (T, "add", "add the {a}"),
    (T, "add", "include the {a} column"), (H, "add", "I'd like the {a} in there"), (T, "add", "show the {a} as well"),
    (H, "add", "throw in the {a}"), (T, "add", "we need a column for {a}"), (H, "add", "can we see the {a} too"),
    (T, "add", "add a {a} column"), (H, "add", "stick the {a} on the end"), (T, "add", "add {n} asap"),
    (T, "add", "add {n} for the monthly review"), (T, "add", "add the {a}, the managers want it"),
    (H, "add", "add {n}, I think we need it"), (H, "add", "add {n} before Friday"), (T, "add", "add the {a} so we can sort by it"),
    (H, "add", "add the {a} - cheers mate"), (T, "add", "add {n} 👍"), (H, "add", "add the {a} ✅"), (T, "add", "add {n} 🙏"),
    (T, "add", "Hi! Could we get the {a} on this one?"), (H, "add", "it's missing the {a}, add it"),
    (T, "add", "need the {a} column added"), (H, "add", "the {a} should be on here"),
    # remove a column that is there
    (T, "remove", "remove {n}"), (T, "remove", "drop {n}"), (T, "remove", "take out the {a}"),
    (H, "remove", "get rid of the {a} please"), (T, "remove", "delete the {a} column"), (H, "remove", "can we drop the {a}?"),
    (T, "remove", "lose the {a} column"), (H, "remove", "bin the {a}"), (T, "remove", "ditch the {a}"),
    (H, "remove", "strip out the {a}"), (T, "remove", "pull the {a} column"), (H, "remove", "cut the {a}"),
    (T, "remove", "I don't need the {a} anymore"), (T, "remove", "we don't use the {a}"), (H, "remove", "the {a} isn't needed"),
    (T, "remove", "don't keep the {a} column"), (H, "remove", "no need for the {a}"),
    (T, "remove", "nobody looks at the {a}, take it out"), (H, "remove", "we never use the {a} column"),
    (T, "remove", "remove {n} since nobody reads it"), (H, "remove", "remove the {a}, it's clutter"),
    (T, "remove", "remove {n} for now"), (H, "remove", "drop {n} asap"), (T, "remove", "remove {n} ❌"),
    (H, "remove", "drop the {a} 🙂"), (T, "remove", "the {a} column can go"), (H, "remove", "hide the {a} from it"),
    # ask about a column, change it only if it is or isn't there
    (T, "if_present_remove", "Is the {a} in it? If so remove it."), (H, "if_present_remove", "If the {a} is there, take it out."),
    (T, "if_present_remove", "if there's a {a} column, drop it"),
    (T, "if_absent_add", "Is the {a} in it? If not, add it."), (H, "if_absent_add", "If the {a} isn't there, add it."),
    (T, "if_absent_add", "add the {a} if it's missing"),
    # says not to, or asks nothing
    (T, "dont_add", "don't add the {a}"), (H, "dont_add", "please don't add {n}"), (T, "dont_add", "no need to add the {a}"),
    (H, "dont_add", "we won't need the {a} added"),
    (T, "dont_remove", "don't remove the {a}"), (H, "dont_remove", "leave the {a} where it is"),
    (T, "dont_remove", "keep the {a} as it is"), (H, "dont_remove", "please don't take the {a} out"),
    (T, "question", "Is the {a} in the report?"), (T, "question", "What does the {a} column show?"),
    (H, "question", "Does it have the {a}?"), (H, "question", "Why is the {a} there?"),
    (T, "question", "How many columns does it have?"), (H, "question", "Which filters are on it?"),
    (T, "question", "What's the owner group?"), (H, "question", "Can DA2 sort by the {a}?"),
    (T, "question", "what is {n}?"), (H, "question", "where does the {a} come from?"),
    (T, "unsure", "add the {a} or maybe not"), (T, "unsure", "should I remove the {a}?"),
    (H, "unsure", "maybe add the {a}? not sure"), (H, "unsure", "remove the {a}, or keep it?"),
    (T, "unsure", "thinking about adding the {a}"), (H, "unsure", "not sure about the {a}"),
    # after an earlier change
    (T, "and_too", "and the {a} too"), (H, "and_too", "same for the {a}"), (T, "and_too", "{a} as well"),
    (H, "and_too", "also the {a}"), (T, "and_too", "the {a} too please"),
    (T, "put_back", "put it back"), (H, "put_back", "actually keep the {a}"), (T, "put_back", "bring the {a} back"),
    (H, "put_back", "I need the {a} after all"), (T, "put_back", "oh wait, we need the {a}, put it back"),
    (T, "take_out_again", "actually take it out again"), (H, "take_out_again", "on second thought, drop the {a}"),
    (T, "take_out_again", "hmm, remove that one again"),
    (T, "undo", "undo"), (T, "undo", "take that back"), (H, "undo", "scratch that"), (T, "undo", "undo the last one please"),
    (H, "undo", "revert that"), (T, "undo", "go back one step"), (H, "undo", "undo that change"), (T, "undo", "oops, undo"),
    (H, "undo", "can you undo that?"),
    # only-filters
    (T, "only", "only {f} in ({vals})"), (T, "only", "just {vals} please"), (H, "only", "limit it to {vals}"),
    (T, "only", "only show {vals} encounters"), (H, "only", "filter to {vals}"), (T, "only", "I only want {vals}"),
    (H, "only", "keep just the {vals} ones"), (T, "only", "restrict it to {vals} {fd}"), (H, "only", "only {vals} 🙂"),
    (T, "only", "only the {vals} ones, thanks"),
    # remove a filter that is there
    (T, "remove_filter", "remove the filter on {f}"), (T, "remove_filter", "drop the {fa} filter"),
    (H, "remove_filter", "stop filtering on {fa}"), (T, "remove_filter", "I don't want it filtered by {fa}"),
    (H, "remove_filter", "take off the {fa} filter"), (T, "remove_filter", "no {fa} filter please"),
    (H, "remove_filter", "get rid of the {fa} restriction"),
    # owner group
    (T, "owner", "set the owner group to {og}"), (T, "owner", "the owner group should be {og}"),
    (H, "owner", "make {og} the owner"), (T, "owner", "owner group: {og}"), (H, "owner", "use {og} as the owner group please"),
    (T, "owner", "change the owner to {og}"), (H, "owner", "it belongs to {og}"), (T, "owner", "put it under {og}"),
    (T, "owner_none", "who owns this?"), (H, "owner_none", "the owner group is {x}"), (T, "owner_none", "owner group {x}"),
    (H, "owner_none", "not sure who the owner group is"),
    # two changes
    (T, "add_remove", "add the {a} and drop the {a2}"), (H, "add_remove", "lose the {a2} and add the {a}"),
    (T, "add_remove", "add {n} asap and remove {n2}"), (T, "add_add", "add the {a} and the {a2}"),
    (H, "add_add", "can we have the {a} plus the {a2}?"), (T, "remove_remove", "remove the {a} and the {a2}"),
    (H, "remove_remove", "drop both the {a} and the {a2}"),
]
NONE_STYLES = {"dont_add", "dont_remove", "question", "unsure", "owner_none"}
INTROS = ["", "", "", "Here is my census. ", "Here's our report. ", "This is the daily report. "]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha(kind: str, data: bytes) -> str:
    """The file's identity: a .dareport zip's members, not its bytes (zipfile stamps each member with the time)."""
    if kind != ".dareport":
        return hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return hashlib.sha256(b"".join(n.encode() + b"\0" + zf.read(n) + b"\0" for n in sorted(zf.namelist()))).hexdigest()


def ops_of(changes: list[str]) -> list[dict]:
    """The chat's change lines as structured changes."""
    out = []
    for c in changes:
        for rx, op in ((r"Added the column `(.+?)`\.", "add_column"), (r"Removed the column `(.+?)`\.", "remove_column"),
                       (r"(?:Added|Changed) the filter `(.+?)`\.", "only"), (r"Removed the filter on `(.+?)`\.",
                                                                              "remove_filter"),
                       (r"Owner group set to `(.+?)`\.", "owner_group")):
            if m := re.match(rx, c):
                out.append({"op": op, "target": m[1]})
                break
        else:
            raise ValueError(f"unknown change line: {c}")
    return out


class Rules:
    """endexis-chat's upload rules, run as the chat runs them."""

    def __init__(self, app: Path):
        os.environ.update(ENDEXIS_API_TOKEN="x" * 24, PUBLIC_URL="https://endexis.example", GES_KEY="",
                          FILES_DIR=tempfile.mkdtemp())
        sys.path.insert(0, str(app))
        import server  # noqa: PLC0415
        self.s = server
        git = lambda *a: subprocess.run(["git", "-C", str(app), *a], capture_output=True, text=True).stdout.strip()  # noqa: E731
        self.commit, self.dirty = git("rev-parse", "HEAD"), bool(git("status", "--porcelain", "--", "."))

    def run(self, up, edits: list[str]):
        """(files or None, said lines, whether the latest message changed the files, outcome)."""
        spent = self.s.Spent()
        try:
            files, said = self.s.replay(up, edits, spent=spent)
        except self.s.UploadHeld as exc:
            return None, [str(exc)], False, "held"
        except self.s.DA2FileError as exc:
            return None, [str(exc)], False, "refused"
        return files, said, spent.changed, "changed" if spent.changed else "no_change"

    def delta(self, up, edits: list[str]) -> list[dict] | None:
        """The structured changes the latest message makes, or None if it makes none."""
        before = self.run(up, edits[:-1])[0]
        after, _, changed, _ = self.run(up, edits)
        if not changed or before is None:
            return None
        if self.s.UNDO_RE.fullmatch(self.s.plain_text(edits[-1])):
            return [{"op": "undo", "target": ""}]
        return ops_of(after.changes[len(before.changes):])


def canonical(intent: list[dict]) -> str:
    parts = []
    for o in intent:
        parts.append({"add_column": "add {t}", "remove_column": "remove {t}", "only": "only {t}",
                      "remove_filter": "remove the filter on {t}", "owner_group": "set the owner group to {t}",
                      "undo": "undo"}[o["op"]].format(t=o["target"]))
    return ". ".join(parts)


def trace(ops: list[dict]) -> str:
    said = "; ".join(f"{o['op']} {o['target']}".strip() for o in ops) or "none"
    return f"<reasoning>\nintent: {said}\n</reasoning>"


def completion(plain: str | None, ops: list[dict]) -> str:
    return f"{trace(ops)}\n<output>\n{json.dumps({'plain': plain})}\n</output>"


def make_files(rules: Rules, requests: list[str], rng: random.Random) -> list[dict]:
    out = []
    for req in requests:
        owner = rng.choice(OWNERS)
        bundle = DA2ReportGenerator().generate(req, owner_group_display=owner)
        report = DA2ReportGenerator.build_dareport_zip_bytes(bundle, owner_group_display=owner)
        out.append({"request": req, "kind": ".dareport", "owner": owner, "data": report})
        out.append({"request": req, "kind": ".daquery", "owner": owner, "data": bundle.queries[0].daquery_xml.encode()})
    for f in out:
        f["sha"] = file_sha(f["kind"], f["data"])
        f["up"] = rules.s.Upload(f"upload{f['kind']}", f["data"], 0)
    return out


def columns(files) -> list[str]:
    return [c.name for _, _, cols, _ in files.queries() for c in cols]


def kept_filters(files) -> list[str]:
    return list(dict.fromkeys(f.field for *_, fs in files.queries() for f in fs if f.operator != "PROMPT"))


def history_for(rules: Rules, f: dict, rng: random.Random, steps: int) -> list[str]:
    """Earlier messages that each made one change: plain adds, removals and only-filters."""
    hist = []
    for _ in range(steps):
        state = rules.run(f["up"], hist)[0]
        if state is None:
            break
        have = columns(state)
        pick = rng.random()
        if pick < 0.45:
            cand = [n for n in ALIASES if n not in have]
            msg = f"add {rng.choice(cand)}"
        elif pick < 0.85:
            cand = [n for n in have if n in ALIASES]
            msg = f"remove {rng.choice(cand)}" if cand else "add Enc Admit Source"
        else:
            field = rng.choice(list(ONLY))
            vals = rng.sample(ONLY[field][1], k=min(2, len(ONLY[field][1])))
            msg = f"only {field} in ({', '.join(v for v, _ in vals)})"
        if rules.delta(f["up"], hist + [msg]):
            hist.append(msg)
    if hist and rng.random() < 0.4:
        hist[0] = rng.choice(INTROS[3:]) + hist[0][0].upper() + hist[0][1:]
    return hist


def last_change(rules: Rules, f: dict, hist: list[str]) -> dict | None:
    return (rules.delta(f["up"], hist) or [None])[-1] if hist else None


def plain_values(vals: list[tuple[str, str]]) -> str:
    words = [w for _, w in vals]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def build_case(rules: Rules, f: dict, style: str, template: str, rng: random.Random) -> dict | None:
    """Fills a phrasing for this file and a fresh history, and works out what was meant. None if it doesn't fit."""
    needs_hist = style in ("and_too", "put_back", "take_out_again", "undo")
    hist = history_for(rules, f, rng, rng.randint(1, 3) if needs_hist else rng.choice([0, 0, 1, 1, 2, 3]))
    state = rules.run(f["up"], hist)[0]
    if state is None:
        return None
    have = columns(state)
    present = [n for n in have if n in ALIASES]
    absent = [n for n in ALIASES if n not in have]
    slots: dict[str, str] = {"x": rng.choice(STAND_INS), "og": rng.choice(OWNERS)}
    intent: list[dict] = []

    def col(name: str, key: str = "") -> None:
        slots["n" + key], slots["a" + key] = name, rng.choice(ALIASES[name])

    last = last_change(rules, f, hist)
    if style in ("add", "dont_add", "if_absent_add") or style == "unsure" and "add" in template:
        col(rng.choice(absent))
        intent = [{"op": "add_column", "target": slots["n"]}] if style in ("add", "if_absent_add") else []
    elif style in ("remove", "dont_remove", "if_present_remove", "unsure", "question"):
        if not present:
            return None
        col(rng.choice(present))
        intent = [{"op": "remove_column", "target": slots["n"]}] if style in ("remove", "if_present_remove") else []
    elif style in ("if_absent_add_present", "if_present_remove_absent"):
        return None
    elif style == "and_too":
        if not last or last["op"] not in ("add_column", "remove_column"):
            return None
        pool = absent if last["op"] == "add_column" else [n for n in present if n != last["target"]]
        if not pool:
            return None
        col(rng.choice(pool))
        intent = [{"op": last["op"], "target": slots["n"]}]
    elif style in ("put_back", "take_out_again"):
        want = "remove_column" if style == "put_back" else "add_column"
        if not last or last["op"] != want or last["target"] not in ALIASES:
            return None
        col(last["target"])
        intent = [{"op": "add_column" if style == "put_back" else "remove_column", "target": last["target"]}]
    elif style == "undo":
        if not last:
            return None
        intent = [{"op": "undo", "target": ""}]
    elif style == "only":
        field = rng.choice(list(ONLY))
        fwords, values = ONLY[field]
        if "{fd}" in template or "{f}" in template:
            pass
        elif field != "Enc Type":  # "just inpatients" names Enc Type's values only
            field, (fwords, values) = "Enc Type", ONLY["Enc Type"]
        vals = rng.sample(values, k=rng.choice([1, 2, 2]) if len(values) > 1 else 1)
        slots.update(f=field, fd=rng.choice([w for w in fwords if w] or [field]), vals=plain_values(vals))
        if "{f}" in template:
            slots["vals"] = ", ".join(v for v, _ in vals)
        intent = [{"op": "only", "target": f"{field} in ({', '.join(v for v, _ in vals)})"}]
    elif style == "remove_filter":
        pool = [x for x in kept_filters(state) if x in FILTER_ALIASES]
        if not pool:
            return None
        field = rng.choice(pool)
        slots.update(f=field, fa=rng.choice(FILTER_ALIASES[field]))
        intent = [{"op": "remove_filter", "target": field}]
    elif style == "owner":
        intent = [{"op": "owner_group", "target": slots["og"]}]
    elif style == "owner_none":
        intent = []
    elif style in ("add_remove", "add_add", "remove_remove"):
        if style == "add_add":
            pick = rng.sample(absent, 2)
        elif style == "remove_remove":
            if len(present) < 2:
                return None
            pick = rng.sample(present, 2)
        else:
            if not present:
                return None
            pick = [rng.choice(absent), rng.choice(present)]
        col(pick[0])
        col(pick[1], "2")
        kinds = {"add_remove": ("add_column", "remove_column"), "add_add": ("add_column", "add_column"),
                 "remove_remove": ("remove_column", "remove_column")}[style]
        intent = [{"op": k, "target": n} for k, n in zip(kinds, pick)]
    if style in ("if_present_remove", "if_absent_add"):
        # the same phrasing about a column on the other side: nothing to do
        if rng.random() < 0.4:
            col(rng.choice(absent if style == "if_present_remove" else present or absent))
            intent = []
    try:
        message = template.format(**slots)
    except KeyError:
        return None
    if message[:1].islower() and rng.random() < 0.3:
        message = message[0].upper() + message[1:]

    expected: list[dict] = []
    plain = None
    if intent:
        plain = canonical(intent)
        expected = rules.delta(f["up"], hist + [plain]) or []
        if [o["op"] for o in expected] != [o["op"] for o in intent]:
            return None  # the file can't take it (a layout that refuses, a value Endexis doesn't know)
        plain = canonical(expected)  # the names as the rules write them
    got = rules.delta(f["up"], hist + [message])
    outcome = rules.run(f["up"], hist + [message])[3]
    if got is None:
        slice_ = "held_meant" if intent else "held_none"
    elif got == expected and intent:
        slice_ = "accepted"
    else:
        slice_ = "misread"
    prompt = rules.s.edit_prompt(state, hist, message)
    return {"style": style, "template": template, "message": message, "history": hist, "prompt": prompt,
            "plain": plain, "expected": expected, "got": got, "slice": slice_, "outcome": outcome,
            "file": {"request": f["request"], "kind": f["kind"], "owner": f["owner"], "sha256": f["sha"]}}


def generate(rules: Rules, family: str, files: list[dict], n: int, rng: random.Random) -> tuple[list[dict], Counter]:
    phrases = [(s, t) for fam, s, t in PHRASES if fam == family]
    out, seen, misread = [], set(), Counter()
    misread.examples = []  # type: ignore[attr-defined]
    tries = 0
    while len(out) < n and tries < n * 6:
        tries += 1
        style, template = rng.choice(phrases)
        case = build_case(rules, rng.choice(files), style, template, rng)
        if case is None or case["prompt"] in seen:
            continue
        seen.add(case["prompt"])
        if case["slice"] == "misread":
            misread[style] += 1
            misread.examples.append({"history": case["history"], "message": case["message"], "meant": case["expected"],  # type: ignore[attr-defined]
                                     "rules_made": case["got"]})
            continue
        out.append(case)
    return out, misread


def balance(cases: list[dict], rng: random.Random, accepted_share: float) -> list[dict]:
    """Keeps every held case and accepted ones up to `accepted_share` of the total."""
    held = [c for c in cases if c["slice"] != "accepted"]
    acc = [c for c in cases if c["slice"] == "accepted"]
    rng.shuffle(acc)
    keep = int(len(held) * accepted_share / (1 - accepted_share))
    return held + acc[:keep]


def rejected_for(case: dict, rng: random.Random) -> tuple[str | None, str] | None:
    """A plausible wrong answer and what makes it wrong."""
    exp, style = case["expected"], case["style"]
    if not exp:  # a proposal where none was meant
        n = re.search(r"`([^`]+)`|the (\w[\w ]*?)(?: column)?[?.]?$", case["message"])
        target = next((name for name in ALIASES if name.lower() in case["prompt"].lower()
                       and any(a in case["message"].lower() for a in ALIASES[name])), None)
        if style == "owner_none":
            return "set the owner group to TBD", "stand_in_owner"
        if target:
            verb = "add" if style in ("dont_add",) or "add" in case["message"].lower() else "remove"
            return f"{verb} {target}", "proposed_when_none_meant"
        return None if n is None else ("undo", "proposed_when_none_meant")
    o = exp[0]
    if o["op"] in ("add_column", "remove_column") and rng.random() < 0.5:
        flip = "remove" if o["op"] == "add_column" else "add"
        return canonical([{"op": "remove_column" if flip == "remove" else "add_column", "target": o["target"]}]
                         + exp[1:]), "opposite_change"
    if o["op"] in ("add_column", "remove_column") and rng.random() < 0.5:
        other = rng.choice([n for n in ALIASES if n != o["target"]])
        return canonical([{"op": o["op"], "target": other}] + exp[1:]), "wrong_column"
    return None, "refused_a_meant_change"


def row_sft(case: dict) -> dict:
    return {"task": TASK, "provenance": PROVENANCE, "instruction": case["prompt"],
            "response": {"plain": case["plain"]}, "reasoning_trace": trace(case["expected"]), "formatted_completion": completion(case["plain"], case["expected"]),
            "meta": {k: case[k] for k in ("style", "slice", "outcome", "file")}}


def norm_overlap(train: list[dict], hold: list[dict]) -> tuple[list[dict], dict]:
    """Drops holdout cases whose latest message or prompt overlaps training (exact, token bag or 5-gram skeleton)."""
    msg_idx = build_firewall_index(c["message"] for c in train)
    prompt_idx = build_firewall_index(c["prompt"] for c in train)
    kept, dropped = [], Counter()
    for c in hold:
        m = check_text_against_index(c["message"], msg_idx)
        p = check_text_against_index(c["prompt"], prompt_idx)
        hit = next((k for k, v in (("message_exact", m["exact_hit"]), ("message_bag", m["token_bag_hit"]),
                                   ("message_skeleton", m["skeleton_5gram_hits"]), ("prompt_exact", p["exact_hit"]),
                                   ("prompt_bag", p["token_bag_hit"])) if v), None)
        if hit:
            dropped[hit] += 1
        else:
            kept.append(c)
    return kept, dict(dropped)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-app", required=True, type=Path, help="endexis-chat's app/ directory (the rules)")
    ap.add_argument("--train", type=int, default=3200)
    ap.add_argument("--hold", type=int, default=900)
    ap.add_argument("--dpo", type=int, default=650, help="pairs to keep (the v2.3 corpus has 648)")
    ap.add_argument("--seed", type=int, default=1664)
    ap.add_argument("--reseal", action="store_true", help="rewrite the sealed holdout")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    rules = Rules(args.chat_app.resolve())
    train_files = make_files(rules, TRAIN_REPORTS, rng)
    hold_files = make_files(rules, HOLD_REPORTS, rng)

    train_raw, train_misread = generate(rules, T, train_files, args.train, rng)
    hold_raw, hold_misread = generate(rules, H, hold_files, args.hold, rng)
    train = balance(train_raw, rng, 0.25)
    hold, dropped = norm_overlap(train, balance(hold_raw, rng, 0.2))
    rng.shuffle(train)
    hold.sort(key=lambda c: sha(c["prompt"]))

    # the v2.3 sealed holdout must not overlap the new rows either (its hashes only; the file is never opened)
    pf = json.loads(POSTFREEZE_FIREWALL.read_text())
    pf_idx = {k: set(pf[k]) for k in ("exact_hashes", "bag_hashes", "skel_5gram_hashes", "raw_5gram_hashes")}
    pf_hits = Counter()
    for c in train:
        r = check_text_against_index(c["prompt"], pf_idx)
        pf_hits.update({k: int(v) for k, v in r.items()})
    # the same gate as the v3 holdout build: shared column names (raw 5-grams) are expected, phrasings are not
    assert not (pf_hits["exact_hit"] or pf_hits["token_bag_hit"] or pf_hits["skeleton_5gram_hits"]), pf_hits

    sft_rows = [row_sft(c) for c in train]
    dpo_rows, modes = [], Counter()
    for i, c in enumerate(c for c in train if c["slice"] != "accepted"):
        rej = rejected_for(c, rng)
        if not rej or rej[0] == c["plain"]:
            continue
        modes[rej[1]] += 1
        dpo_rows.append({"scenario_id": f"upload_edit_dpo_{i:04d}", "provenance": PROVENANCE, "failure_mode": rej[1],
                         "oof_fold": None, "prompt": c["prompt"],
                         "chosen": {"plain": c["plain"], "reasoning_trace": trace(c["expected"])},
                         "rejected": {"plain": rej[0], "antipattern": rej[1]}, "chosen_formatted": completion(c["plain"], c["expected"]),
                         "rejected_formatted": completion(rej[0], [])})

    rng.shuffle(dpo_rows)
    dpo_rows = sorted(dpo_rows[:args.dpo], key=lambda r: r["scenario_id"])
    modes = Counter(r["failure_mode"] for r in dpo_rows)

    def rewrite(path: Path, keep, new: list[dict]) -> int:
        old = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        rows = [r for r in old if keep(r)] + new
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return len(rows)

    n_sft = rewrite(SFT, lambda r: r.get("task") != TASK, sft_rows)
    n_dpo = rewrite(DPO, lambda r: r.get("provenance") != PROVENANCE, dpo_rows)

    hold_rows = [{"eval_id": f"ue1_{i:04d}", "slice": c["slice"], "style": c["style"], "prompt": c["prompt"],
                  "history": c["history"], "message": c["message"], "expected_plain": c["plain"],
                  "expected_changes": c["expected"], "rules_outcome": c["outcome"], "file": c["file"]}
                 for i, c in enumerate(hold)]
    hold_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in hold_rows)
    hold_sha = sha(hold_text)
    if HOLDOUT.exists() and not args.reseal:  # restoring the training rows (after a corpus rebuild) keeps the seal
        if hashlib.sha256(HOLDOUT.read_bytes()).hexdigest() != hold_sha:
            sys.exit(f"{HOLDOUT.name} is sealed and this build differs from it; pass --reseal to replace it")
    else:
        if HOLDOUT.exists():
            HOLDOUT.chmod(0o644)
        HOLDOUT.write_text(hold_text)
        HOLDOUT.chmod(0o444)
    save_firewall_hashes(build_firewall_index(c["message"] for c in hold), FIREWALL,
                         {"holdout_file": f"data/{HOLDOUT.name}", "holdout_sha256": hold_sha, "items": len(hold_rows),
                          "hashed_text": "the latest message of each case"})

    count = lambda cs, key: dict(sorted(Counter(c[key] for c in cs).items()))  # noqa: E731
    receipt = {
        "schema": "endexis-upload-edit-cases/v1",
        "labelled_by": {"repo": "endexis-chat", "commit": rules.commit, "uncommitted_changes": rules.dirty, "functions": ["replay", "apply_message",
                                                                                     "edit_prompt"]},
        "seed": args.seed,
        "files": {"train_requests": TRAIN_REPORTS, "holdout_requests": HOLD_REPORTS, "kinds": [".dareport", ".daquery"]},
        "phrasings": {"train": sum(p[0] == T for p in PHRASES), "holdout": sum(p[0] == H for p in PHRASES)},
        "train": {"sft_rows": len(sft_rows), "dpo_pairs": len(dpo_rows), "by_slice": count(train, "slice"),
                  "by_style": count(train, "style"), "dpo_failure_modes": dict(modes)},
        "holdout": {"items": len(hold_rows), "sha256": hold_sha, "by_slice": count(hold, "slice"),
                    "by_style": count(hold, "style"), "dropped_for_overlap": dropped},
        "known_gaps": ["training and holdout share the plain-word table for column names (ALIASES): the holdout tests "
                       "new phrasings, files and histories, not new words for a column"],
        "misread_by_rules": {"train_phrasings": dict(train_misread), "holdout_phrasings": dict(hold_misread),
                             "examples": (train_misread.examples + hold_misread.examples)[:30],
                             "note": "messages the rules read as some other change; left out of both sets"},
        "overlap_new_rows_vs_postfreeze_v3": {k: v for k, v in pf_hits.items()},
        "corpus_totals": {"sft_train_v1": n_sft, "dpo_contrastive_v1": n_dpo},
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: receipt[k] for k in ("train", "holdout", "misread_by_rules", "corpus_totals")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
