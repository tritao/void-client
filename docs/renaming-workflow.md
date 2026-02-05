# Renaming workflow (loop)

This repo is a flat/default-package Java codebase (`client/src/*.java`). Renaming is done by:

- Applying a rename mapping (usually `classes.csv`) token-aware (skips comments/strings).
- Compiling after each batch.
- Regenerating reports to pick the next batch.

## One loop iteration

Dry-run the next batch (default cap: 20):

- `make rename-dry`

Apply + compile + regenerate reports:

- `make rename-loop`

If you’re on Linux/macOS and compilation fails due to platform-specific sources, use `EXCLUDE_REGEX`:

- `EXCLUDE_REGEX='Class7\\.java' make rename-loop`

Tune batch size:

- `MAX_RENAMES=5 make rename-loop`
- `MAX_RENAMES=0 make rename-loop` (no cap; potentially huge)

## Reports

- `docs/unnamed-status.md` tracks remaining obfuscated-by-filename files.
- `docs/fan-graph.md` shows heuristic fan-in/out + mutable statics.
- `docs/rename-dossiers.md` gives per-class “shape” dossiers for picking safe renames/splits.
