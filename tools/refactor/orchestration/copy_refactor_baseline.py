#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _copy_flat_java(src_dir: Path, dst_dir: Path) -> int:
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(src_dir.glob("*.java")):
        shutil.copy2(path, dst_dir / path.name)
        copied += 1
    return copied


def _is_protected_subpath(path: Path, *, root: Path, protected_top_dirs: set[str]) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if not rel.parts:
        return False
    return rel.parts[0] in protected_top_dirs


def _remove_java_files_recursive(dst_dir: Path) -> int:
    if not dst_dir.exists():
        return 0
    removed = 0
    for path in sorted(dst_dir.rglob("*.java")):
        path.unlink()
        removed += 1
    return removed


def _remove_empty_dirs(dst_dir: Path, *, protected_top_dirs: set[str] | None = None) -> None:
    if not dst_dir.exists():
        return
    protected = protected_top_dirs or {".git", ".refactor-plan"}
    dirs = [d for d in dst_dir.rglob("*") if d.is_dir()]
    for path in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
        if path == dst_dir:
            continue
        if _is_protected_subpath(path, root=dst_dir, protected_top_dirs=protected):
            continue
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reset client/refactor to a flat copy of client/src Java files "
            "(baseline snapshot before layout/refactor stages)."
        )
    )
    parser.add_argument("--src-dir", type=Path, default=Path("client/src"))
    parser.add_argument("--dst-dir", type=Path, default=Path("client/refactor"))
    args = parser.parse_args(argv)

    root = Path.cwd()
    src_dir = (root / args.src_dir).resolve()
    dst_dir = (root / args.dst_dir).resolve()
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")

    dst_dir.mkdir(parents=True, exist_ok=True)
    removed = _remove_java_files_recursive(dst_dir)
    _remove_empty_dirs(dst_dir, protected_top_dirs={".git", ".refactor-plan"})
    copied = _copy_flat_java(src_dir, dst_dir)

    print(f"Baseline copied: {copied} files from {src_dir} -> {dst_dir} (removed={removed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
