#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
UNRENAMED_CLASS_RE = re.compile(r"(Class|Static)\d+(?:_Sub\d+)*\Z")
UNRENAMED_NODE_RE = re.compile(r"Node(?:_Sub\d+)+\Z")

JAVA_FIELD_RE = re.compile(
    r"\b(public|protected|private)\s+"
    r"(?P<static>static\s+)?"
    r"(?P<final>final\s+)?"
    r"[\w<>\[\], ?]+\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(=|;)"
)


@dataclass(frozen=True)
class JavaFileInfo:
    path: Path
    class_name: str
    bytes: int
    lines: int
    ref_classes: set[str]
    static_fields: int
    static_mutable_fields: int


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


def strip_java(code: str) -> str:
    """
    Removes comments and string/char literals.
    Not a full Java parser; intended for approximate identifier extraction.
    """
    out: list[str] = []
    i = 0
    n = len(code)
    state = "code"
    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                out.append(" ")
                i += 1
                continue
            if ch == "'":
                state = "char"
                out.append(" ")
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        if state == "line_comment":
            if ch == "\n":
                state = "code"
                out.append("\n")
            i += 1
            continue

        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
            else:
                i += 1
            continue

        if state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                state = "code"
            i += 1
            continue

        if state == "char":
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                state = "code"
            i += 1
            continue

    return "".join(out)


def remove_package_and_imports(code: str) -> str:
    out_lines: list[str] = []
    for line in code.splitlines(True):
        stripped = line.lstrip()
        if stripped.startswith("package ") or stripped.startswith("import "):
            out_lines.append("\n" if line.endswith("\n") else "")
            continue
        out_lines.append(line)
    return "".join(out_lines)


def count_lines(data: bytes) -> int:
    if not data:
        return 0
    lines = data.count(b"\n")
    if not data.endswith(b"\n"):
        lines += 1
    return lines


def gather_java_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.java") if p.is_file()]


def build_infos(java_files: list[Path], all_class_names: set[str]) -> list[JavaFileInfo]:
    infos: list[JavaFileInfo] = []
    for path in java_files:
        data = path.read_bytes()
        size = len(data)
        lines = count_lines(data)
        text = data.decode("utf-8", errors="replace")

        scan_text = strip_java(remove_package_and_imports(text))
        tokens = set(IDENT_RE.findall(scan_text))
        ref_classes = (tokens & all_class_names) - {path.stem}

        static_fields = 0
        static_mutable_fields = 0
        for m in JAVA_FIELD_RE.finditer(text):
            is_static = m.group("static") is not None
            is_final = m.group("final") is not None
            if not is_static:
                continue
            static_fields += 1
            if not is_final:
                static_mutable_fields += 1

        infos.append(
            JavaFileInfo(
                path=path,
                class_name=path.stem,
                bytes=size,
                lines=lines,
                ref_classes=ref_classes,
                static_fields=static_fields,
                static_mutable_fields=static_mutable_fields,
            )
        )
    return infos


def fmt_int(n: int) -> str:
    return f"{n:,}"


def pct(part: int, total: int) -> float:
    return 0.0 if total == 0 else (part * 100.0 / total)


