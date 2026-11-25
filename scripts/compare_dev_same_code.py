#!/usr/bin/env python3
"""Scores models on oof_eval_v2_3 (N=132) under the same, current code, and compares the multiturn IR field by field.

v2.3's receipt (Pass@1 0.9697) was scored on 2026-11-11, before the strict compiler began rejecting unknown override
values. The multiturn slice's ground truth carries values like 'cleanly stepdown' (a word of filler before the unit),
so since then no model passes those 36 items, v2.3 included. A candidate is therefore compared with v2.3 under the
same code, and its multiturn IR with the ground truth: domain, element F1, override filters and excluded columns.

Usage (on the GPU host, in the training workspace):
  python scripts/compare_dev_same_code.py v2_3_0 <v2.3 merged dir> v2_5_0 <candidate merged dir>
Writes dev_same_code.json in the working directory.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_and_eval_v2_3_oof_and_full as v23  # noqa: E402
from discern_intelligence.schemas import NeuralCompilationIR  # noqa: E402

torch = v23.torch


def multiturn_ir(model, tok, items: list[dict]) -> dict:
    rows = []
    for it in items:
        p = (f"<|im_start|>system\n{v23.CANONICAL_SYSTEM_PROMPT}<|im_end|>\n"
             f"<|im_start|>user\n{it['prompt']}<|im_end|>\n<|im_start|>assistant\n")
        enc = tok(p, return_tensors="pt").to("cuda:0")
        with torch.no_grad():
            g = model.generate(**enc, max_new_tokens=384, do_sample=False, eos_token_id=tok.eos_token_id)
        ir = NeuralCompilationIR.parse_from_completion(tok.decode(g[0, enc.input_ids.shape[1]:], skip_special_tokens=True))
        gt = it["ground_truth_ir"]
        if ir is None:
            rows.append({"parsed": False})
            continue
        ps, gs = set(ir.selected_elements), set(gt["selected_elements"])
        pr, rc = len(ps & gs) / max(1, len(ps)), len(ps & gs) / max(1, len(gs))
        pe, ge = set(ir.exclude_columns), set(gt.get("exclude_columns", []))
        rows.append({"parsed": True, "domain": ir.da2_domain == gt["da2_domain"],
                     "element_f1": 2 * pr * rc / max(1e-9, pr + rc),
                     "override_exact": dict(ir.override_filters) == gt.get("override_filters", {}),
                     "exclude_exact": pe == ge, "exclude_jaccard": len(pe & ge) / max(1, len(pe | ge))})
    keys = ("parsed", "domain", "element_f1", "override_exact", "exclude_exact", "exclude_jaccard")
    return {"n": len(rows), **{k: round(sum(r.get(k, 0) for r in rows) / len(rows), 4) for k in keys}}


def main() -> int:
    oof = json.loads((v23.DATA_DIR / "oof_eval_v2_3.json").read_text())
    mt = [x for x in oof["items"] if x["slice"] == "multiturn_refinement"]
    out = {}
    args = sys.argv[1:]  # label model_dir [label model_dir ...]
    for i in range(0, len(args) - 1, 2):
        label, d = args[i], args[i + 1]
        tok = v23.AutoTokenizer.from_pretrained(d)
        m = v23.AutoModelForCausalLM.from_pretrained(d, dtype=torch.bfloat16).to("cuda:0")
        dev = v23.score_oof_items_on_gpu(m, tok, oof["items"], v23.CANONICAL_SYSTEM_PROMPT, torch.device("cuda:0"),
                                         max_new_tokens=384, legacy_mode=False, batch_size_gen=1)
        out[label] = {"model_dir": d, "dev_same_code": {k: v for k, v in dev.items() if k != "items"},
                      "multiturn_ir_vs_ground_truth": multiturn_ir(m, tok, mt)}
        print(label, json.dumps(out[label]["dev_same_code"]["by_slice"]),
              json.dumps(out[label]["multiturn_ir_vs_ground_truth"]), flush=True)
        del m
        torch.cuda.empty_cache()
    Path("dev_same_code.json").write_text(json.dumps(out, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
