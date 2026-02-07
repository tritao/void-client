#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from static_extract_common import build_java_parser, load_manifests, parse_static_members


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


def _count_remaining_callsites(src_dir: Path, source_class: str, members: list[str]) -> int:
    total = 0
    for member in members:
        rx = re.compile(rf"\b{re.escape(source_class)}\s*\.\s*{re.escape(member)}\b")
        for java in src_dir.rglob("*.java"):
            text = java.read_text(encoding="utf-8", errors="replace")
            total += len(rx.findall(text))
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate static extraction manifests are fully applied.")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--manifest-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--rename-csv", type=Path, default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"))
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    manifests = load_manifests(args.manifest_dir.resolve())
    if not manifests:
        print(f"No extraction manifests found under {args.manifest_dir}")
        return 0
    parser = build_java_parser(out_so=args.language_so)
    rename_map = _load_symbol_renames(args.rename_csv.resolve())
    downstream_moves: set[tuple[str, str, str]] = set()
    for manifest in manifests:
        source_rel = str(manifest.source)
        for move in manifest.moves:
            downstream_moves.add((source_rel, move.kind, move.name))

    errors: list[str] = []
    for manifest in manifests:
        source_path = (src_dir / manifest.source).resolve()
        target_path = (src_dir / manifest.target_file).resolve()
        if not source_path.exists():
            errors.append(f"{manifest.path}: source missing: {source_path}")
            continue
        if not target_path.exists():
            errors.append(f"{manifest.path}: target missing: {target_path}")
            continue
        source_keys = {(m.kind, m.name) for m in parse_static_members(source_path, parser=parser)}
        target_keys = {(m.kind, m.name) for m in parse_static_members(target_path, parser=parser)}
        source_class = source_path.stem
        for move in manifest.moves:
            source_names = _candidate_names(rename_map, owner=source_class, kind=move.kind, old_name=move.name)
            target_names = _candidate_names(rename_map, owner=manifest.target_class, kind=move.kind, old_name=move.name)
            if any((move.kind, n) in source_keys for n in source_names):
                errors.append(f"{manifest.path}: still in source: {move.kind} {move.name}")
            if not any((move.kind, n) in target_keys for n in target_names):
                target_rel = str(manifest.target_file)
                if (target_rel, move.kind, move.name) not in downstream_moves:
                    errors.append(f"{manifest.path}: missing in target: {move.kind} {move.name}")
            remain = _count_remaining_callsites(src_dir, source_class, source_names)
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
