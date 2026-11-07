#!/usr/bin/env python3
"""Command-line interface for Endexis (`endexis`)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ccl_compiler import CCLCompiler
from .da2_generator import DA2ReportGenerator
from .eval_harness import generate_reasoning_trace, run_full_evaluation_suite, verifier_guided_best_of_n
from .forms_mapper import FormsAndOutputResolver
from .interpreter import MillenniumInterpreter
from .neural_compiler import LocalNeuralCompiler
from .schemas import DEFAULT_OWNER_GROUP_DISPLAY, TenantConfig
from .sql_execution_harness import execute_and_verify_ejs_sql


def _build_tenant_config(args: argparse.Namespace) -> TenantConfig:
    base = TenantConfig.from_file(getattr(args, "tenant_config", None))
    return TenantConfig.resolve_for_request(
        request_text=getattr(args, "request", ""),
        base_config=base,
        owner_group_display=getattr(args, "owner_group", None),
        tenancy=getattr(args, "tenancy", None),
        facility_display=getattr(args, "facility", None),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="endexis",
        description="Endexis — Tenant-Agnostic Clinical & Financial Analytics, DA2, V500, CCL & BIRT 4.4.2 Compiler",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_int = sub.add_parser("interpret", help="Interpret a request into V500 tables/columns, types, Code Sets, and DA2 fields")
    p_int.add_argument("request", help="Natural language clinical/financial analytics request")
    p_int.add_argument("--tenancy", default=None, help="Optional tenancy override: continuum, communityworks, standalone_rho")

    p_frm = sub.add_parser("forms", help="Resolve which V500 tables/columns and DA2 fields hold specific forms or output data")
    p_frm.add_argument("request", help="Description of the clinical form, assessment, note, flowsheet, or output")

    p_ccl = sub.add_parser("compile-ccl", help="Compile a request into a validated CCL (.prg) program")
    p_ccl.add_argument("request", help="Natural language request")
    p_ccl.add_argument("--name", default=None, help="Optional CCL program name (<=28 chars)")
    p_ccl.add_argument("--tenancy", default=None, help="Optional tenancy override")
    p_ccl.add_argument("--out", default=None, help="Optional file path to write .prg source")

    p_qry = sub.add_parser("generate-query", help="Generate standalone DA2 <cclquery version=\"1.0\"> XML and EJS v3.17.3 Oracle V500 SQL")
    p_qry.add_argument("request", help="Natural language request")
    p_qry.add_argument("--name", default=None, help="Optional query name")
    p_qry.add_argument("--tenancy", default=None, help="Optional tenancy override")
    p_qry.add_argument("--owner-group", default=None, help=f"Optional DA2 owner_group_display override (default: {DEFAULT_OWNER_GROUP_DISPLAY})")
    p_qry.add_argument("--facility", default=None, help="Optional tenant facility display override")
    p_qry.add_argument("--tenant-config", default=None, help="Optional path to tenant JSON config file")
    p_qry.add_argument("--out-xml", default=None, help="Optional file path to write <cclquery> XML")
    p_qry.add_argument("--out-sql", default=None, help="Optional file path to write EJS v3.17.3 V500 SQL")

    p_da2 = sub.add_parser("generate-da2", help="Generate a direct-upload BIRT 4.4.2 .rptdesign report + DA2 query spec")
    p_da2.add_argument("request", help="Natural language request")
    p_da2.add_argument("--name", default=None, help="Optional report name")
    p_da2.add_argument("--tenancy", default=None, help="Optional tenancy override")
    p_da2.add_argument("--owner-group", default=None, help=f"Optional DA2 owner_group_display override (default: {DEFAULT_OWNER_GROUP_DISPLAY})")
    p_da2.add_argument("--facility", default=None, help="Optional tenant facility display override")
    p_da2.add_argument("--tenant-config", default=None, help="Optional path to tenant JSON config file")
    p_da2.add_argument("--out-dir", default=None, help="Directory to write .rptdesign, .prg, .md card, and .json spec")
    p_da2.add_argument("--best-of-n", type=int, default=1, help="Run Verifier-Guided Best-of-N candidate selection (e.g., 4)")
    p_da2.add_argument("--reasoning", action="store_true", help="Include 6-step Chain-of-Thought <reasoning> trace in output")

    p_neu = sub.add_parser("compile-neural", help="Compile a request via the local Endexis neural endpoint with deterministic verifier fallback")
    p_neu.add_argument("request", help="Natural language clinical/financial analytics request")
    p_neu.add_argument("--tenancy", default=None, help="Optional tenancy override")
    p_neu.add_argument("--best-of-n", type=int, default=4, help="Number of Best-of-N candidates (default: 4)")

    p_eval = sub.add_parser("evaluate", help="Run the deterministic verification and zero-leakage holdout evaluation suite")
    p_eval.add_argument("--data-dir", default=None, help="Optional corpus directory override")

    p_init = sub.add_parser("init", help="Write a zero-PHI declarative .endexis.toml tenant profile")
    p_init.add_argument("--out", default=".endexis.toml", help="Output path (.endexis.toml or tenant.json)")
    p_init.add_argument("--owner-group", default=DEFAULT_OWNER_GROUP_DISPLAY, help="Tenant DA2 owner_group_display")
    p_init.add_argument("--tenancy", default="communityworks", help="Tenancy mode (communityworks, continuum, standalone_rho)")
    p_init.add_argument("--utc-timezone-index", type=int, default=54, help="Millennium UTC timezone index (default: 54)")
    p_init.add_argument("--facility", default=None, help="Optional primary facility display name")
    p_init.add_argument("--facility-code-value", default=None, help="Optional primary facility CODE_VALUE")

    p_disc = sub.add_parser("init-discover", help="Build .endexis.toml from an exported Millennium Code Set 220 CSV/JSON file")
    p_disc.add_argument("export_file", help="Path to exported Code Set 220 CSV or JSON file")
    p_disc.add_argument("--out", default=".endexis.toml", help="Output path (.endexis.toml or tenant.json)")
    p_disc.add_argument("--owner-group", default=DEFAULT_OWNER_GROUP_DISPLAY, help="Tenant DA2 owner_group_display")
    p_disc.add_argument("--tenancy", default="communityworks", help="Tenancy mode")
    p_disc.add_argument("--utc-timezone-index", type=int, default=54, help="Millennium UTC timezone index (default: 54)")

    p_vsql = sub.add_parser("verify-sql", help="Compile a request to EJS V500 SQL and execute it in the synthetic V500 SQLite harness")
    p_vsql.add_argument("request", help="Natural language clinical/financial analytics request")
    p_vsql.add_argument("--tenant-config", default=None, help="Optional path to .endexis.toml or tenant.json")

    args = parser.parse_args(argv)

    if args.cmd == "interpret":
        interp = MillenniumInterpreter().interpret(args.request, tenancy=args.tenancy)
        print(json.dumps(interp.to_dict(), indent=2))
        return 0

    if args.cmd == "forms":
        res = FormsAndOutputResolver().resolve(args.request, top_k=3)
        print(json.dumps([r.to_dict() for r in res], indent=2))
        return 0

    if args.cmd == "compile-ccl":
        prog = CCLCompiler().compile(args.request, program_name=args.name, tenancy=args.tenancy)
        if args.out:
            Path(args.out).write_text(prog.source_code, encoding="utf-8")
        print(prog.source_code)
        return 0 if prog.verification.is_valid else 1

    if args.cmd == "generate-query":
        tcfg = _build_tenant_config(args)
        bundle = DA2ReportGenerator(tenant_config=tcfg).generate(
            args.request,
            report_name=args.name,
            tenancy=tcfg.tenancy,
            owner_group_display=tcfg.owner_group_display,
            facility_display=tcfg.facility_display,
            tenant_config=tcfg,
        )
        primary_q = bundle.queries[0]
        if args.out_xml:
            Path(args.out_xml).write_text(primary_q.cclquery_xml, encoding="utf-8")
        if args.out_sql:
            Path(args.out_sql).write_text(primary_q.ejs_v500_sql, encoding="utf-8")
        print(primary_q.cclquery_xml)
        print(primary_q.ejs_v500_sql)
        return 0 if bundle.verification.is_valid else 1

    if args.cmd == "generate-da2":
        tcfg = _build_tenant_config(args)
        if getattr(args, "best_of_n", 1) > 1:
            bon = verifier_guided_best_of_n(args.request, n=args.best_of_n, tenancy=tcfg.tenancy, report_name=args.name)
            bundle = bon["bundle"]
            r_trace = bon["reasoning_trace"]
        else:
            bundle = DA2ReportGenerator(tenant_config=tcfg).generate(
                args.request,
                report_name=args.name,
                tenancy=tcfg.tenancy,
                owner_group_display=tcfg.owner_group_display,
                facility_display=tcfg.facility_display,
                tenant_config=tcfg,
            )
            r_trace = generate_reasoning_trace(args.request, tenancy=tcfg.tenancy) if getattr(args, "reasoning", False) else None
        if args.out_dir:
            out_d = Path(args.out_dir)
            out_d.mkdir(parents=True, exist_ok=True)
            (out_d / f"{bundle.report_name}.rptdesign").write_text(bundle.rptdesign_xml, encoding="utf-8")
            (out_d / f"{bundle.report_name}_build_card.md").write_text(bundle.build_card_markdown, encoding="utf-8")
            (out_d / f"{bundle.report_name}.prg").write_text(bundle.equivalent_ccl, encoding="utf-8")
            for q in bundle.queries:
                if q.cclquery_xml:
                    (out_d / f"{q.query_name}_cclquery.xml").write_text(q.cclquery_xml, encoding="utf-8")
                if q.daquery_xml:
                    (out_d / f"{q.query_name}.daquery").write_text(q.daquery_xml, encoding="utf-8")
                if q.ejs_v500_sql:
                    (out_d / f"{q.query_name}_ejs_v500.sql").write_text(q.ejs_v500_sql, encoding="utf-8")
            (out_d / f"{bundle.report_name}.dareport").write_bytes(
                DA2ReportGenerator.build_dareport_zip_bytes(bundle, owner_group_display=bundle.owner_group_display)
            )
            (out_d / f"{bundle.report_name}_bundle.json").write_text(json.dumps(bundle.to_dict(), indent=2), encoding="utf-8")
            print(f"Wrote uploadable DA2 bundle to {out_d}")
        else:
            out_dict = bundle.to_dict()
            if r_trace:
                out_dict["reasoning_trace"] = r_trace
            print(json.dumps(out_dict, indent=2))
        return 0 if bundle.verification.is_valid else 1

    if args.cmd == "compile-neural":
        nc = LocalNeuralCompiler()
        res = nc.compile_with_verifier_fallback(args.request, n=args.best_of_n, tenancy=args.tenancy)
        out = {
            "model": res["model"],
            "neural_endpoint_active": res["neural_endpoint_active"],
            "transport": res["transport"],
            "n_candidates_evaluated": res["n_candidates_evaluated"],
            "all_candidates_verified_count": res["all_candidates_verified_count"],
            "selected_verifier_score": res["selected_verifier_score"],
            "neural_reasoning_trace": res["neural_reasoning_trace"],
            "verified_reasoning_trace": res["verified_reasoning_trace"],
            "latency_ms": res["latency_ms"],
            "vram_allocated_mb": res["vram_allocated_mb"],
            "verification_valid": res["bundle"].verification.is_valid and res["ccl_program"].verification.is_valid,
            "report_name": res["bundle"].report_name,
            "execution_strategy": res["bundle"].execution_strategy,
        }
        print(json.dumps(out, indent=2))
        return 0 if out["verification_valid"] else 1

    if args.cmd == "evaluate":
        rep = run_full_evaluation_suite(Path(args.data_dir) if args.data_dir else None)
        print(json.dumps(rep, indent=2))
        return 0 if rep["zero_leakage_verification"]["zero_leakage_passed"] else 1

    if args.cmd == "init":
        cfg = TenantConfig(
            owner_group_display=args.owner_group,
            tenancy=args.tenancy,
            utc_timezone_index=args.utc_timezone_index,
            facility_display=args.facility,
            facility_code_value=args.facility_code_value,
            facility_overrides={args.facility: str(args.facility_code_value)} if (args.facility and args.facility_code_value) else {},
        )
        out_p = Path(args.out)
        if out_p.suffix.lower() == ".json":
            out_p.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")
        else:
            out_p.write_text(cfg.to_toml(), encoding="utf-8")
        print(f"Wrote tenant configuration to {out_p}")
        return 0

    if args.cmd == "init-discover":
        cfg = TenantConfig.discover_from_export_file(
            args.export_file,
            owner_group_display=args.owner_group,
            tenancy=args.tenancy,
            utc_timezone_index=args.utc_timezone_index,
        )
        out_p = Path(args.out)
        if out_p.suffix.lower() == ".json":
            out_p.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")
        else:
            out_p.write_text(cfg.to_toml(), encoding="utf-8")
        print(f"Discovered Code Set 220 locations and wrote tenant profile to {out_p}")
        return 0

    if args.cmd == "verify-sql":
        tcfg = _build_tenant_config(args)
        bundle = DA2ReportGenerator(tenant_config=tcfg).generate(
            args.request,
            tenancy=tcfg.tenancy,
            owner_group_display=tcfg.owner_group_display,
            facility_display=tcfg.facility_display,
            tenant_config=tcfg,
        )
        exec_rep = execute_and_verify_ejs_sql(bundle.queries[0].ejs_v500_sql)
        print(json.dumps(exec_rep, indent=2))
        return 0 if (exec_rep["executed"] and exec_rep["non_empty"] and exec_rep["fanout_safe"]) else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
