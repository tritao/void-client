#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import (
    REFACTOR_CLEANUP_CANDIDATES_CSV,
    REFACTOR_CLEANUP_CANDIDATES_MD,
    REFACTOR_SRC_DIR,
)


FIELD_DECL_RX = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|transient|volatile)\s+)*"
    r"(?P<type>[\w<>\[\], ?$.]+?)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:=\s*[^;]*)?;\s*$"
)
OBF_NAME_RX = re.compile(
    r"^(?:"
    r"anInt(?:Array)*|anLong(?:Array)*|"
    r"aByte(?:Array)*|aShort(?:Array)*|aLong(?:Array)*|aChar(?:Array)*|"
    r"aBoolean(?:Array)*|aFloat(?:Array)*|aDouble(?:Array)*|aString(?:Array)*|"
    r"anObject(?:Array)*|"
    r"aClass\d+(?:_Sub\d+)*(?:Array)*_?"
    r")\d+$"
)
INCREMENT_RX_TEMPLATE = r"(?:\+\+\s*{n}\b|\b{n}\s*\+\+|--\s*{n}\b|\b{n}\s*--|\b{n}\s*[\+\-]=\s*1\b)"
ASSIGN_NULL_RX_TEMPLATE = r"\b{n}\s*=\s*null\s*;"
READ_LIKE_RX_TEMPLATE = r"\b{n}\b(?!\s*(?:\+\+|--|[\+\-]=\s*1|=\s*null\b|=))"
TOKEN_RX = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b")


@dataclass(frozen=True)
class FieldDecl:
    file: Path
    line_no: int
    name: str
    type_name: str
    line_text: str


@dataclass(frozen=True)
class Candidate:
    file: str
    kind: str
    name: str
    phase: str
    status: str
    confidence: str
    category: str
    notes: str


def _to_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _iter_java_files(src_dir: Path) -> list[Path]:
    return sorted(src_dir.rglob("*.java"))


def _collect_field_decls(path: Path) -> list[FieldDecl]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[FieldDecl] = []
    depth = 0
    for idx, line in enumerate(lines, start=1):
        cur_depth = depth
        if cur_depth == 1:
            m = FIELD_DECL_RX.match(line)
            if m and " static " in f" {line} ":
                out.append(
                    FieldDecl(
                        file=path,
                        line_no=idx,
                        name=m.group("name"),
                        type_name=m.group("type").strip(),
                        line_text=line.strip(),
                    )
                )
        depth += line.count("{") - line.count("}")
    return out


def _find_occurrences(lines: list[str], name: str) -> list[tuple[int, str]]:
    rx = re.compile(rf"\b{re.escape(name)}\b")
    return [(idx, line) for idx, line in enumerate(lines, start=1) if rx.search(line)]


def _is_counter_type(type_name: str) -> bool:
    base = type_name.replace("final", "").replace("static", "").strip()
    return base in {"int", "long"}


def _make_field_drop(decl: FieldDecl, note: str, src_dir: Path) -> Candidate:
    return Candidate(
        file=_to_rel(decl.file, src_dir),
        kind="field",
        name=decl.name,
        phase="cleanup",
        status="proposed",
        confidence="high",
        category="field-drop",
        notes=note,
    )


