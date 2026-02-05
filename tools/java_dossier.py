#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
STRING_RE = re.compile(r"\"([^\"\\\\]|\\\\.)*\"")

JAVA_FIELD_RE = re.compile(
    r"\b(public|protected|private)\s+"
    r"(?P<static>static\s+)?"
    r"(?P<final>final\s+)?"
    r"(?P<type>[\w<>\[\], ?]+?)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(=|;)"
)

JAVA_METHOD_RE = re.compile(
    r"\b(public|protected|private)\s+"
    r"(?P<static>static\s+)?"
    r"(?P<type>[\w<>\[\], ?]+?)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)

UNRENAMED_CLASS_RE = re.compile(r"(Class|Static)\d+(?:_Sub\d+)*\Z")
UNRENAMED_NODE_RE = re.compile(r"Node(?:_Sub\d+)+\Z")


@dataclass(frozen=True)
class JavaNode:
    name: str
    path: Path
    bytes: int
    lines: int
    refs: set[str]
    static_fields: list[tuple[str, str, bool]]  # (type, name, is_final)
    static_methods: list[str]
    mutable_static_count: int


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


def build_nodes(java_files: list[Path]) -> dict[str, JavaNode]:
    class_names = {p.stem for p in java_files}
    nodes: dict[str, JavaNode] = {}

    for path in java_files:
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        scan_text = strip_java(remove_package_and_imports(text))
        tokens = set(IDENT_RE.findall(scan_text))
        refs = (tokens & class_names) - {path.stem}

        static_fields: list[tuple[str, str, bool]] = []
        mutable_statics = 0
        for m in JAVA_FIELD_RE.finditer(text):
            if m.group("static") is None:
                continue
            field_type = " ".join(m.group("type").split())
            field_name = m.group("name")
            is_final = m.group("final") is not None
            static_fields.append((field_type, field_name, is_final))
            if not is_final:
                mutable_statics += 1

        static_methods: list[str] = []
        for m in JAVA_METHOD_RE.finditer(text):
            if m.group("static") is None:
                continue
            method_name = m.group("name")
            if method_name == path.stem:
                continue
            static_methods.append(method_name)

        nodes[path.stem] = JavaNode(
            name=path.stem,
            path=path,
            bytes=len(data),
            lines=count_lines(data),
            refs=refs,
            static_fields=static_fields,
            static_methods=static_methods,
            mutable_static_count=mutable_statics,
        )

    return nodes


def rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def render_dossier(
    *,
    repo_root: Path,
    nodes: dict[str, JavaNode],
    name: str,
    fan_in: dict[str, int],
    callers: dict[str, list[str]],
    max_fields: int,
    max_methods: int,
    max_callers: int,
    max_refs: int,
    max_strings: int,
) -> str:
    node = nodes.get(name)
    if node is None:
        return f"## `{name}`\n\n- Missing: not found in scanned root.\n"

    text = node.path.read_text("utf-8", errors="replace")
    strings = [s[1:-1] for s in STRING_RE.findall(text)]
    string_counts = Counter(strings)
    top_strings = [s for s, _ in string_counts.most_common(max_strings) if s.strip()]

    callers_list = callers.get(name, [])
    callers_sorted = sorted(callers_list, key=lambda n: (fan_in.get(n, 0), n), reverse=True)
    refs_sorted = sorted(node.refs, key=lambda n: (fan_in.get(n, 0), n), reverse=True)

    out: list[str] = []
    out += [
        f"## `{name}`",
        "",
        f"- Path: `{rel(repo_root, node.path)}`",
        f"- Size: {node.bytes} bytes, {node.lines} lines",
        f"- Graph: fan_in={fan_in.get(name, 0)}, fan_out={len(node.refs)}",
        f"- Statics: fields={len(node.static_fields)} (mutable={node.mutable_static_count}), methods={len(node.static_methods)}",
        "",
    ]

    if node.static_fields:
        out.append("**Static Fields**")
        for t, n, is_final in node.static_fields[:max_fields]:
            suffix = " final" if is_final else ""
            out.append(f"- `{t} {n}`{suffix}")
        if len(node.static_fields) > max_fields:
            out.append(f"- … ({len(node.static_fields) - max_fields} more)")
        out.append("")

    if node.static_methods:
        out.append("**Static Methods**")
        for m in node.static_methods[:max_methods]:
            out.append(f"- `{m}(…)`")
        if len(node.static_methods) > max_methods:
            out.append(f"- … ({len(node.static_methods) - max_methods} more)")
        out.append("")

    if refs_sorted:
        out.append("**References (fan-out)**")
        for r in refs_sorted[:max_refs]:
            out.append(f"- `{r}`")
        if len(refs_sorted) > max_refs:
            out.append(f"- … ({len(refs_sorted) - max_refs} more)")
        out.append("")

    if callers_sorted:
        out.append("**Top Callers (fan-in)**")
        for c in callers_sorted[:max_callers]:
            out.append(f"- `{c}`")
        if len(callers_sorted) > max_callers:
            out.append(f"- … ({len(callers_sorted) - max_callers} more)")
        out.append("")

    if top_strings:
        out.append("**String Signals**")
        for s in top_strings:
            s2 = s.replace("`", "\\`")
            if len(s2) > 80:
                s2 = s2[:77] + "…"
            out.append(f"- `{s2}`")
        out.append("")

    return "\n".join(out)


def render_markdown(
    *,
    repo_root: Path,
    nodes: dict[str, JavaNode],
    names: list[str],
    top_fanin: int,
    exclude: set[str],
) -> str:
    fan_in: dict[str, int] = defaultdict(int)
    callers: dict[str, list[str]] = defaultdict(list)
    for src, node in nodes.items():
        for dst in node.refs:
            fan_in[dst] += 1
            callers[dst].append(src)

    generated = dt.date.today().isoformat()
    out: list[str] = []
    out += [
        "# Rename dossiers (heuristic, void-client)",
        "",
        f"Generated: {generated}",
        "",
        "This file is intended to help pick safe renames/splits for `Static###`/`Class###`-style classes.",
        "",
        "## Notes",
        "",
        "- Graph is heuristic identifier-matching (not a full Java parser).",
        "- String signals are raw string literals seen in the file (may include noise).",
        "",
    ]

    chosen = names
    if not chosen:
        candidates: list[tuple[int, int, str]] = []
        for name, node in nodes.items():
            if name in exclude:
                continue
            if not is_unrenamed_basename(name):
                continue
            if len(name) <= 2:
                continue
            candidates.append((fan_in.get(name, 0), node.bytes, name))
        candidates.sort(reverse=True)
        chosen = [n for _, _, n in candidates[:top_fanin]]

    out += ["## Selected", ""]
    for n in chosen:
        marker = " (excluded)" if n in exclude else ""
        out.append(f"- `{n}`{marker}")
    out.append("")

    for n in chosen:
        if n in exclude:
            continue
        out.append(
            render_dossier(
                repo_root=repo_root,
                nodes=nodes,
                name=n,
                fan_in=fan_in,
                callers=callers,
                max_fields=20,
                max_methods=20,
                max_callers=15,
                max_refs=20,
                max_strings=12,
            )
        )

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate heuristic rename dossiers for void-client Java classes.")
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
        help="Write markdown output to this path (relative to repo root allowed).",
    )
    parser.add_argument(
        "--class",
        dest="classes",
        action="append",
        default=[],
        help="Class name to include (repeatable). If omitted, picks top fan-in unrenamed.",
    )
    parser.add_argument(
        "--top-fanin",
        type=int,
        default=12,
        help="How many top fan-in unrenamed classes to include when --class is omitted.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Class name to exclude (repeatable).",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    root = args.root
    if not root.is_absolute():
        root = (repo_root / root).resolve()

    java_files = gather_java_files(root)
    nodes = build_nodes(java_files)

    md = render_markdown(
        repo_root=repo_root,
        nodes=nodes,
        names=args.classes,
        top_fanin=max(1, args.top_fanin),
        exclude=set(args.exclude or []),
    )

    if args.write is None:
        print(md)
        return 0

    out_path = args.write
    if not out_path.is_absolute():
        out_path = (repo_root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    rel_path = os.path.relpath(out_path, repo_root)
    print(f"Wrote {rel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

