#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


try:
    from tree_sitter import Language, Node, Parser  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "\n".join(
            [
                f"Missing tree-sitter dependency: {e}",
                "",
                "Run:",
                "  make bootstrap-treesitter",
                "",
                "Then rerun this command using:",
                "  ./.venv/bin/python tools/ts_rename_identifiers.py ...",
            ]
        )
    )


JAVA_GRAMMAR_DIR = Path("tools/vendor/tree-sitter-java")

# Conservative default: only rename classic JODE-style numbered identifiers.
#
# Notes:
# - JODE commonly emits object-typed fields like `aClass45_4286`, `aClass348Array4374`,
#   `aClass318_Sub1Array4293`, `aClass190ArrayArray3335`, etc.
# - It also emits primitive multi-dimensional arrays like `anIntArrayArray1234`.
OBF_NAME_RX = re.compile(
    r"^(?:"
    r"anInt(?:Array)*|"
    r"aByte(?:Array)*|aShort(?:Array)*|aLong(?:Array)*|aChar(?:Array)*|"
    r"aBoolean(?:Array)*|aFloat(?:Array)*|aDouble(?:Array)*|aString(?:Array)*|"
    r"anObject(?:Array)*|"
    r"aD_?|"
    r"aPlayer(?:Array)*_?|"
    r"aClass\d+(?:_Sub\d+)*(?:Array)*_?|"
    r"aBigInteger"
    r")\d+$"
)

JAVA_IDENT_RX = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclasses.dataclass(frozen=True)
class Mapping:
    old: str
    new: str
    file: str = ""
    notes: str = ""


