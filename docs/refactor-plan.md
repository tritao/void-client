# Refactor Plan Workflow

## Goals
- Keep one canonical source for rename/split operations.
- Generate tool-specific views deterministically.
- Run one resumable pipeline end-to-end.

## Canonical Inputs
- Directory: `client/refactor/.refactor-plan/`
- Format: action-focused module CSVs (`*.rename.csv`, `*.extract.csv`, `*.class_rename.csv`, `*.defer.csv`) documented in `client/refactor/.refactor-plan/README.md`.

## Generated Views
Built by `tools/refactor/build_refactor_views.py`:
- `client/refactor/.refactor-plan/generated/symbol_renames.csv`
- `client/refactor/.refactor-plan/symbol-renames/generated/*.csv`
- `client/refactor/.refactor-plan/generated/classes.csv`
- `client/refactor/.refactor-plan/extract-statics/generated/*.yaml`
- `docs/refactor-views-report.md`

Validation gate:
- `build_refactor_views` fails when conflict/collision warnings exist.
- Temporary bypass is available with `--allow-conflicts` while cleaning migrated data.

Readability model:
- `id` is optional (builder does not require explicit IDs).
- `action` comes from filename suffix.
- `module` comes from filename prefix if omitted in rows.

Legacy imports are no longer stored in-repo; migrate once into canonical `*.csv` files.

## Execution Pipeline
`tools/refactor/orchestration/refactor_pipeline.py` runs:
1. `build_views`
2. `class_renames` (optional; skip by default in make target)
3. `symbol_renames`
4. `extract_statics`
5. `check_extract`
6. `compile_refactor`

State and logs:
- Markers: `build/refactor-state/*.json`
- Logs: `build/refactor-state/logs/*.log`
- Consolidated report: `docs/refactor-pipeline-report.md`

## Naming Rules
- Use `LRU` uppercase acronym (not `Lru`).
- Prefer stable semantic names over temporary numbered names.
- For conflicting historical proposals, latest row in canonical plan wins for same `scope + old`.

## Commands
- Import old artifacts into canonical plan:
  - `make migrate-refactor-plan`
- Build views only:
  - `make build-refactor-views`
- Full dry-run:
  - `make refactor-loop-dry`
- Full apply (refactor tree only):
  - `make refactor-loop`
