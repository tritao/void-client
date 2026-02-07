#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from static_extract_common import (
    ExtractManifest,
    MoveSpec,
    apply_non_string_comment_replacements,
    build_java_parser,
    load_manifests,
    parse_static_members,
)


def _ensure_target_class(target_file: Path, target_class: str, *, dry_run: bool) -> None:
    if target_file.exists():
        return
    if dry_run:
        return
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(
        "\n".join(
            [
                f"final class {target_class} {{",
                f"    private {target_class}() {{",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _insert_members_in_target(target_text: str, members_text: list[str]) -> str:
    idx = target_text.rfind("}")
    if idx < 0:
        raise SystemExit("Target file has no closing brace.")
    insertion = "\n\n" + "\n\n".join(members_text).rstrip() + "\n"
    return target_text[:idx] + insertion + target_text[idx:]


def _parse_imports(java_text: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    for match in re.finditer(r"(?m)^\s*import\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*;\s*$", java_text):
        fqcn = match.group(1)
        simple = fqcn.rsplit(".", 1)[-1]
        imports[simple] = fqcn
    return imports


def _insert_imports(target_text: str, fqcn_lines: list[str]) -> str:
    if not fqcn_lines:
        return target_text
    type_match = re.search(r"(?m)^\s*(?:final\s+)?(?:class|interface|enum)\b", target_text)
    type_start = type_match.start() if type_match else len(target_text)
    header = target_text[:type_start]
    body = target_text[type_start:]
    existing = {
        match.group(1)
        for match in re.finditer(r"(?m)^\s*import\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*;\s*$", header)
    }
    missing = sorted({fqcn for fqcn in fqcn_lines if fqcn not in existing})
    if not missing:
        return target_text
    import_block = "".join(f"import {fqcn};\n" for fqcn in missing)
    import_matches = list(re.finditer(r"(?m)^\s*import\s+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\s*;\s*$", header))
    if import_matches:
        insert_at = import_matches[-1].end() + 1
        header = header[:insert_at] + import_block + header[insert_at:]
        return header + body
    comment_end = header.find("*/")
    if comment_end >= 0:
        insert_at = comment_end + 2
        suffix = "\n\n" if insert_at < len(header) and header[insert_at] == "\n" else "\n"
        header = header[:insert_at] + suffix + import_block + header[insert_at:]
        return header + body
    if header.strip():
        return header.rstrip() + "\n\n" + import_block + "\n" + body.lstrip("\n")
    return import_block + "\n" + body


def _sync_target_imports(source_text: str, target_text: str, member_texts: list[str]) -> str:
    if not member_texts:
        return target_text
    source_imports = _parse_imports(source_text)
    joined = "\n".join(member_texts)
    needed: list[str] = []
    for simple, fqcn in source_imports.items():
        if re.search(rf"\b{re.escape(simple)}\b", joined):
            needed.append(fqcn)
    if re.search(r"\bIOException\b", joined):
        needed.append("java.io.IOException")
    return _insert_imports(target_text, needed)


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    data = text.encode("utf-8")
    for start, end in sorted(spans, reverse=True):
        data = data[:start] + data[end:]
    out = data.decode("utf-8", errors="replace")
    return _normalize_blank_lines(out)


def _normalize_blank_lines(text: str) -> str:
    lines = text.splitlines()
    normalized: list[str] = []
    blank_run = 0
    for line in lines:
        trimmed = line.rstrip()
        if trimmed == "":
            blank_run += 1
            if blank_run <= 1:
                normalized.append("")
            continue
        blank_run = 0
        normalized.append(trimmed)
    out = "\n".join(normalized).strip() + "\n"
    return out


def _format_moved_member(text: str, *, indent: str = "    ") -> str:
    lines = text.rstrip("\n").splitlines()
    if not lines:
        return text
    first_nonblank = next((idx for idx, line in enumerate(lines) if line.strip()), -1)
    if first_nonblank < 0:
        return "\n".join(lines)
    if not lines[first_nonblank].startswith((" ", "\t")):
        lines[first_nonblank] = indent + lines[first_nonblank]
    return "\n".join(lines)


def _member_exists(name: str, kind: str, text: str) -> bool:
    if kind == "method":
        return re.search(rf"\b{name}\s*\(", text) is not None
    return re.search(rf"\b{name}\b", text) is not None


def _replace_callsites(src_dir: Path, source_class: str, target_class: str, moves: list[MoveSpec], *, dry_run: bool) -> int:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for move in moves:
        rx = re.compile(rf"\b{re.escape(source_class)}\s*\.\s*{re.escape(move.name)}\b")
        repl = f"{target_class}.{move.name}"
        patterns.append((rx, repl))

    changed = 0
    for path in sorted(src_dir.rglob("*.java")):
        text = path.read_text(encoding="utf-8", errors="replace")
        new_text = apply_non_string_comment_replacements(text, patterns)
        if new_text == text:
            continue
        changed += 1
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
    return changed


def _replace_callsites_per_member(
    src_dir: Path,
    *,
    source_class: str,
    targets: list[tuple[str, str, str]],
    dry_run: bool,
) -> int:
    """
    Rewrite `SourceClass.member` to per-member target classes.

    targets: list of (from_member_name, target_class, to_member_name)
    """
    patterns: list[tuple[re.Pattern[str], str]] = []
    for from_name, target_class, to_name in targets:
        rx = re.compile(rf"\b{re.escape(source_class)}\s*\.\s*{re.escape(from_name)}\b")
        repl = f"{target_class}.{to_name}"
        patterns.append((rx, repl))
    if not patterns:
        return 0

    changed = 0
    for path in sorted(src_dir.rglob("*.java")):
        text = path.read_text(encoding="utf-8", errors="replace")
        new_text = apply_non_string_comment_replacements(text, patterns)
        if new_text == text:
            continue
        changed += 1
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
    return changed


def _build_move_map(all_manifests: list[ExtractManifest]) -> dict[tuple[str, str, str], tuple[str, Path]]:
    """
    Build a chainable map of moved members:
      (source_class_stem, kind, member) -> (target_class, target_file)
    """
    out: dict[tuple[str, str, str], tuple[str, Path]] = {}
    for m in all_manifests:
        source_stem = m.source.stem
        for mv in m.moves:
            out[(source_stem, mv.kind, mv.name)] = (m.target_class, m.target_file)
    return out


def _chase_final_target(
    move_map: dict[tuple[str, str, str], tuple[str, Path]],
    *,
    source_class: str,
    kind: str,
    name: str,
) -> tuple[str, Path] | None:
    """
    Follow move chains to find the final target class+file for a member.
    Returns None when no chain exists.
    """
    cur = source_class
    seen: set[str] = set()
    last: tuple[str, Path] | None = None
    while True:
        key = (cur, kind, name)
        nxt = move_map.get(key)
        if nxt is None:
            return last
        target_class, target_file = nxt
        last = (target_class, target_file)
        if target_class in seen:
            return last
        seen.add(target_class)
        cur = target_class


def _load_symbol_renames(path: Path) -> dict[tuple[str, str, str], str]:
    """
    Load (owner, kind, old) -> new mapping from generated symbol rename view.
    """
    out: dict[tuple[str, str, str], str] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader((line for line in handle if line.strip() and not line.lstrip().startswith("#")))
        for row in reader:
            kind = (row.get("kind") or "").strip()
            owner = (row.get("owner") or "").strip()
            old = (row.get("old") or "").strip()
            new = (row.get("new") or "").strip()
            if kind not in ("field", "method"):
                continue
            if not owner or not old or not new:
                continue
            out.setdefault((owner, kind, old), new)
    return out


def _candidate_names(
    rename_map: dict[tuple[str, str, str], str],
    *,
    owner: str,
    kind: str,
    old_name: str,
) -> list[str]:
    names: list[str] = [old_name]
    alt = rename_map.get((owner, kind, old_name), "")
    if alt and alt != old_name:
        names.append(alt)
    # Dedupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _qualify_extracted_member_refs(
    member_text: str,
    source_class: str,
    remaining_field_names: list[str],
    remaining_method_names: list[str],
) -> str:
    """
    When we move a static method/field out of `source_class` into some other class,
    any unqualified references *inside the moved text* to other static members that
    stayed behind in `source_class` must be qualified as `source_class.member`.

    We do a conservative, token-ish rewrite that avoids touching strings/comments.
    This isn't a full Java resolver, but it eliminates a common class of "cannot find symbol"
    compile errors after extraction.
    """
    patterns: list[tuple[re.Pattern[str], str]] = []
    for name in sorted(set(remaining_field_names)):
        rx = re.compile(rf"(?<![\w$.]){re.escape(name)}\b")
        patterns.append((rx, f"{source_class}.{name}"))
    for name in sorted(set(remaining_method_names)):
        rx = re.compile(rf"(?<![\w$.]){re.escape(name)}(?=\s*\()")
        patterns.append((rx, f"{source_class}.{name}"))
    if not patterns:
        return member_text
    return apply_non_string_comment_replacements(member_text, patterns)


def _qualify_source_member_refs(
    source_text: str,
    target_class: str,
    field_names: list[str],
    method_names: list[str],
) -> str:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for name in sorted(set(field_names)):
        rx = re.compile(rf"(?<![\w$.]){re.escape(name)}\b")
        repl = f"{target_class}.{name}"
        patterns.append((rx, repl))
    for name in sorted(set(method_names)):
        rx = re.compile(rf"(?<![\w$.]){re.escape(name)}(?=\s*\()")
        repl = f"{target_class}.{name}"
        patterns.append((rx, repl))
    if not patterns:
        return source_text
    return apply_non_string_comment_replacements(source_text, patterns)


def _process_manifest(
    manifest: ExtractManifest,
    *,
    src_dir: Path,
    parser,
    dry_run: bool,
    move_map: dict[tuple[str, str, str], tuple[str, Path]],
    rename_map: dict[tuple[str, str, str], str],
) -> tuple[int, int]:
    source_path = (src_dir / manifest.source).resolve()
    target_path = (src_dir / manifest.target_file).resolve()
    if not source_path.exists():
        raise SystemExit(f"{manifest.path}: source not found: {source_path}")

    _ensure_target_class(target_path, manifest.target_class, dry_run=dry_run)
    source_members = parse_static_members(source_path, parser=parser)
    target_members = parse_static_members(target_path, parser=parser) if target_path.exists() else []
    source_text = source_path.read_text(encoding="utf-8", errors="replace")
    target_text = (
        target_path.read_text(encoding="utf-8", errors="replace")
        if target_path.exists()
        else "\n".join(
            [
                f"final class {manifest.target_class} {{",
                f"    private {manifest.target_class}() {{",
                "    }",
                "}",
                "",
            ]
        )
    )

    by_key = {(m.kind, m.name): m for m in source_members}
    target_keys = {(m.kind, m.name) for m in target_members}
    source_class = source_path.stem
    moved_keys = {(m.kind, m.name) for m in manifest.moves}
    remaining_static_fields = [m.name for m in source_members if m.kind == "field" and (m.kind, m.name) not in moved_keys]
    remaining_static_methods = [m.name for m in source_members if m.kind == "method" and (m.kind, m.name) not in moved_keys]

    to_move: list[MoveSpec] = []
    move_spans: list[tuple[int, int]] = []
    moved_text: list[str] = []
    imported_member_texts: list[str] = []
    moved_field_names: list[str] = []
    moved_method_names: list[str] = []
    callsite_targets: list[tuple[str, str, str]] = []
    for move in manifest.moves:
        candidate_names = _candidate_names(rename_map, owner=source_class, kind=move.kind, old_name=move.name)
        member = None
        actual_source_name = ""
        key = (move.kind, move.name)
        for n in candidate_names:
            m = by_key.get((move.kind, n))
            if m is not None:
                member = m
                actual_source_name = n
                break
        if member is None:
            target_candidate_names = _candidate_names(rename_map, owner=manifest.target_class, kind=move.kind, old_name=move.name)
            existing_target_name = next((n for n in target_candidate_names if (move.kind, n) in target_keys), "")
            if existing_target_name:
                # Idempotent: already in the immediate target (possibly renamed).
                for from_name in candidate_names:
                    callsite_targets.append((from_name, manifest.target_class, existing_target_name))
                if move.kind == "field":
                    moved_field_names.append(existing_target_name)
                if move.kind == "method":
                    moved_method_names.append(existing_target_name)
                continue
            final = _chase_final_target(move_map, source_class=source_class, kind=move.kind, name=move.name)
            if final is not None:
                final_class, final_file = final
                final_path = (src_dir / final_file).resolve()
                if final_path.exists():
                    final_members = parse_static_members(final_path, parser=parser)
                    final_keys = {(m.kind, m.name) for m in final_members}
                    final_candidate_names = _candidate_names(rename_map, owner=final_class, kind=move.kind, old_name=move.name)
                    existing_final_name = next((n for n in final_candidate_names if (move.kind, n) in final_keys), "")
                    if existing_final_name:
                        # Idempotent across multi-hop moves: member ended up in a later target.
                        for from_name in candidate_names:
                            callsite_targets.append((from_name, final_class, existing_final_name))
                        if move.kind == "field":
                            moved_field_names.append(existing_final_name)
                        if move.kind == "method":
                            moved_method_names.append(existing_final_name)
                        continue
            raise SystemExit(f"{manifest.path}: missing source member {move.kind} {move.name}")
        to_move.append(move)
        move_spans.append((member.start, member.end))
        formatted_member = _format_moved_member(member.text)
        formatted_member = _qualify_extracted_member_refs(
            formatted_member,
            source_class,
            remaining_static_fields,
            remaining_static_methods,
        )
        moved_text.append(formatted_member)
        imported_member_texts.append(formatted_member)
        if move.kind == "field":
            moved_field_names.append(actual_source_name or move.name)
        if move.kind == "method":
            moved_method_names.append(actual_source_name or move.name)
        # Rewrite callsites from both old and already-renamed spellings.
        for from_name in candidate_names:
            callsite_targets.append((from_name, manifest.target_class, actual_source_name or move.name))

    new_source = source_text
    target_updated = target_text
    if to_move:
        new_source = _remove_spans(new_source, move_spans)
        target_updated = _insert_members_in_target(target_updated, moved_text)
    new_source = _qualify_source_member_refs(new_source, manifest.target_class, moved_field_names, moved_method_names)
    new_source = _normalize_blank_lines(new_source)
    if not dry_run:
        source_path.write_text(new_source, encoding="utf-8")
    target_updated = _sync_target_imports(source_text, target_updated, imported_member_texts)
    if not dry_run and target_updated != target_text:
        target_path.write_text(target_updated, encoding="utf-8")

    callsite_files = _replace_callsites_per_member(
        src_dir,
        source_class=source_class,
        targets=callsite_targets,
        dry_run=dry_run,
    )
    return len(to_move), callsite_files


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract selected static members into target classes (manifest-driven).")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--manifest-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--rename-csv", type=Path, default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-manifests", type=int, default=10)
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    all_manifests = load_manifests(args.manifest_dir.resolve())
    move_map = _build_move_map(all_manifests)
    manifests = all_manifests
    if not manifests:
        print(f"No extraction manifests found under {args.manifest_dir}")
        return 0
    if args.max_manifests >= 0:
        manifests = manifests[: args.max_manifests]
    parser = build_java_parser(out_so=args.language_so)
    rename_map = _load_symbol_renames(args.rename_csv.resolve())

    moved_total = 0
    touched_total = 0
    for manifest in manifests:
        moved, touched = _process_manifest(
            manifest,
            src_dir=src_dir,
            parser=parser,
            dry_run=args.dry_run,
            move_map=move_map,
            rename_map=rename_map,
        )
        moved_total += moved
        touched_total += touched
        print(f"{manifest.path}: moved {moved} members, updated {touched} callsite files")
    print(f"Done. moved={moved_total}, callsite_files={touched_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