@dataclasses.dataclass
class Report:
    renamed_files: dict[str, int]
    renamed_total: int
    skipped: list[str]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rename report (identifiers via tree-sitter)")
        lines.append("")
        lines.append(f"Edited files: {len(self.renamed_files)}  ")
        lines.append(f"Renamed identifiers: {self.renamed_total}")
        lines.append("")
        lines.append("## Files")
        lines.append("")
        lines.append("| file | edits |")
        lines.append("|---|---:|")
        for f, n in sorted(self.renamed_files.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{f}` | {n} |")
        lines.append("")
        if self.skipped:
            lines.append("## Skipped")
            lines.append("")
            for s in self.skipped:
                lines.append(f"- {s}")
            lines.append("")
        return "\n".join(lines)


def _non_comment_csv_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        out.append(raw)
    return out


def _load_mappings_from_csv(path: Path, *, label: str) -> list[Mapping]:
    raw_lines = _non_comment_csv_lines(path.read_text(encoding="utf-8", errors="replace"))
    if not raw_lines:
        return []

    reader = csv.DictReader(raw_lines)
    if not reader.fieldnames:
        return []
    if "old" not in reader.fieldnames or "new" not in reader.fieldnames:
        raise SystemExit(f"{label} must have header containing at least: old,new (got: {reader.fieldnames})")

    out: list[Mapping] = []
    for row in reader:
        old = (row.get("old") or "").strip()
        new = (row.get("new") or "").strip()
        if not old or not new:
            continue
        out.append(
            Mapping(
                old=old,
                new=new,
                file=(row.get("file") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
        )
    return out


def load_mappings(*, csv_files: list[Path], csv_dir: Path | None) -> list[Mapping]:
    out: list[Mapping] = []
    for p in csv_files:
        if p.exists():
            out.extend(_load_mappings_from_csv(p, label=str(p)))

    if csv_dir and csv_dir.exists():
        for p in sorted(csv_dir.rglob("*.csv")):
            out.extend(_load_mappings_from_csv(p, label=str(p)))
    return out


def _build_java_language(*, out_so: Path) -> Any:
    out_so.parent.mkdir(parents=True, exist_ok=True)
    if not JAVA_GRAMMAR_DIR.exists():
        raise SystemExit(f"Missing vendored Java grammar at {JAVA_GRAMMAR_DIR}")
    if not (JAVA_GRAMMAR_DIR / "src" / "parser.c").exists():
        raise SystemExit(f"Vendored grammar missing generated parser.c: {JAVA_GRAMMAR_DIR}/src/parser.c")
    # Build once per output path.
    if not out_so.exists():
        Language.build_library(str(out_so), [str(JAVA_GRAMMAR_DIR)])
    return Language(str(out_so), "java")


def _iter_nodes(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        # Reverse so traversal is stable-ish.
        for ch in reversed(n.children):
            stack.append(ch)


def _apply_edits_bytes(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    # Apply back-to-front.
    for start, end, repl in sorted(edits, key=lambda t: (t[0], t[1]), reverse=True):
        data = data[:start] + repl + data[end:]
    return data


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, action="append", default=[Path("client/refactor/symbol_renames.csv")])
    ap.add_argument("--csv-dir", type=Path, default=Path("client/refactor/.symbol-renames"))
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"))
    ap.add_argument("--report", type=Path, default=Path("docs/rename-report-ts.md"))
    ap.add_argument("--max-mappings", type=int, default=25, help="Apply at most N mapping rows (default 25). Use -1 for all.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-non-obfuscated", action="store_true", help="Allow renaming identifiers not matching the default obfuscated-name regex.")
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    args = ap.parse_args(argv)

    root = Path.cwd()
    src_dir = args.src_dir.resolve()
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")

    mappings = load_mappings(csv_files=[Path(p) for p in (args.csv or [])], csv_dir=args.csv_dir)
    if not mappings:
        raise SystemExit("No mappings found (expected --csv files and/or --csv-dir).")
    if args.max_mappings >= 0:
        mappings = mappings[: args.max_mappings]

    # Validate + build map.
    rename_map: dict[str, str] = {}
    skipped: list[str] = []
    for m in mappings:
        if not JAVA_IDENT_RX.match(m.old) or not JAVA_IDENT_RX.match(m.new):
            skipped.append(f"Invalid java identifier: {m.old!r} -> {m.new!r}")
            continue
        if not args.allow_non_obfuscated and not OBF_NAME_RX.match(m.old):
            skipped.append(f"Not obfuscated (use --allow-non-obfuscated): {m.old} -> {m.new}")
            continue
        prev = rename_map.get(m.old)
        if prev and prev != m.new:
            raise SystemExit(f"Conflicting mappings for {m.old}: {prev} vs {m.new}")
        rename_map[m.old] = m.new

    if not rename_map:
        raise SystemExit("No applicable mappings after filtering.")

    java = _build_java_language(out_so=args.language_so)
    parser = Parser()
    parser.set_language(java)

    renamed_files: dict[str, int] = {}
    renamed_total = 0

    java_files = sorted(src_dir.rglob("*.java"))
    for path in java_files:
        data = path.read_bytes()
        tree = parser.parse(data)
        edits: list[tuple[int, int, bytes]] = []
        for n in _iter_nodes(tree.root_node):
            # In decompiled sources, parenthesized expressions like `(x.y)` can be
            # mis-parsed by the grammar as a cast-expression, yielding
            # `type_identifier` tokens instead of `identifier`. Renaming both keeps
            # the transform robust.
            if n.type not in ("identifier", "type_identifier"):
                continue
            tok = data[n.start_byte : n.end_byte]
            try:
                s = tok.decode("utf-8")
            except UnicodeDecodeError:
                continue
            new = rename_map.get(s)
            if not new or new == s:
                continue
            edits.append((n.start_byte, n.end_byte, new.encode("utf-8")))

        if not edits:
            continue
        out = _apply_edits_bytes(data, edits)
        if out != data:
            rel = str(path.relative_to(root))
            renamed_files[rel] = len(edits)
            renamed_total += len(edits)
            if not args.dry_run:
                path.write_bytes(out)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        Report(renamed_files=renamed_files, renamed_total=renamed_total, skipped=skipped).to_markdown(),
        encoding="utf-8",
    )
    print(f"Renamed {renamed_total} identifiers across {len(renamed_files)} files. Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
