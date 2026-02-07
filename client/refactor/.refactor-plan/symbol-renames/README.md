# Generated symbol views (`.refactor-plan/symbol-renames/`)

This directory is generated output only.

- Active inputs for symbol renames are the canonical module CSVs in `client/refactor/.refactor-plan/*.csv`.
- Generated files are written to `client/refactor/.refactor-plan/symbol-renames/generated/*.csv` by `make build-refactor-views`.
- Execution tools (`rename-refactor`, pipeline symbol step) read only from the `generated/` subdirectory.

No legacy symbol-shard archive is maintained in-repo.
