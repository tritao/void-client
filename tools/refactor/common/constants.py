from __future__ import annotations

from pathlib import Path


CLIENT_SRC_DIR = Path("client/src")
REFACTOR_SRC_DIR = Path("client/refactor")

REFACTOR_PLAN_DIR = Path("client/refactor/.refactor-plan")
REFACTOR_PLAN_GENERATED_DIR = REFACTOR_PLAN_DIR / "generated"
REFACTOR_SYMBOL_DIR = REFACTOR_PLAN_DIR / "symbol-renames" / "generated"
REFACTOR_EXTRACT_MANIFEST_DIR = REFACTOR_PLAN_DIR / "extract-statics" / "generated"
REFACTOR_CLEANUP_PLAN_DIR = REFACTOR_PLAN_GENERATED_DIR / "cleanup-plan"
REFACTOR_CLEANUP_CANDIDATES_CSV = REFACTOR_PLAN_GENERATED_DIR / "cleanup_candidates.csv"
REFACTOR_DROP_MEMBERS_CSV = REFACTOR_PLAN_DIR / "drop_members.csv"

REFACTOR_CLASSES_CSV = REFACTOR_PLAN_GENERATED_DIR / "classes.csv"
REFACTOR_SYMBOLS_CSV = REFACTOR_PLAN_GENERATED_DIR / "symbol_renames.csv"
REFACTOR_CLEANUP_SUMMARY_JSON = Path("build/refactor-state/cleanup-owner-remap.json")

REFACTOR_STATE_DIR = Path("build/refactor-state")
REFACTOR_VIEWS_SUMMARY_JSON = REFACTOR_STATE_DIR / "views-summary.json"

REFACTOR_VIEWS_REPORT_MD = Path("docs/refactor-views-report.md")
REFACTOR_PIPELINE_REPORT_MD = Path("docs/refactor-pipeline-report.md")
REFACTOR_CLEANUP_CANDIDATES_MD = Path("docs/cleanup-candidates.md")

DEFAULT_MAX_RENAMES = 20
DEFAULT_MAX_MANIFESTS = 10
