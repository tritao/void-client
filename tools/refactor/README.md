# Refactor Tooling

This folder contains the data-driven refactor engine used for `client/refactor`.

## Module Layout

- `cli/`
  - Thin command entrypoints used by `make` targets.
  - Parse args and orchestrate calls into lower-level modules.
- `common/`
  - Shared utilities (logging, errors, paths, CSV IO, tree-sitter Java bootstrap).
- `rename/`
  - Symbol rename internals (mapping IO, index/cache, semantic rename engine, edit/scope helpers).
- `extract/`
  - Static extraction internals (manifest schema/loader, static member parsing, callsite-safe text rewrites).
- `cleanup/`
  - Candidate detection and application for post-refactor cleanup.
- `planning/`
  - Plan migration/rewrites and priority tooling.
- `orchestration/`
  - Multi-step pipelines (`refactor-loop`, rebuild flows).

## Core Commands

- `make verify-refactor-tooling`
  - Fast confidence loop for tooling changes.
- `make refactor-loop-dry`
  - Dry-run of the incremental refactor pipeline.
- `make build-refactor-views`
  - Rebuild generated symbol/class/extract views from `.refactor-plan`.
- `make preflight-cleanup-refactor`
  - Validate generated cleanup plan before apply.

## Rules of Thumb

- Add new business logic to package modules (`rename/`, `extract/`, etc.), not `cli/`.
- Keep `cli/` modules small and orchestration-only.
- Prefer shared helpers in `common/` when logic is reused across features.

