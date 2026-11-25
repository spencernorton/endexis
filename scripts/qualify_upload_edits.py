#!/usr/bin/env python3
"""Scores a served Endexis model on the sealed upload-edit holdout (data/eval_upload_edits_v1.jsonl), one shot.

  generate  sends each holdout prompt to an OpenAI-style endpoint (a llama-server on the GGUF as served) with the
            chat's own request settings, and writes the raw replies. Run it where the holdout is; only prompts leave.
  score     runs each reply through endexis-chat's own `suggestion()` (the model call stubbed with the reply), so a
            rewording counts only if the chat would show it, and compares the changes the rules make from it with
            the expected ones. Also scores the rules alone (no model), the chat today.

Usage:
  python scripts/qualify_upload_edits.py generate --endpoint http://127.0.0.1:18946 --label v2_5_q4 --out preds.jsonl
  python scripts/qualify_upload_edits.py score --chat-app ../endexis-chat/app --preds preds.jsonl [--preds base.jsonl]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOLDOUT = REPO / "data" / "eval_upload_edits_v1.jsonl"
RECEIPT = REPO / "data" / "upload_edits_v1_build_receipt.json"
sys.path[:0] = [str(REPO / "src"), str(REPO / "scripts")]

from discern_intelligence.da2_generator import DA2ReportGenerator  # noqa: E402
from discern_intelligence.neural_compiler import CANONICAL_SYSTEM_PROMPT  # noqa: E402


def holdout() -> list[dict]:
    raw = HOLDOUT.read_bytes()
    want = json.loads(RECEIPT.read_text())["holdout"]["sha256"]
    assert hashlib.sha256(raw).hexdigest() == want, "the sealed holdout changed since it was built"
    return [json.loads(ln) for ln in raw.decode().splitlines() if ln.strip()]


def generate(args) -> None:
    out = []
    for item in holdout():
        body = {"model": args.model, "temperature": 0.0, "top_p": 0.9, "max_tokens": 384,  # as the chat asks
                "messages": [{"role": "system", "content": CANONICAL_SYSTEM_PROMPT},
                             {"role": "user", "content": item["prompt"]}]}
        req = urllib.request.Request(f"{args.endpoint.rstrip('/')}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            msg = json.loads(resp.read())["choices"][0]["message"]
        rc, cc = (msg.get("reasoning_content") or "").strip(), (msg.get("content") or "").strip()
        raw = f"<reasoning>\n{rc}\n</reasoning>\n{cc}".strip() if rc and "<reasoning>" not in cc else cc  # model_chat
        out.append({"eval_id": item["eval_id"], "raw": raw, "ms": round((time.perf_counter() - t) * 1000, 1)})
    Path(args.out).write_text("".join(json.dumps({"label": args.label, **o}) + "\n" for o in out))
    print(f"{len(out)} replies -> {args.out}")


def bag(pairs: list[tuple[list, list]]) -> dict:
    tp = fp = fn = 0
    for got, want in pairs:
        g, w = Counter(map(json.dumps, got)), Counter(map(json.dumps, want))
        tp += sum((g & w).values())
        fp += sum((g - w).values())
        fn += sum((w - g).values())
    p, r = tp / max(1, tp + fp), tp / max(1, tp + fn)
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / max(1e-9, p + r), 4)}


def score_one(rules, files: dict, item: dict, raw: str | None) -> dict:
    """What the chat does with this reply: whether it shows a rewording, and the changes the rules make from it."""
    s = rules.s
    up = files[item["file"]["sha256"]]
    edits = item["history"] + [item["message"]]
    if rules.run(up, edits)[2]:  # the rules made a change from the message, and the chat doesn't ask the model
        made = rules.delta(up, edits) or []
        return {"asked": False, "parsed": False, "shown": False, "made": made, "right": made == item["expected_changes"]}
    seen = []

    def stub(prompt, timeout, system=None, temperature=None):
        seen.append((prompt, system, temperature))
        return raw

    s.model_chat, s.UPLOAD_SUGGEST, s.GES_KEY = stub, True, "qualify"
    tip = s.suggestion(up, edits, s.Spent()) if raw is not None else ""
    if seen:  # none when the chat keeps the message from the model (an identifier, or a "don't change" message)
        assert seen[0][0] == item["prompt"] and seen[0][1] == CANONICAL_SYSTEM_PROMPT, item["eval_id"]
    plain = s.edit_output(raw) if seen else None
    null = bool(seen) and plain is None and '"plain": null' in raw.replace('"plain":null', '"plain": null')
    made = (rules.delta(up, item["history"] + [plain]) or []) if tip else []
    return {"asked": bool(seen), "parsed": plain is not None or null, "shown": bool(tip), "made": made,
            "right": made == item["expected_changes"]}


def summarize(item_scores: list[tuple[dict, dict]]) -> dict:
    by = {}
    for sl in ("held_meant", "held_none", "accepted"):
        sub = [(i, r) for i, r in item_scores if i["slice"] == sl]
        if not sub:
            continue
        n = len(sub)
        asked = [r for _, r in sub if r.get("asked")]
        d = {"n": n, "asked_rate": round(len(asked) / n, 4),
             "parse_rate": round(sum(r["parsed"] for r in asked) / len(asked), 4) if asked else None,  # of those asked
             "shown_rate": round(sum(r["shown"] for _, r in sub) / n, 4)}
        if sl == "held_none":
            d["false_suggestion_rate"] = d["shown_rate"]
            neg = [(i, r) for i, r in sub if i["style"] in ("dont_add", "dont_remove")]
            d["false_suggestion_rate_on_dont"] = {"n": len(neg), "rate": round(sum(r["shown"] for _, r in neg)
                                                                              / max(1, len(neg)), 4)}
        elif sl == "accepted":  # the rules' own changes: the model isn't asked
            d["exact_match"] = round(sum(r["right"] for _, r in sub) / n, 4)
        else:
            d["exact_match"] = round(sum(r["shown"] and r["right"] for _, r in sub) / n, 4)
            d["wrong_suggestion_rate"] = round(sum(r["shown"] and not r["right"] for _, r in sub) / n, 4)
            d["bag"] = bag([(r["made"], i["expected_changes"]) for i, r in sub])
        by[sl] = d
    held = [(i, r) for i, r in item_scores if i["slice"] != "accepted"]
    # the chat end to end on held messages: a right suggestion helps; a wrong one or one where none was meant is noise
    return {"by_slice": by, "held_messages": {
        "n": len(held), "right_suggestion": round(sum(r["shown"] and r["right"] and i["slice"] == "held_meant"
                                                       for i, r in held) / max(1, len(held)), 4),
        "unwanted_suggestion": round(sum(r["shown"] and not (r["right"] and i["slice"] == "held_meant")
                                         for i, r in held) / max(1, len(held)), 4)}}


def score(args) -> None:
    from build_upload_edit_cases import Rules, file_sha  # noqa: PLC0415
    rules = Rules(Path(args.chat_app).resolve())
    items = holdout()
    files = {}
    for it in items:
        f = it["file"]
        if f["sha256"] not in files:
            b = DA2ReportGenerator().generate(f["request"], owner_group_display=f["owner"])
            data = (DA2ReportGenerator.build_dareport_zip_bytes(b, owner_group_display=f["owner"])
                    if f["kind"] == ".dareport" else b.queries[0].daquery_xml.encode())
            assert file_sha(f["kind"], data) == f["sha256"], f"{f['request']} no longer builds the same file"
            files[f["sha256"]] = rules.s.Upload(f"upload{f['kind']}", data, 0)
    report = {"holdout": {"file": HOLDOUT.name, "items": len(items), "chat_commit": rules.commit,
                          "chat_uncommitted_changes": rules.dirty},
              "rules_only": summarize([(it, score_one(rules, files, it, None)) for it in items])}
    for path in args.preds:
        preds = {p["eval_id"]: p for p in map(json.loads, Path(path).read_text().splitlines())}
        assert set(preds) == {it["eval_id"] for it in items}, f"{path} doesn't cover the holdout"
        label = next(iter(preds.values()))["label"]
        ms = sorted(p["ms"] for p in preds.values())
        report[label] = summarize([(it, score_one(rules, files, it, preds[it["eval_id"]]["raw"])) for it in items])
        report[label]["latency_ms"] = {"p50": ms[len(ms) // 2], "p95": ms[int(len(ms) * 0.95)]}
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--endpoint", required=True)
    g.add_argument("--model", default="endexis")
    g.add_argument("--label", required=True)
    g.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("--chat-app", required=True)
    s.add_argument("--preds", action="append", default=[])
    s.add_argument("--out")
    args = ap.parse_args()
    (generate if args.cmd == "generate" else score)(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
