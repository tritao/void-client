#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import re
import subprocess
from pathlib import Path

from tools.refactor.common.ts_java import build_java_parser
from tools.refactor.extract.manifest import ExtractManifest, MoveSpec, load_manifests
from tools.refactor.extract.static_members import parse_static_members
from tools.refactor.extract.text_rewrite import apply_non_string_comment_replacements

_CALLSITE_WORKER_PATTERNS: list[tuple[re.Pattern[str], str]] = []
_CALLSITE_WORKER_DRY_RUN = False
_RUST_CALLSITE_MANIFEST = Path("tools/refactor/rust/callsite_rewriter/Cargo.toml")
_RUST_CALLSITE_TARGET_DIR = Path("build/refactor-cache/rust-target")
_RUST_CALLSITE_BINARY = _RUST_CALLSITE_TARGET_DIR / "release" / "callsite_rewriter"


def _init_callsite_worker(pattern_specs: list[tuple[str, str, str, str]], dry_run: bool) -> None:
    global _CALLSITE_WORKER_PATTERNS, _CALLSITE_WORKER_DRY_RUN
    _CALLSITE_WORKER_DRY_RUN = dry_run
    compiled: list[tuple[re.Pattern[str], str]] = []
    for source_class, from_name, target_class, to_name in pattern_specs:
        rx = re.compile(rf"\b{re.escape(source_class)}\s*\.\s*{re.escape(from_name)}\b")
        repl = f"{target_class}.{to_name}"
        compiled.append((rx, repl))
    _CALLSITE_WORKER_PATTERNS = compiled


def _rewrite_callsite_file_worker(path_str: str) -> str | None:
    path = Path(path_str)
    text = path.read_text(encoding="utf-8", errors="replace")
    new_text = apply_non_string_comment_replacements(text, _CALLSITE_WORKER_PATTERNS)
    if new_text == text:
        return None
    if not _CALLSITE_WORKER_DRY_RUN:
        path.write_text(new_text, encoding="utf-8")
    return str(path.resolve())


def _resolve_rust_callsite_binary(*, rust_mode: str) -> Path | None:
    override = os.environ.get("RUST_CALLSITE_REWRITER_BIN", "").strip()
    if override:
        candidate = Path(override).resolve()
        if candidate.exists():
            return candidate
    candidate = _RUST_CALLSITE_BINARY.resolve()
    if candidate.exists():
        return candidate
    if rust_mode != "build":
        return None
    try:
        subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "--manifest-path",
                str(_RUST_CALLSITE_MANIFEST),
                "--target-dir",
                str(_RUST_CALLSITE_TARGET_DIR),
            ],
            check=True,
        )
    except Exception:
        return None
    candidate = _RUST_CALLSITE_BINARY.resolve()
    if candidate.exists():
        return candidate
    return None


def _replace_callsites_batch_rust(
    src_dir: Path,
    *,
    pattern_specs: list[tuple[str, str, str, str]],
    dry_run: bool,
    jobs: int,
    rust_mode: str,
) -> set[Path] | None:
    if rust_mode == "off":
        return None
    binary = _resolve_rust_callsite_binary(rust_mode=rust_mode)
    if binary is None:
        return None
    cache_dir = Path("build/refactor-cache/extract_statics").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    patterns_file = cache_dir / "callsite-patterns.tsv"
    changed_file = cache_dir / "callsite-changed.txt"
    lines = [f"{src}\t{old}\t{dst}\t{new}" for src, old, dst, new in pattern_specs]
    patterns_file.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    cmd = [
        str(binary),
        "--src-dir",
        str(src_dir),
        "--patterns-file",
        str(patterns_file),
        "--jobs",
        str(max(1, jobs)),
        "--changed-out",
        str(changed_file),
    ]
    if dry_run:
        cmd.append("--dry-run")
    try:
        subprocess.run(cmd, check=True)
    except Exception:
        return None
    changed_paths: set[Path] = set()
    if changed_file.exists():
        for line in changed_file.read_text(encoding="utf-8", errors="replace").splitlines():
            value = line.strip()
            if value:
                changed_paths.add(Path(value))
    return changed_paths


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


def _normalize_static_init_line_for_target(line: str, target_class: str) -> str:
    stripped = line.strip()
    prefix = f"{target_class}."
    if stripped.startswith(prefix):
        return stripped[len(prefix) :].strip()
    return stripped


def _remove_static_init_line(text: str, line: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(line.strip())}[ \t]*\n?")
    out, count = pattern.subn("", text, count=1)
    if count <= 0:
        return text, False
    return _normalize_blank_lines(out), True


