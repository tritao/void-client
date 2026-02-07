#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def _process_manifest(manifest: ExtractManifest, *, src_dir: Path, parser, dry_run: bool) -> tuple[int, int]:
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
    for move in manifest.moves:
        key = (move.kind, move.name)
        member = by_key.get(key)
        if member is None:
            if key in target_keys:
                if move.kind == "field":
                    moved_field_names.append(move.name)
                if move.kind == "method":
                    moved_method_names.append(move.name)
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
            moved_field_names.append(move.name)
        if move.kind == "method":
            moved_method_names.append(move.name)

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

    callsite_files = _replace_callsites(src_dir, source_class, manifest.target_class, list(manifest.moves), dry_run=dry_run)
    return len(to_move), callsite_files


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract selected static members into target classes (manifest-driven).")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--manifest-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-manifests", type=int, default=10)
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    manifests = load_manifests(args.manifest_dir.resolve())
    if not manifests:
        print(f"No extraction manifests found under {args.manifest_dir}")
        return 0
    if args.max_manifests >= 0:
        manifests = manifests[: args.max_manifests]
    parser = build_java_parser(out_so=args.language_so)

    moved_total = 0
    touched_total = 0
    for manifest in manifests:
        moved, touched = _process_manifest(manifest, src_dir=src_dir, parser=parser, dry_run=args.dry_run)
        moved_total += moved
        touched_total += touched
        print(f"{manifest.path}: moved {moved} members, updated {touched} callsite files")
    print(f"Done. moved={moved_total}, callsite_files={touched_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
