#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import fnmatch
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    out_dir: str
    match: str
    pattern: str

    def matches(self, class_name: str) -> bool:
        m = self.match
        p = self.pattern
        if m == "exact":
            return class_name == p
        if m == "prefix":
            return class_name.startswith(p)
        if m == "suffix":
            return class_name.endswith(p)
        if m == "glob":
            return fnmatch.fnmatch(class_name, p)
        if m == "regex":
            return re.search(p, class_name) is not None
        raise ValueError(f"Unknown match type: {m!r}")


def _copy_flat_java(src_dir: Path, dst_dir: Path) -> int:
    dst_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for p in sorted(src_dir.glob("*.java")):
        shutil.copy2(p, dst_dir / p.name)
        count += 1
    return count


def _remove_java_files_recursive(dst_dir: Path) -> int:
    removed = 0
    if not dst_dir.exists():
        return 0
    for p in sorted(dst_dir.rglob("*.java")):
        p.unlink()
        removed += 1
    return removed


def _remove_empty_dirs(dst_dir: Path) -> None:
    if not dst_dir.exists():
        return
    # Bottom-up, keep root even if empty.
    dirs = [d for d in dst_dir.rglob("*") if d.is_dir()]
    for p in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
        if p == dst_dir:
            continue
        try:
            next(p.iterdir())
        except StopIteration:
            p.rmdir()


def _run_apply_class_renames(*, csv_path: Path, src_dir: Path, report: Path) -> None:
    cmd = [
        sys.executable,
        "tools/apply_class_renames.py",
        "--csv",
        str(csv_path),
        "--src-dir",
        str(src_dir),
        "--report",
        str(report),
        "--max-renames",
        "0",
        "--no-git-mv",
    ]
    subprocess.run(cmd, check=True)


def _count_class_mappings(csv_path: Path) -> int:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return 0
        required = {"src", "dst"}
        if not required.issubset(set(reader.fieldnames)):
            return 0
        count = 0
        for row in reader:
            src = (row.get("src") or "").strip()
            dst = (row.get("dst") or "").strip()
            if src and dst:
                count += 1
        return count