def _insert_static_init_line(target_text: str, line: str) -> tuple[str, bool]:
    normalized = line.strip()
    if not normalized:
        return target_text, False
    if re.search(rf"(?m)^[ \t]*{re.escape(normalized)}[ \t]*$", target_text):
        return target_text, False
    m = re.search(r"\bstatic\s*\{", target_text)
    if m:
        open_brace = target_text.find("{", m.start())
        depth = 0
        close_brace = -1
        for i in range(open_brace, len(target_text)):
            ch = target_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    close_brace = i
                    break
        if close_brace < 0:
            return target_text, False
        insertion = f"\n        {normalized}\n    "
        return target_text[:close_brace] + insertion + target_text[close_brace:], True
    idx = target_text.rfind("}")
    if idx < 0:
        raise SystemExit("Target file has no closing brace.")
    block = f"\n\n    static {{\n        {normalized}\n    }}\n"
    return target_text[:idx] + block + target_text[idx:], True


def _line_present(text: str, line: str) -> bool:
    normalized = line.strip()
    if not normalized:
        return False
    return re.search(rf"(?m)^[ \t]*{re.escape(normalized)}[ \t]*$", text) is not None


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


def _remove_empty_static_blocks(text: str) -> str:
    out = re.sub(r"\n[ \t]*static\s*\{\s*\}\n", "\n", text)
    return _normalize_blank_lines(out)


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


def _replace_callsites_batch(
    src_dir: Path,
    *,
    targets: list[tuple[str, str, str, str]],
    dry_run: bool,
    jobs: int,
    rust_callsites: str,
) -> set[Path]:
    """
    Rewrite callsites in a single pass across source files.

    targets: list of (source_class, from_member_name, target_class, to_member_name)
    """
    if not targets:
        return set()

    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    for source_class, from_name, target_class, to_name in targets:
        key = (source_class, from_name)
        mapping[key] = (target_class, to_name)

    pattern_specs: list[tuple[str, str, str, str]] = []
    for (source_class, from_name), (target_class, to_name) in sorted(
        mapping.items(),
        key=lambda item: (item[0][0], -len(item[0][1]), item[0][1]),
    ):
        pattern_specs.append((source_class, from_name, target_class, to_name))

    rust_changed = _replace_callsites_batch_rust(
        src_dir,
        pattern_specs=pattern_specs,
        dry_run=dry_run,
        jobs=jobs,
        rust_mode=rust_callsites,
    )
    if rust_changed is not None:
        return rust_changed

    java_files = [p.resolve() for p in sorted(src_dir.rglob("*.java"))]
    changed_paths: set[Path] = set()
    worker_jobs = max(1, jobs)
    if worker_jobs <= 1:
        _init_callsite_worker(pattern_specs, dry_run)
        for path in java_files:
            out = _rewrite_callsite_file_worker(str(path))
            if out:
                changed_paths.add(Path(out))
    else:
        try:
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=worker_jobs,
                initializer=_init_callsite_worker,
                initargs=(pattern_specs, dry_run),
            ) as pool:
                for out in pool.map(_rewrite_callsite_file_worker, [str(p) for p in java_files], chunksize=16):
                    if out:
                        changed_paths.add(Path(out))
        except (PermissionError, OSError):
            _init_callsite_worker(pattern_specs, dry_run)
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_jobs) as pool:
                for out in pool.map(_rewrite_callsite_file_worker, [str(p) for p in java_files]):
                    if out:
                        changed_paths.add(Path(out))
    return changed_paths


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


def _build_reverse_rename_map(
    rename_map: dict[tuple[str, str, str], str],
) -> dict[tuple[str, str, str], str]:
    reverse: dict[tuple[str, str, str], str] = {}
    for (owner, kind, old), new in rename_map.items():
        if not owner or kind not in ("field", "method") or not old or not new:
            continue
        reverse.setdefault((owner, kind, new), old)
    return reverse


def _rewrite_line_with_reverse_symbols(
    line: str,
    *,
    owner: str,
    reverse_map: dict[tuple[str, str, str], str],
) -> str:
    out = line
    pairs: list[tuple[str, str]] = []
    for (candidate_owner, _kind, new_name), old_name in reverse_map.items():
        if candidate_owner != owner:
            continue
        pairs.append((new_name, old_name))
    for new_name, old_name in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
        out = re.sub(rf"\b{re.escape(new_name)}\b", old_name, out)
    return out


def _rewrite_line_with_aliases(line: str, aliases: dict[str, str]) -> str:
    out = line
    for alias, canonical in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        out = re.sub(rf"\b{re.escape(alias)}\b", canonical, out)
    return out