def render_markdown(
    *,
    repo_root: Path,
    root: Path,
    generated: dt.date,
    infos: list[JavaFileInfo],
    unrenamed_only: bool,
    top_n: int,
    callers_top_n: int,
    excludes: list[str] | None = None,
    excluded_nodes: int = 0,
) -> str:
    by_name = {i.class_name: i for i in infos}
    class_names = set(by_name.keys())

    fan_in: dict[str, int] = defaultdict(int)
    callers: dict[str, list[str]] = defaultdict(list)
    for src_name, info in by_name.items():
        for dst_name in info.ref_classes:
            if dst_name not in class_names:
                continue
            fan_in[dst_name] += 1
            callers[dst_name].append(src_name)

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(repo_root))
        except ValueError:
            return str(path)

    rows = infos if not unrenamed_only else [i for i in infos if is_unrenamed_basename(i.class_name)]

    def stat_row(i: JavaFileInfo) -> dict[str, object]:
        c = callers.get(i.class_name, [])
        c_sorted = sorted(c, key=lambda n: (fan_in.get(n, 0), n), reverse=True)
        callers_preview = ", ".join(c_sorted[:callers_top_n])
        return {
            "name": i.class_name,
            "path": rel(i.path),
            "bytes": i.bytes,
            "lines": i.lines,
            "fan_in": fan_in.get(i.class_name, 0),
            "fan_out": len(i.ref_classes),
            "static_fields": i.static_fields,
            "static_mutable_fields": i.static_mutable_fields,
            "callers": callers_preview,
        }

    stats = [stat_row(i) for i in rows]

    def table(title: str, sort_key, reverse: bool = True) -> list[str]:
        top = sorted(stats, key=sort_key, reverse=reverse)[:top_n]
        out: list[str] = [f"## {title}", ""]
        out += [
            "fan_in | fan_out | mut_statics | statics | bytes | lines | class | path",
            "---:|---:|---:|---:|---:|---:|---|---",
        ]
        for r in top:
            out.append(
                f"{r['fan_in']} | {r['fan_out']} | {r['static_mutable_fields']} | {r['static_fields']} | {r['bytes']} | {r['lines']} | `{r['name']}` | `{r['path']}`"
            )
        out.append("")
        return out

    total = len(infos)
    total_un = sum(1 for i in infos if is_unrenamed_basename(i.class_name))
    scope_label = "unrenamed only" if unrenamed_only else "all classes"
    excludes = excludes or []

    out: list[str] = []
    out += [
        "# Fan-in / fan-out report (void-client)",
        "",
        f"Generated: {generated.isoformat()}",
        "",
        "Heuristic dependency graph over top-level Java classes, based on identifier matching (not a full Java parser).",
        "",
        "## Scope",
        "",
        f"- Root: `{rel(root)}`",
        f"- Included nodes: **{total}** classes; unrenamed (filename heuristic): **{total_un} ({pct(total_un, total):.1f}%)**",
        f"- Excluded nodes: **{excluded_nodes}**"
        + (f" (by `--exclude`: {', '.join(f'`{e}`' for e in excludes)})" if excludes else ""),
        f"- Tables below show: **{scope_label}**",
        "",
        "## Notes",
        "",
        "- Reference extraction strips `package`/`import`, comments, and string/char literals before scanning identifiers.",
        "- `fan_in`/`fan_out` counts are for references to other top-level classes in the scanned root.",
        "- `mut_statics` counts `static` fields that are not `final` (proxy for global mutable state).",
        "",
        "## Regenerate",
        "",
        f"- `python {rel(repo_root / 'tools' / 'fan_graph.py')} --root {rel(root)} --write docs/fan-graph.md`",
        "",
    ]

    out += table("Top by fan-in", lambda r: (r["fan_in"], r["bytes"]))
    out += table("Top by fan-out", lambda r: (r["fan_out"], r["bytes"]))
    out += table("Top by mutable statics", lambda r: (r["static_mutable_fields"], r["fan_in"], r["bytes"]))

    top_in = sorted(stats, key=lambda r: (r["fan_in"], r["bytes"]), reverse=True)[: min(top_n, 30)]
    out += ["## Top callers (for top fan-in)", ""]
    for r in top_in:
        callers_str = r["callers"] or "(none detected)"
        out.append(f"- `{r['name']}` callers: {callers_str}")
    out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Heuristic fan-in/fan-out report for void-client Java classes.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
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
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all classes (default: only unrenamed-by-filename).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        help="How many rows to show per table.",
    )
    parser.add_argument(
        "--callers-top",
        type=int,
        default=12,
        help="How many callers to list per class in the callers section.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Regex applied to class name OR relative path. Matching nodes are excluded. Can be repeated.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    root = args.root
    if not root.is_absolute():
        root = (repo_root / root).resolve()

    java_files_all = gather_java_files(root)
    excludes = [re.compile(p) for p in (args.exclude or [])]

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(repo_root))
        except ValueError:
            return str(p)

    def excluded(p: Path) -> bool:
        if not excludes:
            return False
        r = rel(p)
        stem = p.stem
        return any(rx.search(stem) or rx.search(r) for rx in excludes)

    java_files = [p for p in java_files_all if not excluded(p)]
    excluded_nodes = len(java_files_all) - len(java_files)

    all_class_names = {p.stem for p in java_files}
    infos = build_infos(java_files, all_class_names=all_class_names)

    report = render_markdown(
        repo_root=repo_root,
        root=root,
        generated=dt.date.today(),
        infos=infos,
        unrenamed_only=(not args.all),
        top_n=max(1, args.top),
        callers_top_n=max(1, args.callers_top),
        excludes=(args.exclude or []),
        excluded_nodes=excluded_nodes,
    )

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
