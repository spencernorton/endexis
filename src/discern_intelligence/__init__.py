"""
Endexis — Tenant-Agnostic Clinical & Financial Analytics Engine.

Provides four integrated capabilities:
1. Millennium V500 Data Model Interpreter (columns, data types, Code Sets, DA2 elements, tenancy rules)
2. Clinical Forms, Assessments, Notes, Flowsheets, I&O, and Output Resolver
3. Request-to-CCL Compiler & Static Verifier (.prg scripts with PLAN/JOIN and UAR lookups)
4. Direct-Upload Discern Analytics 2.0 (DA2) Query & BIRT 4.4.2 .rptdesign Report Generator
"""

from .sql_execution_harness import create_synthetic_v500_connection, execute_and_verify_ejs_sql, run_adversarial_sql_edge_case_suite
from .schemas import (
    DEFAULT_OWNER_GROUP_DISPLAY,
    ColumnMatch,
    FormOutputResolution,
    InterpretationResult,
    CompiledCCLProgram,
    DA2QuerySpec,
    DA2UploadableBundle,
    TenantConfig,
    VerificationReport,
)
from .interpreter import MillenniumInterpreter
from .forms_mapper import FormsAndOutputResolver
from .ccl_compiler import CCLCompiler
from .da2_generator import DA2ReportGenerator
from .neural_compiler import ApolloNeuralCompiler, LocalNeuralCompiler
from .eval_harness import (
    evaluate_adversarial_v2_benchmark,
    evaluate_filter_value_routing,
    generate_reasoning_trace,
    run_full_evaluation_suite,
    score_query_element_and_filter_f1,
    verifier_guided_best_of_n,
    verify_cclquery_xml,
    verify_ejs_v500_sql,
)
from .knowledge import (
    load_da2_view_alias_catalog,
    load_event_set_and_crosswalks,
    lookup_language_crosswalk,
    load_da2_domain_taxonomy,
    load_da2_filter_value_catalog,
    load_da2_canonical_reports_catalog,
    load_healtheintent_edw_catalog,
    resolve_da2_filter_value,
    resolve_da2_view_or_alias,
)

__version__ = "2.2.2"
__all__ = [
    "DEFAULT_OWNER_GROUP_DISPLAY",
    "TenantConfig",
    "create_synthetic_v500_connection",
    "execute_and_verify_ejs_sql",
    "ColumnMatch",
    "FormOutputResolution",
    "InterpretationResult",
    "CompiledCCLProgram",
    "DA2QuerySpec",
    "DA2UploadableBundle",
    "VerificationReport",
    "MillenniumInterpreter",
    "FormsAndOutputResolver",
    "CCLCompiler",
    "DA2ReportGenerator",
    "LocalNeuralCompiler",
    "ApolloNeuralCompiler",
    "verify_cclquery_xml",
    "verify_ejs_v500_sql",
    "score_query_element_and_filter_f1",
    "evaluate_filter_value_routing",
    "run_full_evaluation_suite",
    "generate_reasoning_trace",
    "verifier_guided_best_of_n",
    "load_da2_view_alias_catalog",
    "load_event_set_and_crosswalks",
    "lookup_language_crosswalk",
    "load_da2_domain_taxonomy",
    "load_da2_filter_value_catalog",
    "load_da2_canonical_reports_catalog",
    "load_healtheintent_edw_catalog",
    "resolve_da2_filter_value",
    "resolve_da2_view_or_alias",
]
