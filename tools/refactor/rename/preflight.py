from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from tools.refactor.rename.models import Mapping, PreparedMapping
from tools.refactor.rename.scope import build_stem_index, resolve_scope_files_with_index


JAVA_IDENT_RX = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def build_prepared_mappings(
    *,
    mappings: list[Mapping],
    src_dir: Path,
    java_files: list[Path],
    field_decls: dict[str, set[Path]],
    allow_non_obfuscated: bool,
    safe_preflight: bool,
    is_obfuscated_name: Callable[[str, str], bool],
    parse_signature_types: Callable[[str], tuple[str, ...] | None],
) -> tuple[list[PreparedMapping], list[str]]:
    prepared: list[PreparedMapping] = []
    skipped: list[str] = []
    stem_index = build_stem_index(java_files)
    scope_files_cache: dict[str, set[Path]] = {}
    signature_cache: dict[str, tuple[str, ...] | None] = {}
    for mapping in mappings:
        if not JAVA_IDENT_RX.match(mapping.old) or not JAVA_IDENT_RX.match(mapping.new):
            skipped.append(f"Invalid java identifier: {mapping.old!r} -> {mapping.new!r}")
            continue
        if not allow_non_obfuscated and not is_obfuscated_name(mapping.old, mapping.kind):
            skipped.append(f"Not obfuscated (use --allow-non-obfuscated): {mapping.old} -> {mapping.new}")
            continue
        scope_key = (mapping.file or "").strip()
        cached_scope = scope_files_cache.get(scope_key)
        if cached_scope is None:
            cached_scope = resolve_scope_files_with_index(
                mapping_file=mapping.file,
                src_dir=src_dir,
                stem_index=stem_index,
            )
            scope_files_cache[scope_key] = cached_scope
        scope_files = cached_scope
        signature_types = (
            signature_cache.setdefault(mapping.signature, parse_signature_types(mapping.signature))
            if (mapping.kind or "").strip().lower() == "method"
            else None
        )
        if safe_preflight and mapping.kind.lower() == "field":
            old_decl_files = field_decls.get(mapping.old, set())
            new_decl_files = field_decls.get(mapping.new, set())
            owner_stem = (mapping.owner or "").strip()

            if scope_files:
                old_decl_files = {path for path in old_decl_files if path in scope_files}
                new_decl_files = {path for path in new_decl_files if path in scope_files}
            if owner_stem:
                old_decl_files_owner = {path for path in old_decl_files if path.stem == owner_stem}
                new_decl_files_owner = {path for path in new_decl_files if path.stem == owner_stem}
            else:
                old_decl_files_owner = set()
                new_decl_files_owner = set()

            if old_decl_files:
                if len(old_decl_files) > 1 and not (owner_stem and len(old_decl_files_owner) == 1):
                    skipped.append(
                        f"Ambiguous field declaration ({len(old_decl_files)} files): {mapping.old} -> {mapping.new}"
                    )
                    continue
            elif new_decl_files:
                if len(new_decl_files) > 1 and not (owner_stem and len(new_decl_files_owner) == 1):
                    skipped.append(
                        f"Ambiguous renamed field declaration ({len(new_decl_files)} files): {mapping.old} -> {mapping.new}"
                    )
                    continue
            else:
                skipped.append(f"No field declaration found (old/new): {mapping.old} -> {mapping.new}")
                continue
        prepared.append(
            PreparedMapping(
                old=mapping.old,
                new=mapping.new,
                kind=mapping.kind,
                owner=mapping.owner,
                member=mapping.member,
                file=mapping.file,
                signature=mapping.signature,
                signature_types=signature_types,
                notes=mapping.notes,
                scope_files=frozenset(scope_files),
            )
        )
    return prepared, skipped


def validate_prepared_conflicts(prepared: list[PreparedMapping]) -> str | None:
    def scopes_overlap(left: PreparedMapping, right: PreparedMapping) -> bool:
        if not left.scope_files or not right.scope_files:
            return True
        return bool(left.scope_files.intersection(right.scope_files))

    by_key: dict[tuple[str, str, tuple[str, ...] | None, str], list[PreparedMapping]] = {}
    for mapping in prepared:
        kind = (mapping.kind or "").strip().lower()
        signature = mapping.signature_types if kind == "method" else None
        member_scope = (mapping.member or "").strip() if kind in ("local", "param") else ""
        by_key.setdefault((kind, mapping.old, signature, member_scope), []).append(mapping)

    for (kind, old, signature, member_scope), entries in by_key.items():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                left = entries[i]
                right = entries[j]
                if left.new == right.new:
                    continue
                if scopes_overlap(left, right):
                    where = f"::{member_scope}" if member_scope else ""
                    return (
                        f"Conflicting mappings for {kind}:{old}{where}"
                        f"{' ' + str(signature) if signature is not None else ''}: {left.new} vs {right.new} "
                        f"(scope overlap: {left.file or '<global>'} / {right.file or '<global>'})"
                    )
    return None
