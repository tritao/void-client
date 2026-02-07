# Generated extraction manifests (`.refactor-plan/extract-statics/`)

This directory is generated output only.

- Active inputs for extraction are canonical `action=extract` rows in `client/refactor/.refactor-plan/*.csv`.
- Generated manifests are written to `client/refactor/.refactor-plan/extract-statics/generated/*.yaml` by `make build-refactor-views`.
- Extraction tools (`extract-statics`, pipeline extract step) read only from the `generated/` subdirectory.

No legacy extraction-manifest archive is maintained in-repo.