def _load_rules(rules_csv: Path) -> list[Rule]:
    if not rules_csv.exists():
        raise SystemExit(f"--rules not found: {rules_csv}")

    def _non_comment_lines() -> list[str]:
        lines: list[str] = []
        for raw in rules_csv.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                continue
            lines.append(raw)
        return lines

    lines = _non_comment_lines()
    if not lines:
        return []

    reader = csv.DictReader(lines)
    required = {"dir", "match", "pattern"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise SystemExit(f"--rules must have header: dir,match,pattern (got: {reader.fieldnames})")

    rules: list[Rule] = []
    for i, row in enumerate(reader, start=2):
        out_dir = (row.get("dir") or "").strip()
        match = (row.get("match") or "").strip()
        pattern = (row.get("pattern") or "").strip()
        if not out_dir or not match or not pattern:
            raise SystemExit(f"Invalid rules row at {rules_csv}:{i} (dir/match/pattern required)")
        rules.append(Rule(out_dir=out_dir, match=match, pattern=pattern))
    return rules


def _choose_dir(class_name: str, rules: list[Rule], default_dir: str) -> tuple[str, int | None]:
    for i, rule in enumerate(rules, start=1):
        if rule.matches(class_name):
            return rule.out_dir, i
    return default_dir, None


def _write_layout_report(
    *,
    report_path: Path,
    rules_csv: Path,
    total: int,
    placed: dict[str, int],
    unmatched: list[str],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Refactor layout report")
    lines.append("")
    lines.append(f"- Rules: `{rules_csv}`")
    lines.append(f"- Files placed: **{total}**")
    lines.append(f"- Folders: **{len(placed)}**")
    lines.append(f"- Unmatched: **{len(unmatched)}** (fell back to `_unclassified/`)")
    lines.append("")
    lines.append("## Folder counts")
    lines.append("")
    lines.append("| Folder | Files |")
    lines.append("|---|---:|")
    for out_dir in sorted(placed, key=lambda d: (-placed[d], d)):
        lines.append(f"| `{out_dir}` | {placed[out_dir]} |")
    lines.append("")
    if unmatched:
        lines.append("## Unmatched")
        lines.append("")
        for name in sorted(unmatched):
            lines.append(f"- `{name}.java`")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Generate an organized (subdirectory) refactor tree from client/src + a classes.csv mapping. "
            "Output is idempotent: reruns rewrite dst_dir from scratch."
        )
    )
    ap.add_argument("--csv", type=Path, default=Path("client/refactor/.refactor-plan/generated/classes.csv"), help="Rename mapping CSV (src,dst[,score,anchors])")
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"), help="Flat directory containing *.java")
    ap.add_argument("--dst-dir", type=Path, default=Path("client/refactor"), help="Output directory (nested)")
    ap.add_argument("--rules", type=Path, default=Path("client/refactor/.refactor-plan/layout_rules.csv"), help="Layout rules CSV")
    ap.add_argument("--default-dir", default="_unclassified", help="Fallback folder for files that match no rules")
    ap.add_argument("--work-dir", type=Path, default=Path("build/refactor-layout"), help="Scratch working directory under build/")
    ap.add_argument("--rename-report", type=Path, default=Path("build/refactor-layout-rename-report.md"), help="Rename report output path")
    ap.add_argument("--report", type=Path, default=Path("build/refactor-layout-report.md"), help="Layout report output path")
    args = ap.parse_args(argv)

    root = Path.cwd()
    csv_path = (root / args.csv).resolve()
    src_dir = (root / args.src_dir).resolve()
    dst_dir = (root / args.dst_dir).resolve()
    rules_csv = (root / args.rules).resolve()
    work_dir = (root / args.work_dir).resolve()
    rename_report = (root / args.rename_report).resolve()
    report = (root / args.report).resolve()

    if not csv_path.exists():
        raise SystemExit(f"--csv not found: {csv_path}")
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")
    class_mappings = _count_class_mappings(csv_path)
    if class_mappings == 0:
        raise SystemExit(
            f"--csv has no class mappings: {csv_path}\n"
            "Run `make build-refactor-views` and ensure canonical `*.class_rename.csv` inputs exist."
        )

    rules = _load_rules(rules_csv)

    # Rebuild into a temp dir for idempotence.
    tmp_src = work_dir / "src"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    tmp_src.mkdir(parents=True, exist_ok=True)

    copied = _copy_flat_java(src_dir, tmp_src)
    if copied == 0:
        raise SystemExit(f"No .java files found under: {src_dir}")

    rename_report.parent.mkdir(parents=True, exist_ok=True)
    _run_apply_class_renames(csv_path=csv_path, src_dir=tmp_src, report=rename_report)

    dst_dir.mkdir(parents=True, exist_ok=True)
    removed = _remove_java_files_recursive(dst_dir)
    _remove_empty_dirs(dst_dir)

    placed: dict[str, int] = {}
    unmatched: list[str] = []
    written = 0

    for p in sorted(tmp_src.glob("*.java")):
        class_name = p.stem
        out_dir, _rule_idx = _choose_dir(class_name, rules, args.default_dir)
        if out_dir == args.default_dir:
            unmatched.append(class_name)
        target_dir = dst_dir / out_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target_dir / p.name)
        placed[out_dir] = placed.get(out_dir, 0) + 1
        written += 1

    _write_layout_report(
        report_path=report,
        rules_csv=rules_csv,
        total=written,
        placed=placed,
        unmatched=unmatched,
    )

    print(f"Removed {removed} old .java files under {dst_dir}")
    print(f"Wrote {written} files to {dst_dir} (from {copied} source files).")
    print(f"Rename report: {rename_report}")
    print(f"Layout report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
