#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from collections import defaultdict

from tools.refactor.common.ts_java import build_java_parser
from tools.refactor.extract.manifest import load_manifests
from tools.refactor.extract.static_members import parse_static_members


def _load_symbol_renames(path: Path) -> dict[tuple[str, str, str], str]:
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
        if kind not in ("field", "method") or not owner or not old or not new:
            continue
        reverse.setdefault((owner, kind, new), old)
    return reverse


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
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _build_callsite_counts(
    src_dir: Path,
    *,
    members_by_class: dict[str, set[str]],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    if not members_by_class:
        return counts

    class_patterns: dict[str, re.Pattern[str]] = {}
    for source_class, members in members_by_class.items():
        if not members:
            continue
        alternation = "|".join(sorted((re.escape(m) for m in members), key=len, reverse=True))
        class_patterns[source_class] = re.compile(
            rf"\b{re.escape(source_class)}\s*\.\s*({alternation})\b"
        )

    for java in src_dir.rglob("*.java"):
        text = java.read_text(encoding="utf-8", errors="replace")
        for source_class, rx in class_patterns.items():
            for match in rx.finditer(text):
                member = match.group(1)
                counts[(source_class, member)] += 1
    return counts


def _normalize_static_init_line_for_target(line: str, target_class: str) -> str:
    stripped = line.strip()
    prefix = f"{target_class}."
    if stripped.startswith(prefix):
        return stripped[len(prefix) :].strip()
    return stripped


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


def _line_present(text: str, line: str) -> bool:
    normalized = line.strip()
    if not normalized:
        return False
    return re.search(rf"(?m)^[ \t]*{re.escape(normalized)}[ \t]*$", text) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate static extraction manifests are fully applied.")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--manifest-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--rename-csv", type=Path, default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"))
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    ap.add_argument(
        "--skip-callsites",
        action="store_true",
        help="Skip global source_class.member callsite validation (faster, less strict).",
    )
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    manifests = load_manifests(args.manifest_dir.resolve())
    if not manifests:
        print(f"No extraction manifests found under {args.manifest_dir}")
        return 0
    parser = build_java_parser(out_so=args.language_so)
    rename_map = _load_symbol_renames(args.rename_csv.resolve())
    reverse_rename_map = _build_reverse_rename_map(rename_map)
    downstream_moves: set[tuple[str, str, str]] = set()
    callsite_members_by_class: dict[str, set[str]] = defaultdict(set)
    for manifest in manifests:
        source_rel = str(manifest.source)
        source_class = manifest.source.stem
        for move in manifest.moves:
            downstream_moves.add((source_rel, move.kind, move.name))
            if move.kind in ("field", "method"):
                for candidate in _candidate_names(
                    rename_map,
                    owner=source_class,
                    kind=move.kind,
                    old_name=move.name,
                ):
                    callsite_members_by_class[source_class].add(candidate)

    callsite_counts: dict[tuple[str, str], int] = {}
    if not args.skip_callsites:
        callsite_counts = _build_callsite_counts(src_dir, members_by_class=callsite_members_by_class)

    errors: list[str] = []
    member_key_cache: dict[Path, set[tuple[str, str]]] = {}
    for manifest in manifests:
        source_path = (src_dir / manifest.source).resolve()
        target_path = (src_dir / manifest.target_file).resolve()
        if not source_path.exists():
            errors.append(f"{manifest.path}: source missing: {source_path}")
            continue
        if not target_path.exists():
            errors.append(f"{manifest.path}: target missing: {target_path}")
            continue
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        target_text = target_path.read_text(encoding="utf-8", errors="replace")
        source_keys = member_key_cache.get(source_path)
        if source_keys is None:
            source_keys = {(m.kind, m.name) for m in parse_static_members(source_path, parser=parser)}
            member_key_cache[source_path] = source_keys
        target_keys = member_key_cache.get(target_path)
        if target_keys is None:
            target_keys = {(m.kind, m.name) for m in parse_static_members(target_path, parser=parser)}
            member_key_cache[target_path] = target_keys
        source_class = source_path.stem
        local_aliases: dict[str, str] = {}
        for move in manifest.moves:
            if move.kind not in ("field", "method"):
                continue
            for candidate in _candidate_names(rename_map, owner=manifest.target_class, kind=move.kind, old_name=move.name):
                if candidate and candidate != move.name:
                    local_aliases[candidate] = move.name
        for move in manifest.moves:
            if move.kind == "static_init":
                source_line = move.name.strip()
                target_line = _normalize_static_init_line_for_target(source_line, manifest.target_class)
                source_candidates = _candidate_static_source_lines(
                    source_line,
                    source_class=source_class,
                    target_class=manifest.target_class,
                    reverse_map=reverse_rename_map,
                    local_aliases=local_aliases,
                )
                if any(_line_present(source_text, candidate) for candidate in source_candidates):
                    errors.append(f"{manifest.path}: static_init still in source: {source_line}")
                target_rel = str(manifest.target_file)
                moved_downstream = (target_rel, move.kind, move.name) in downstream_moves
                if not moved_downstream and not (_line_present(target_text, target_line) or _line_present(target_text, source_line)):
                    errors.append(f"{manifest.path}: static_init missing in target: {source_line}")
                continue
            source_names = _candidate_names(rename_map, owner=source_class, kind=move.kind, old_name=move.name)
            target_names = _candidate_names(rename_map, owner=manifest.target_class, kind=move.kind, old_name=move.name)
            if any((move.kind, n) in source_keys for n in source_names):
                errors.append(f"{manifest.path}: still in source: {move.kind} {move.name}")
            if not any((move.kind, n) in target_keys for n in target_names):
                target_rel = str(manifest.target_file)
                if (target_rel, move.kind, move.name) not in downstream_moves:
                    errors.append(f"{manifest.path}: missing in target: {move.kind} {move.name}")
            if not args.skip_callsites:
                remain = sum(callsite_counts.get((source_class, member), 0) for member in source_names)
                if remain:
                    errors.append(f"{manifest.path}: remaining callsites {source_class}.{move.name}: {remain}")

    if errors:
        print("Static extract validation failed:")
        for line in errors:
            print(f"- {line}")
        return 1
    print("Static extract validation OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
