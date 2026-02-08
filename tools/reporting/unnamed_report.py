#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path


UNRENAMED_CLASS_RE = re.compile(r"(Class|Static)\d+(?:_Sub\d+)*\Z")
UNRENAMED_NODE_RE = re.compile(r"Node(?:_Sub\d+)+\Z")


@dataclass(frozen=True)
class FileStat:
    path: Path
    bytes: int
    lines: int
    unrenamed: bool


def is_unrenamed_basename(stem: str) -> bool:
    if not stem:
        return False
    if stem[0].islower():
        return True
    if UNRENAMED_CLASS_RE.fullmatch(stem):
        return True
    if UNRENAMED_NODE_RE.fullmatch(stem):
        return True
    return False


def count_lines_fast(path: Path) -> int:
    data = path.read_bytes()
    if not data:
        return 0
    lines = data.count(b"\n")
    if not data.endswith(b"\n"):
        lines += 1
    return lines


def gather_java_stats(root: Path) -> list[FileStat]:
    rows: list[FileStat] = []
    if not root.exists():
        return rows
    for path in root.rglob("*.java"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            lines = count_lines_fast(path)
        except OSError:
            continue
        rows.append(
            FileStat(
                path=path,
                bytes=size,
                lines=lines,
                unrenamed=is_unrenamed_basename(path.stem),
            )
        )
    return rows


def pct(part: int, total: int) -> float:
    return 0.0 if total == 0 else (part * 100.0 / total)


def fmt_int(n: int) -> str:
    return f"{n:,}"


def render_report(*, rows: list[FileStat], repo_root: Path, generated: dt.date) -> str:
    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return str(p)

    total_files = len(rows)
    total_bytes = sum(r.bytes for r in rows)
    total_lines = sum(r.lines for r in rows)
    un = [r for r in rows if r.unrenamed]
    un_files = len(un)
    un_bytes = sum(r.bytes for r in un)
    un_lines = sum(r.lines for r in un)
    top_un = sorted(un, key=lambda r: r.bytes, reverse=True)[:15]

    out: list[str] = []
    out += [
        "# Unnamed / Unrenamed status (void-client)",
        "",
        f"Generated: {generated.isoformat()}",
        "",
        "Quick progress snapshot of “still obfuscated” Java source files based on filename heuristics.",
        "",
        "## Heuristic used",
        "",
        "A file is counted as **unrenamed** if its basename matches any of:",
        "",
        "- Starts with a lowercase letter (e.g. `client.java`, `oa.java`, `i.java`)",
        "- `Class###` with optional `_Sub#` chains (e.g. `Class101_Sub3.java`)",
        "- `Static###` with optional `_Sub#` chains (e.g. `Static684.java`)",
        "- `Node_Sub...` (e.g. `Node_Sub1_Sub27.java`)",
        "",
        "This does **not** inspect method/field names; it’s only a quick proxy for “needs renaming”.",
        "",
        "## Percent unrenamed (by file count / LOC / bytes)",
        "",
        f"- Unrenamed files: **{un_files} / {total_files} ({pct(un_files, total_files):.1f}%)**",
        f"- Unrenamed LOC: **{fmt_int(un_lines)} / {fmt_int(total_lines)} ({pct(un_lines, total_lines):.1f}%)**",
        f"- Unrenamed bytes: **{fmt_int(un_bytes)} / {fmt_int(total_bytes)} ({pct(un_bytes, total_bytes):.1f}%)**",
        "",
        "## Biggest unrenamed files (by bytes)",
        "",
        "Bytes | Lines | Path",
        "---:|---:|---",
    ]
    for r in top_un:
        out.append(f"{fmt_int(r.bytes)} | {fmt_int(r.lines)} | `{rel(r.path)}`")
    out.append("")

    out += [
        "## Regenerate",
        "",
        "Run:",
        "",
        f"- `python {rel(repo_root / 'tools' / 'reporting' / 'unnamed_report.py')} --write {rel(repo_root / 'docs' / 'unnamed-status.md')}`",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report unrenamed Java files by filename heuristics (void-client).")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (default: inferred from this script location).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("client/src"),
        help="Java source root relative to repo root (default: client/src).",
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write markdown report to this path (relative to repo root allowed).",
    )
    args = parser.parse_args()

    repo_root: Path = args.repo_root.resolve()
    src_root = args.root
    if not src_root.is_absolute():
        src_root = (repo_root / src_root).resolve()

    rows = gather_java_stats(src_root)
    generated = dt.date.today()
    report = render_report(rows=rows, repo_root=repo_root, generated=generated)

    if args.write is None:
        print(report)
        return 0

    out_path = args.write
    if not out_path.is_absolute():
        out_path = (repo_root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    rel_path = os.path.relpath(out_path, repo_root)
    print(f"Wrote {rel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
