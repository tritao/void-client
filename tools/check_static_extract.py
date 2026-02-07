#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from static_extract_common import build_java_parser, load_manifests, parse_static_members


def _count_remaining_callsites(src_dir: Path, source_class: str, member: str) -> int:
    rx = re.compile(rf"\b{re.escape(source_class)}\s*\.\s*{re.escape(member)}\b")
    total = 0
    for java in src_dir.rglob("*.java"):
        text = java.read_text(encoding="utf-8", errors="replace")
        total += len(rx.findall(text))
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate static extraction manifests are fully applied.")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--manifest-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    manifests = load_manifests(args.manifest_dir.resolve())
    if not manifests:
        print(f"No extraction manifests found under {args.manifest_dir}")
        return 0
    parser = build_java_parser(out_so=args.language_so)
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
            key = (move.kind, move.name)
            if key in source_keys:
                errors.append(f"{manifest.path}: still in source: {move.kind} {move.name}")
            if key not in target_keys:
                target_rel = str(manifest.target_file)
                if (target_rel, move.kind, move.name) not in downstream_moves:
                    errors.append(f"{manifest.path}: missing in target: {move.kind} {move.name}")
            remain = _count_remaining_callsites(src_dir, source_class, move.name)
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
