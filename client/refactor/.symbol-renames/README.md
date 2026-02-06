# Symbol rename shards (`.symbol-renames/`)

Use this folder to split `client/refactor/symbol_renames.csv` into smaller, reviewable batches.

## Recommended structure

- Put one or more `*.csv` files under subfolders that mirror the *refactor layout* domains:
  - `client/refactor/.symbol-renames/client/`
  - `client/refactor/.symbol-renames/ui/`
  - `client/refactor/.symbol-renames/graphics/`
  - `client/refactor/.symbol-renames/world/`
  - `client/refactor/.symbol-renames/cache/`
  - ...
- Use numeric prefixes to make apply order deterministic:
  - `010-widget.csv`
  - `020-scripts.csv`
  - `030-js5.csv`

## How it’s loaded

`tools/apply_symbol_renames.py` loads mappings from:

1. Every `--csv` file (defaults to `client/refactor/symbol_renames.csv`), then
2. Every `*.csv` under `--csv-dir` (defaults to `client/refactor/.symbol-renames/`, recursive; lexical order)

Exact duplicate rows are ignored.

## Authoring notes

- Prefer signature-based keys for params (`owner/member/signature/param_index`) to avoid brittle `line/col`.
- Locals still require `line/col`.
