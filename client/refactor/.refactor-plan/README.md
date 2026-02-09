# Refactor plan source of truth

This directory is the canonical input for refactor operations.

Canonical files are action-focused and human-readable:

- `*.rename.csv`
- `*.scoped_fallback.csv`
- `*.extract.csv`
- `*.class_rename.csv`
- `*.defer.csv`

`action` is inferred from filename suffix (e.g. `collections.rename.csv`).
`module` is inferred from filename prefix when omitted.
`id` is optional and not required for authoring.

Enums:
- `action`: `rename` | `extract` | `class_rename` | `defer` | `scoped_fallback`
- `kind`: `type` | `method` | `field` | `param` | `local` | `class`
- `status`: `proposed` | `approved` | `applied` | `blocked`
- `phase`: `core` | `module` | `cleanup` | `split`
- `confidence`: `low` | `medium` | `high`

Per-action columns (`module` inferred from filename prefix):

- `rename`: `kind,file,owner,member,signature,param_index,old,new,scope,phase,confidence,status,notes`
- `scoped_fallback`: `kind,file,owner,member,signature,param_index,old,new,scope,phase,confidence,status,notes`
- `extract`: `file,kind,old,target_class,target_file,scope,phase,confidence,status,notes`
- `class_rename`: `old,new,phase,confidence,status,notes`
- `defer`: `kind,file,owner,member,signature,param_index,old,new,target_class,target_file,scope,phase,confidence,status,notes`

Cleanup transition files still supported:
- `call_rewrites.csv` (targeted no-arg callsite rewrites)
- `call_arg_rewrites.csv` (targeted argument-drop callsite rewrites)

Generated views are created by:

```bash
make build-refactor-views
```

Cleanup detection reports can be regenerated with:

```bash
make cleanup-candidates
```

Promote accepted fallback rows into canonical module rename CSVs:

```bash
make promote-scoped-fallback
```

This writes:
- `client/refactor/.refactor-plan/generated/cleanup_candidates.csv`
- `docs/cleanup-candidates.md`

Do not hand-edit generated files under:
- `client/refactor/.refactor-plan/symbol-renames/generated/`
- `client/refactor/.refactor-plan/extract-statics/generated/`
- `client/refactor/.refactor-plan/generated/symbol_renames.csv`
- `client/refactor/.refactor-plan/generated/classes.csv`

No in-repo legacy archive is required; canonical `*.csv` files are the only maintained source of truth.