def _make_line_drop(file_path: Path, needle: str, note: str, src_dir: Path) -> Candidate:
    return Candidate(
        file=_to_rel(file_path, src_dir),
        kind="line_contains",
        name=needle,
        phase="cleanup",
        status="proposed",
        confidence="high",
        category="line-drop",
        notes=note,
    )


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = (c.file, c.kind, c.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _detect_candidates(src_dir: Path) -> list[Candidate]:
    java_files = _iter_java_files(src_dir)
    file_lines: dict[Path, list[str]] = {
        p: p.read_text(encoding="utf-8", errors="replace").splitlines() for p in java_files
    }
    candidates: list[Candidate] = []
    all_decls: list[FieldDecl] = []
    for p in java_files:
        all_decls.extend(_collect_field_decls(p))

    candidate_names = {d.name for d in all_decls}
    global_counts: dict[str, int] = {name: 0 for name in candidate_names}
    for lines in file_lines.values():
        for line in lines:
            for tok in set(TOKEN_RX.findall(line)):
                if tok in global_counts:
                    global_counts[tok] += 1

    for decl in all_decls:
        lines = file_lines[decl.file]
        occ = _find_occurrences(lines, decl.name)
        non_decl = [(ln, txt) for ln, txt in occ if ln != decl.line_no]
        global_count = global_counts.get(decl.name, 0)

        if not non_decl and global_count == 1:
            if OBF_NAME_RX.match(decl.name) or _is_counter_type(decl.type_name):
                candidates.append(_make_field_drop(decl, "No references outside declaration", src_dir))
            continue

        if not _is_counter_type(decl.type_name):
            continue
        if global_count != len(occ):
            continue

        inc_rx = re.compile(INCREMENT_RX_TEMPLATE.format(n=re.escape(decl.name)))
        null_rx = re.compile(ASSIGN_NULL_RX_TEMPLATE.format(n=re.escape(decl.name)))
        read_like_rx = re.compile(READ_LIKE_RX_TEMPLATE.format(n=re.escape(decl.name)))

        line_needles: set[str] = set()
        has_read_like = False
        for _, text in non_decl:
            if read_like_rx.search(text):
                has_read_like = True
                break
            if inc_rx.search(text):
                line_needles.add(f"{decl.name}++")
                continue
            if null_rx.search(text):
                line_needles.add(f"{decl.name} = null;")
                continue
            # Unknown write pattern; keep conservative.
            has_read_like = True
            break
        if has_read_like:
            continue
        if not line_needles:
            continue

        candidates.append(
            _make_field_drop(
                decl,
                "Counter/temporary field only used by removable side-effect lines",
                src_dir,
            )
        )
        for needle in sorted(line_needles):
            candidates.append(
                _make_line_drop(
                    decl.file,
                    needle,
                    f"Remove orphan line after dropping `{decl.name}`",
                    src_dir,
                )
            )

    return _dedupe(candidates)


def _write_csv(path: Path, rows: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "kind", "name", "phase", "status", "confidence", "category", "notes"])
        for r in rows:
            w.writerow([r.file, r.kind, r.name, r.phase, r.status, r.confidence, r.category, r.notes])


def _write_md(path: Path, rows: list[Candidate], src_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_file: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for row in rows:
        by_file[row.file] = by_file.get(row.file, 0) + 1
        by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
    lines: list[str] = []
    lines.append("# Cleanup Candidates")
    lines.append("")
    lines.append(f"- Source root: `{src_dir}`")
    lines.append(f"- Candidate rows: `{len(rows)}`")
    lines.append("")
    lines.append("## By kind")
    lines.append("")
    for kind, count in sorted(by_kind.items()):
        lines.append(f"- `{kind}`: {count}")
    lines.append("")
    lines.append("## Top files")
    lines.append("")
    for file, count in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
        lines.append(f"- `{file}`: {count}")
    lines.append("")
    lines.append("## Rows")
    lines.append("")
    lines.append("| file | kind | name | confidence | category |")
    lines.append("|---|---|---|---|---|")
    for row in rows:
        lines.append(f"| `{row.file}` | `{row.kind}` | `{row.name}` | `{row.confidence}` | `{row.category}` |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect high-confidence cleanup candidates in refactor tree.")
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument(
        "--out-csv",
        type=Path,
        default=REFACTOR_CLEANUP_CANDIDATES_CSV,
    )
    ap.add_argument("--out-md", type=Path, default=REFACTOR_CLEANUP_CANDIDATES_MD)
    args = ap.parse_args()
    resolve_path_args(args, ("src_dir", "out_csv", "out_md"))

    src_dir = args.src_dir
    rows = _detect_candidates(src_dir)
    _write_csv(args.out_csv, rows)
    _write_md(args.out_md, rows, src_dir)
    print(f"Detected {len(rows)} cleanup candidates. CSV: {args.out_csv} MD: {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
