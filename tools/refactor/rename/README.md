# Rename Engine

This package contains the symbol-rename internals used by
`tools.refactor.cli.ts_rename_identifiers`.

## Files

- `models.py`
  - Shared dataclasses (`Mapping`, `PreparedMapping`, `ClassIndex`, `Report`).
- `io.py`
  - CSV + extract-manifest loading for rename inputs.
- `cache.py`
  - Fingerprint and disk cache for parsed Java indexes.
- `index.py`
  - Java AST indexing helpers (field/method/type indexes).
- `semantic.py`
  - Semantic rename operations (owner/member/overload-aware renames).
- `scope.py`
  - Mapping scope resolution (`file`/class-stem to concrete Java files).
- `edits.py`
  - Byte-edit application and overlap pruning.
- `preflight.py`
  - Prepared-mapping build and conflict validation.

## Design Notes

- `cli/` should orchestrate only.
- New rename logic should be added here first, then called from CLI entrypoints.
- Keep deterministic behavior for conflicts/overlaps to preserve idempotent loop runs.

