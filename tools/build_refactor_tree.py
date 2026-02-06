#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _copy_flat_java(src_dir: Path, dst_dir: Path) -> int:
    dst_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for p in sorted(src_dir.glob("*.java")):
        shutil.copy2(p, dst_dir / p.name)
        count += 1
    return count


def _remove_java_files(dst_dir: Path) -> int:
    removed = 0
    for p in dst_dir.glob("*.java"):
        p.unlink()
        removed += 1
    return removed


def _run_apply_class_renames(*, csv: Path, src_dir: Path, report: Path) -> None:
    cmd = [
        sys.executable,
        "tools/apply_class_renames.py",
        "--csv",
        str(csv),
        "--src-dir",
        str(src_dir),
        "--report",
        str(report),
        "--max-renames",
        "0",
        "--no-git-mv",
    ]
    subprocess.run(cmd, check=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate a renamed copy of client/src into client/refactor (idempotent).")
    ap.add_argument("--csv", type=Path, default=Path("classes.csv"), help="Rename mapping CSV (src,dst[,score,anchors])")
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"), help="Flat directory containing *.java")
    ap.add_argument("--dst-dir", type=Path, default=Path("client/refactor"), help="Flat output directory (will contain *.java)")
    ap.add_argument("--work-dir", type=Path, default=Path("build/refactor-tree"), help="Scratch working directory under build/")
    ap.add_argument("--report", type=Path, default=Path("build/refactor-rename-report.md"), help="Rename report output path")
    args = ap.parse_args(argv)

    root = Path.cwd()
    csv = (root / args.csv).resolve()
    src_dir = (root / args.src_dir).resolve()
    dst_dir = (root / args.dst_dir).resolve()
    work_dir = (root / args.work_dir).resolve()
    report = (root / args.report).resolve()

    if not csv.exists():
        raise SystemExit(f"--csv not found: {csv}")
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Rebuild into a temp dir for idempotence.
    tmp_src = work_dir / "src"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    tmp_src.mkdir(parents=True, exist_ok=True)

    copied = _copy_flat_java(src_dir, tmp_src)
    if copied == 0:
        raise SystemExit(f"No .java files found under: {src_dir}")

    report.parent.mkdir(parents=True, exist_ok=True)
    _run_apply_class_renames(csv=csv, src_dir=tmp_src, report=report)

    # Sync output: replace only *.java, keep any metadata files (e.g. .gitignore/.gitkeep) in dst_dir.
    _remove_java_files(dst_dir)
    produced = _copy_flat_java(tmp_src, dst_dir)

    print(f"Wrote {produced} files to {dst_dir} (from {copied} source files).")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

