#!/usr/bin/env python3
"""
Deterministic, Receipt-Backed Model Comparison Chart Generator for Discern Intelligence
(endexis).

Compares Discern-IQ (Qwen3-0.6B Stage-2 DPO + Verifier Routing & V1 Baseline)
head-to-head against Anthropic Claude Sonnet 4.6 (Catalog-RAG & Zero-Shot) and
OpenAI GPT (gpt-5.6-terra Heavy & Fast Tiers, Catalog-RAG & Zero-Shot) across
all 6 zero-leakage holdout evaluation dimensions.

Outputs high-DPI PNG and vector PDF to `docs/figures/`.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Patch
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent

BG_CANVAS = "#0D1117"
BG_PANEL = "#161B22"
BG_HIGHLIGHT = "#0F2922"
BORDER_COLOR = "#30363D"
GRID_COLOR = "#21262D"
TEXT_MAIN = "#F0F6FC"
TEXT_SUB = "#8B949E"

C_DISCERN_DPO = "#10B981"
C_DISCERN_DPO_EDGE = "#34D399"
C_DISCERN_V1 = "#3B82F6"
C_CLAUDE_RAG = "#A855F7"
C_GPT_HEAVY_RAG = "#38BDF8"
C_GPT_FAST_RAG = "#F59E0B"
C_CLAUDE_ZS = "#6366F1"
C_GPT_ZS = "#64748B"


def style_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG_PANEL)
    ax.set_axisbelow(True)
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.8, linestyle="--")
    ax.tick_params(axis="both", colors=TEXT_SUB, labelsize=8.8, length=0, pad=6)
    for spine in ax.spines.values():
        spine.set_edgecolor(BORDER_COLOR)
        spine.set_linewidth(1.0)


def extract_model_metrics(entry: dict[str, Any], is_discern: bool = False) -> dict[str, float]:
    if is_discern:
        fv = entry["holdout_filter_value_routing"]
        cv = entry["canonical_and_holdout_verifiers"]
        ccl_rpt = 100.0 * float(cv.get("rptdesign_xml_pass_at_1", 1.0))
        d_dict = {
            "fv_acc1": round(100.0 * float(fv["acc_at_1"]), 1),
            "cv_exact": round(100.0 * float(fv["code_value_exact_match_rate"]), 1),
            "elem_f1": round(100.0 * float(cv["holdout_mean_element_f1"]), 1),
            "xml_pass1": round(100.0 * float(cv["cclquery_xml_pass_at_1"]), 1),
            "sql_pass1": round(100.0 * float(cv["ejs_v500_sql_pass_at_1"]), 1),
            "e2e_pass1": round(ccl_rpt, 1),
        }
    else:
        fv = entry["holdout_filter_values"]
        hq = entry["holdout_queries"]
        fr = entry["frozen_scenarios"]
        e2e = 0.5 * (float(fr.get("ccl_prg_pass_at_1", 0.0)) + float(fr.get("rptdesign_xml_pass_at_1", 0.0)))
        d_dict = {
            "fv_acc1": round(100.0 * float(fv.get("acc_at_1", 0.0)), 1),
            "cv_exact": round(100.0 * float(fv.get("code_value_exact_match_rate", 0.0)), 1),
            "elem_f1": round(100.0 * float(hq.get("mean_element_f1", 0.0)), 1),
            "xml_pass1": round(100.0 * float(hq.get("cclquery_xml_pass_at_1", 0.0)), 1),
            "sql_pass1": round(100.0 * float(hq.get("ejs_v500_sql_pass_at_1", 0.0)), 1),
            "e2e_pass1": round(100.0 * e2e, 1),
        }
    d_dict["composite_6d"] = round(
        float(np.mean([
            d_dict["fv_acc1"],
            d_dict["cv_exact"],
            d_dict["elem_f1"],
            d_dict["xml_pass1"],
            d_dict["sql_pass1"],
            d_dict["e2e_pass1"],
        ])),
        1,
    )
    return d_dict


def render_comparison_chart(output_dir: Path, artifact_dir: Path | None = None) -> tuple[Path, Path]:
    p1_data = json.loads((REPO_ROOT / "data" / "phase1_frontier_vs_discern_iq_baseline.json").read_text(encoding="utf-8"))["phase1_baseline_comparison"]
    p5_data = json.loads((REPO_ROOT / "data" / "phase5_merged_model_and_endpoint_eval.json").read_text(encoding="utf-8"))["phase5_live_neural_deployment_and_evaluation"]["full_evaluation_suite"]

    model_specs = [
        ("Discern-IQ v1.1 (Qwen3-0.6B DPO + Verifier)", "Discern-IQ DPO", C_DISCERN_DPO, extract_model_metrics(p5_data, is_discern=True)),
        ("Discern-IQ V1 Baseline (51110a1)", "Discern-IQ V1", C_DISCERN_V1, extract_model_metrics(p1_data["discern_iq_v1_baseline_51110a1"], is_discern=True)),
        ("Claude Sonnet 4.6 (Catalog-RAG)", "Claude (RAG)", C_CLAUDE_RAG, extract_model_metrics(p1_data["retrieval_augmented_claude_sonnet"])),
        ("OpenAI gpt-6.1-sol (nt-heavy, Catalog-RAG)", "gpt-6.1-sol (RAG)", C_GPT_HEAVY_RAG, extract_model_metrics(p1_data["retrieval_augmented_nt_heavy"])),
        ("OpenAI gpt-5.6-luna (nt-fast, Catalog-RAG)", "gpt-5.6-luna (RAG)", C_GPT_FAST_RAG, extract_model_metrics(p1_data["retrieval_augmented_nt_fast"])),
        ("Claude Sonnet 4.6 (Zero-Shot)", "Claude (ZS)", C_CLAUDE_ZS, extract_model_metrics(p1_data["zero_shot_claude_sonnet"])),
        ("OpenAI gpt-5.6-luna (nt-fast, Zero-Shot)", "gpt-5.6-luna (ZS)", C_GPT_ZS, extract_model_metrics(p1_data["zero_shot_nt_fast"])),
    ]

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]
    fig = plt.figure(figsize=(24.0, 12.0), facecolor=BG_CANVAS)
    fig.suptitle(
        "Discern-IQ vs. OpenAI GPT & Anthropic Claude Across 6 Zero-Leakage DA2 / V500 / BIRT Dimensions",
        fontsize=17.5, fontweight="bold", color=TEXT_MAIN, y=0.972,
    )
    fig.text(
        0.5, 0.935,
        "Evaluated on Stratified 80/20 Zero-Leakage Holdout Filter Values (N=140 / 28), Holdout Production DA2 Queries (N=4), & Frozen End-to-End Scenarios (N=14 / 4)",
        ha="center", fontsize=11.2, style="italic", color=TEXT_SUB,
    )

    gs = GridSpec(
        2, 5, figure=fig,
        width_ratios=[1.65, 0.08, 1.0, 1.0, 1.0],
        wspace=0.18, hspace=0.38,
        top=0.86, bottom=0.12, left=0.19, right=0.985,
    )

    # Left Composite Panel
    ax_left = fig.add_subplot(gs[:, 0])
    style_ax(ax_left)
    y_pos = np.arange(len(model_specs))
    ax_left.axhspan(-0.5, 1.5, color=BG_HIGHLIGHT, alpha=0.75, zorder=1)
    comp_vals = [m[3]["composite_6d"] for m in model_specs]
    colors = [m[2] for m in model_specs]
    ax_left.barh(y_pos, comp_vals, height=0.55, color=colors, zorder=3)
    ax_left.set_yticks(y_pos)
    ax_left.set_yticklabels([m[0] for m in model_specs], fontsize=10.2, fontweight="bold", color=TEXT_MAIN)
    ax_left.invert_yaxis()
    ax_left.set_xlim(0, 125)
    ax_left.set_xticks([0, 20, 40, 60, 80, 100])
    ax_left.set_xticklabels(["0%", "20%", "40%", "60%", "80%", "100%"])
    ax_left.set_xlabel("6-Dimension Composite Mean Accuracy (%)", fontsize=10.0, color=TEXT_SUB, labelpad=8)
    ax_left.set_title(
        "1. 6-Dimension Composite Mean (%)\n(Filter Acc@1, CODE_VALUE, Element F1, XML, SQL, BIRT/CCL)",
        fontsize=11.4, fontweight="bold", color=TEXT_MAIN, pad=10,
    )
    for idx, (full_name, _, _, mdict) in enumerate(model_specs):
        val = mdict["composite_6d"]
        is_top = idx < 2
        ax_left.text(
            val + 1.8, idx,
            f"{val:.1f}%",
            va="center", ha="left",
            fontsize=9.6,
            fontweight="bold" if is_top else "normal",
            color=TEXT_MAIN if is_top else "#CBD5E1",
            zorder=6,
        )

    panel_defs = [
        (0, 2, "2A. Filter Value Routing Acc@1 (%)", "fv_acc1"),
        (0, 3, "2B. Millennium CODE_VALUE Exact Match (%)", "cv_exact"),
        (0, 4, "2C. Holdout Query Element F1 (%)", "elem_f1"),
        (1, 2, "2D. <cclquery> / .daquery XML Pass@1 (%)", "xml_pass1"),
        (1, 3, "2E. EJS v3.17.3 V500 SQL Pass@1 (%)", "sql_pass1"),
        (1, 4, "2F. End-to-End .rptdesign & CCL Pass@1 (%)", "e2e_pass1"),
    ]

    for row, col, title, metric_key in panel_defs:
        ax = fig.add_subplot(gs[row, col])
        style_ax(ax)
        ax.axhspan(-0.5, 1.5, color=BG_HIGHLIGHT, alpha=0.65, zorder=1)
        vals = [m[3][metric_key] for m in model_specs]
        ax.barh(y_pos, vals, height=0.56, color=colors, zorder=3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels([""] * len(model_specs))
        ax.invert_yaxis()
        ax.set_xlim(0, 162)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
        ax.set_title(title, fontsize=10.3, fontweight="bold", color=TEXT_MAIN, pad=8)
        for idx, (_, short_lbl, _, mdict) in enumerate(model_specs):
            v = mdict[metric_key]
            is_top = idx < 2
            ax.text(
                v + 1.8, idx,
                f"{v:.1f}% ({short_lbl})",
                va="center", ha="left",
                fontsize=7.9,
                fontweight="bold" if is_top else "normal",
                color=TEXT_MAIN if is_top else "#CBD5E1",
                zorder=6,
            )

    legend_handles = [
        Patch(facecolor=C_DISCERN_DPO, edgecolor=C_DISCERN_DPO_EDGE, label="Discern-IQ v1.1 (Qwen3-0.6B Stage-2 DPO + Verifier Routing — Live DA2 Import Verified)"),
        Patch(facecolor=C_DISCERN_V1, label="Discern-IQ V1 Baseline (51110a1)"),
        Patch(facecolor=C_CLAUDE_RAG, label="Anthropic Claude Sonnet 4.6 (Catalog-RAG)"),
        Patch(facecolor=C_GPT_HEAVY_RAG, label="OpenAI gpt-6.1-sol (nt-heavy, Catalog-RAG)"),
        Patch(facecolor=C_GPT_FAST_RAG, label="OpenAI gpt-5.6-luna (nt-fast, Catalog-RAG)"),
        Patch(facecolor=C_CLAUDE_ZS, label="Claude Sonnet 4.6 / OpenAI GPT (Zero-Shot)"),
    ]
    fig.legend(
        handles=legend_handles, loc="lower center", bbox_to_anchor=(0.5, 0.015),
        ncol=3, facecolor=BG_PANEL, edgecolor=BORDER_COLOR, fontsize=9.0, labelcolor=TEXT_MAIN,
        borderpad=0.65, columnspacing=1.4,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / "discern_iq_vs_gpt_claude_comparison.png"
    pdf_path = output_dir / "discern_iq_vs_gpt_claude_comparison.pdf"
    fig.savefig(png_path, dpi=200, facecolor=BG_CANVAS)
    fig.savefig(pdf_path, facecolor=BG_CANVAS)
    plt.close(fig)

    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png_path, artifact_dir / png_path.name)
        shutil.copy2(pdf_path, artifact_dir / pdf_path.name)

    return png_path, pdf_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "figures")
    parser.add_argument("--artifact-dir", type=Path, default=None)
    args = parser.parse_args()
    png_p, pdf_p = render_comparison_chart(args.output_dir, args.artifact_dir)
    print(f"Rendered PNG: {png_p}")
    print(f"Rendered PDF: {pdf_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
