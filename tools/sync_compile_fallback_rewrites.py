#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path


ERROR_RX = re.compile(r"^(?P<file>.+?):\d+:\s+error:\s+cannot find symbol$")
SYMBOL_RX = re.compile(r"^\s*symbol:\s+(?P<kind>method|variable)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)")
LOCATION_TYPE_RX = re.compile(r"^\s*location:\s+variable\s+\S+\s+of type\s+(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)")
LOCATION_CLASS_RX = re.compile(r"^\s*location:\s+class\s+(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)")


@dataclass(frozen=True)
class CompileError:
    file: str
    kind: str
    name: str
    owner: str


@dataclass(frozen=True)
class SymbolRename:
    owner: str
    kind: str
    old: str
    new: str


def _parse_compile_errors(path: Path, src_dir: Path) -> list[CompileError]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[CompileError] = []
    i = 0
    while i < len(lines):
        m = ERROR_RX.match(lines[i])
        if not m:
            i += 1
            continue
        raw_file = m.group("file")
        file_path = raw_file
        if raw_file.startswith(str(src_dir) + "/"):
            file_path = raw_file[len(str(src_dir)) + 1 :]
        symbol_kind = ""
        symbol_name = ""
        owner = ""
        j = i + 1
        while j < len(lines) and j <= i + 8:
            sm = SYMBOL_RX.match(lines[j])
            if sm:
                symbol_kind = "method" if sm.group("kind") == "method" else "field"
                symbol_name = sm.group("name")
            lm = LOCATION_TYPE_RX.match(lines[j]) or LOCATION_CLASS_RX.match(lines[j])
            if lm:
                owner = lm.group("owner")
            if symbol_kind and symbol_name and owner:
                break
            j += 1
        if symbol_kind and symbol_name and owner:
            out.append(CompileError(file=file_path, kind=symbol_kind, name=symbol_name, owner=owner))
        i = j + 1
    return out


def _load_symbol_renames(path: Path) -> dict[tuple[str, str, str], SymbolRename]:
    rows = csv.DictReader(path.read_text(encoding="utf-8", errors="replace").splitlines())
    out: dict[tuple[str, str, str], SymbolRename] = {}
    for row in rows:
        owner = (row.get("owner") or "").strip()
        kind = (row.get("kind") or "").strip().lower()
        old = (row.get("old") or "").strip()
        new = (row.get("new") or "").strip()
        if not (owner and old and new):
            continue
        if kind not in {"method", "field"}:
            continue
        out[(owner, kind, old)] = SymbolRename(owner=owner, kind=kind, old=old, new=new)
    return out


def _load_existing_rewrites(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    rows = csv.DictReader(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return {
        ((row.get("file") or "").strip(), (row.get("old") or "").strip(), (row.get("new") or "").strip())
        for row in rows
        if (row.get("file") or "").strip() and (row.get("old") or "").strip() and (row.get("new") or "").strip()
    }


def _append_rows(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not has_header:
            writer.writerow(["file", "old", "new", "phase", "status", "notes"])
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Add safe file-scoped fallback identifier rewrites from compile errors.")
    ap.add_argument("--compile-log", type=Path, default=Path("build/compile-refactor.log"))
    ap.add_argument(
        "--symbol-renames",
        type=Path,
        default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("client/refactor/.refactor-plan/file_identifier_rewrites.csv"),
    )
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    args = ap.parse_args()

    errors = _parse_compile_errors(args.compile_log.resolve(), args.src_dir.resolve())
    symbol_map = _load_symbol_renames(args.symbol_renames.resolve())
    existing = _load_existing_rewrites(args.out.resolve())

    added_rows: list[list[str]] = []
    unresolved = 0
    seen_new: set[tuple[str, str, str]] = set()
    for err in errors:
        symbol = symbol_map.get((err.owner, err.kind, err.name))
        if symbol is None:
            unresolved += 1
            continue
        key = (err.file, symbol.old, symbol.new)
        if key in existing or key in seen_new:
            continue
        seen_new.add(key)
        added_rows.append(
            [
                err.file,
                symbol.old,
                symbol.new,
                "cleanup",
                "approved",
                f"Auto fallback from compile log: {err.owner}.{symbol.old}->{symbol.new}",
            ]
        )

    if added_rows:
        _append_rows(args.out.resolve(), added_rows)

    print(
        f"Added {len(added_rows)} fallback rewrites to {args.out}. "
        f"errors={len(errors)} unresolved={unresolved}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