def _candidate_static_source_lines(
    source_line: str,
    *,
    source_class: str,
    target_class: str,
    reverse_map: dict[tuple[str, str, str], str],
    local_aliases: dict[str, str],
) -> list[str]:
    normalized = source_line.strip()
    candidates: list[str] = []

    def add(line: str) -> None:
        s = line.strip()
        if not s:
            return
        if s not in candidates:
            candidates.append(s)

    add(normalized)
    add(_normalize_static_init_line_for_target(normalized, target_class))
    add(_normalize_static_init_line_for_target(normalized, source_class))

    for base in list(candidates):
        add(_rewrite_line_with_reverse_symbols(base, owner=source_class, reverse_map=reverse_map))
    for base in list(candidates):
        add(_rewrite_line_with_aliases(base, local_aliases))

    return candidates


def _candidate_names(
    rename_map: dict[tuple[str, str, str], str],
    reverse_rename_map: dict[tuple[str, str, str], str],
    *,
    owner: str,
    kind: str,
    old_name: str,
) -> list[str]:
    names: list[str] = [old_name]
    alt = rename_map.get((owner, kind, old_name), "")
    if alt and alt != old_name:
        names.append(alt)
    reverse_alt = reverse_rename_map.get((owner, kind, old_name), "")
    if reverse_alt and reverse_alt != old_name:
        names.append(reverse_alt)
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
    reverse_rename_map: dict[tuple[str, str, str], str],
) -> tuple[int, list[tuple[str, str, str, str]], set[Path]]:
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
    static_source_lines: list[list[str]] = []
    static_target_lines: list[str] = []
    local_aliases: dict[str, str] = {}
    for move in manifest.moves:
        if move.kind not in ("field", "method"):
            continue
        for candidate in _candidate_names(
            rename_map,
            reverse_rename_map,
            owner=manifest.target_class,
            kind=move.kind,
            old_name=move.name,
        ):
            if candidate and candidate != move.name:
                local_aliases[candidate] = move.name
    for move in manifest.moves:
        if move.kind == "static_init":
            source_line = move.name.strip()
            target_line = _normalize_static_init_line_for_target(source_line, manifest.target_class)
            if source_line:
                static_source_lines.append(
                    _candidate_static_source_lines(
                        source_line,
                        source_class=source_class,
                        target_class=manifest.target_class,
                        reverse_map=reverse_rename_map,
                        local_aliases=local_aliases,
                    )
                )
            if target_line:
                static_target_lines.append(target_line)
            to_move.append(move)
            continue
        candidate_names = _candidate_names(
            rename_map,
            reverse_rename_map,
            owner=source_class,
            kind=move.kind,
            old_name=move.name,
        )
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
            target_candidate_names = _candidate_names(
                rename_map,
                reverse_rename_map,
                owner=manifest.target_class,
                kind=move.kind,
                old_name=move.name,
            )
            for source_candidate in candidate_names:
                if source_candidate not in target_candidate_names:
                    target_candidate_names.append(source_candidate)
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
                    final_candidate_names = _candidate_names(
                        rename_map,
                        reverse_rename_map,
                        owner=final_class,
                        kind=move.kind,
                        old_name=move.name,
                    )
                    for source_candidate in candidate_names:
                        if source_candidate not in final_candidate_names:
                            final_candidate_names.append(source_candidate)
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

    touched_files: set[Path] = set()
    new_source = source_text
    target_updated = target_text
    if to_move:
        if move_spans:
            new_source = _remove_spans(new_source, move_spans)
        if moved_text:
            target_updated = _insert_members_in_target(target_updated, moved_text)
    for index, source_lines in enumerate(static_source_lines):
        removed_any = False
        for source_line in source_lines:
            new_source, removed = _remove_static_init_line(new_source, source_line)
            removed_any = removed_any or removed
            if removed:
                break
        if not removed_any and source_lines:
            target_line = static_target_lines[index] if index < len(static_target_lines) else ""
            if target_line and _line_present(target_updated, target_line):
                continue
            raise SystemExit(f"{manifest.path}: missing source static_init line candidates: {source_lines}")
    for target_line in static_target_lines:
        target_updated, _ = _insert_static_init_line(target_updated, target_line)
    new_source = _qualify_source_member_refs(new_source, manifest.target_class, moved_field_names, moved_method_names)
    new_source = _remove_empty_static_blocks(new_source)
    new_source = _normalize_blank_lines(new_source)
    if not dry_run:
        if new_source != source_text:
            touched_files.add(source_path.resolve())
        source_path.write_text(new_source, encoding="utf-8")
    target_updated = _sync_target_imports(source_text, target_updated, imported_member_texts)
    if not dry_run and target_updated != target_text:
        target_path.write_text(target_updated, encoding="utf-8")
        touched_files.add(target_path.resolve())

    batch_targets = [(source_class, from_name, target_class, to_name) for from_name, target_class, to_name in callsite_targets]
    return len(to_move), batch_targets, touched_files


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract selected static members into target classes (manifest-driven).")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--manifest-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--rename-csv", type=Path, default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-manifests", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("EXTRACT_JOBS", "1")))
    ap.add_argument(
        "--rust-callsites",
        choices=("off", "auto", "build"),
        default=os.environ.get("EXTRACT_RUST_CALLSITES", "auto"),
        help="Use Rust backend for callsite rewrites (off|auto|build).",
    )
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    ap.add_argument(
        "--write-touched-files",
        type=Path,
        default=None,
        help="Optional output file listing touched Java files relative to --src-dir.",
    )
    ap.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Optional cache state file used to skip unchanged manifests across loop runs.",
    )
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
    reverse_rename_map = _build_reverse_rename_map(rename_map)
    rename_csv = args.rename_csv.resolve()
    rust_callsites = (args.rust_callsites or "auto").strip().lower()

    state: dict[str, dict[str, object]] = {}
    if args.state_file is not None and args.state_file.exists():
        try:
            loaded = json.loads(args.state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = {str(k): dict(v) for k, v in loaded.items() if isinstance(v, dict)}
        except Exception:
            state = {}

    def _sig(path: Path) -> str:
        if not path.exists():
            return "missing"
        stat = path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def _manifest_key(manifest: ExtractManifest) -> str:
        try:
            return str(manifest.path.resolve().relative_to(args.manifest_dir.resolve()))
        except Exception:
            return str(manifest.path.resolve())

    def _manifest_fingerprint(manifest: ExtractManifest) -> str:
        source_path = (src_dir / manifest.source).resolve()
        target_path = (src_dir / manifest.target_file).resolve()
        payload = {
            "manifest": _sig(manifest.path.resolve()),
            "source": _sig(source_path),
            "target": _sig(target_path),
            "rename_csv": _sig(rename_csv),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    moved_total = 0
    touched_total = 0
    batched_callsite_targets: list[tuple[str, str, str, str]] = []
    touched_paths: set[Path] = set()
    new_state: dict[str, dict[str, object]] = {}
    for manifest in manifests:
        key = _manifest_key(manifest)
        fp = _manifest_fingerprint(manifest)
        if args.state_file is not None and not args.dry_run:
            cached = state.get(key)
            if cached and cached.get("fingerprint") == fp and cached.get("status") == "ok":
                moved_cached = int(cached.get("moved", 0))
                queued_cached = int(cached.get("queued", 0))
                moved_total += moved_cached
                print(f"{manifest.path}: skipped unchanged (moved {moved_cached}, queued {queued_cached})")
                new_state[key] = cached
                continue
        moved, callsite_targets, touched = _process_manifest(
            manifest,
            src_dir=src_dir,
            parser=parser,
            dry_run=args.dry_run,
            move_map=move_map,
            rename_map=rename_map,
            reverse_rename_map=reverse_rename_map,
        )
        moved_total += moved
        batched_callsite_targets.extend(callsite_targets)
        touched_paths.update(touched)
        print(f"{manifest.path}: moved {moved} members, queued {len(callsite_targets)} callsite rewrites")
        if args.state_file is not None and not args.dry_run:
            new_state[key] = {
                "status": "ok",
                "fingerprint": fp,
                "moved": moved,
                "queued": len(callsite_targets),
            }
    callsite_touched = _replace_callsites_batch(
        src_dir,
        targets=batched_callsite_targets,
        dry_run=args.dry_run,
        jobs=args.jobs,
        rust_callsites=rust_callsites,
    )
    touched_paths.update(callsite_touched)
    touched_total = len(callsite_touched)
    if args.write_touched_files is not None:
        args.write_touched_files.parent.mkdir(parents=True, exist_ok=True)
        rel_lines = []
        for p in sorted(touched_paths):
            try:
                rel_lines.append(str(p.relative_to(src_dir.resolve())))
            except ValueError:
                rel_lines.append(str(p))
        args.write_touched_files.write_text("\n".join(rel_lines) + ("\n" if rel_lines else ""), encoding="utf-8")
    if args.state_file is not None and not args.dry_run:
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        args.state_file.write_text(json.dumps(new_state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Done. moved={moved_total}, callsite_files={touched_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
