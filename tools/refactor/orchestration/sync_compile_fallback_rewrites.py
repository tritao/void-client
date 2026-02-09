#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.common.ts_java import build_java_parser, iter_nodes


ERROR_RX = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\s+error:\s+cannot find symbol$")
SYMBOL_RX = re.compile(r"^\s*symbol:\s+(?P<kind>method|variable)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)")
LOCATION_TYPE_RX = re.compile(r"^\s*location:\s+variable\s+\S+\s+of type\s+(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)")
LOCATION_CLASS_RX = re.compile(r"^\s*location:\s+class\s+(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)")


@dataclass(frozen=True)
class CompileError:
    file: str
    line: int
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
        line_no = int(m.group("line"))
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
            out.append(CompileError(file=file_path, line=line_no, kind=symbol_kind, name=symbol_name, owner=owner))
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


RENAME_HEADER = [
    "kind",
    "file",
    "owner",
    "member",
    "signature",
    "param_index",
    "old",
    "new",
    "scope",
    "phase",
    "confidence",
    "status",
    "notes",
]


def _module_for_file(file_rel: str) -> str:
    parts = Path(file_rel).parts
    return parts[0] if parts else "client"


def _load_existing_rewrites(plan_dir: Path) -> set[tuple[str, str, str, str, str]]:
    existing: set[tuple[str, str, str, str, str]] = set()
    for pattern in ("*.rename.csv", "*.scoped_fallback.csv"):
        for path in sorted(plan_dir.glob(pattern)):
            rows = csv.DictReader(path.read_text(encoding="utf-8", errors="replace").splitlines())
            for row in rows:
                file_value = (row.get("file") or "").strip()
                kind = (row.get("kind") or "").strip()
                member = (row.get("member") or "").strip()
                old = (row.get("old") or "").strip()
                new = (row.get("new") or "").strip()
                if file_value and old and new:
                    existing.add((file_value, kind, member, old, new))
    return existing


def _append_rows(plan_dir: Path, rows_by_module: dict[str, list[dict[str, str]]]) -> None:
    plan_dir.mkdir(parents=True, exist_ok=True)
    for module, rows in sorted(rows_by_module.items()):
        path = plan_dir / f"{module}.scoped_fallback.csv"
        existing_rows: list[dict[str, str]] = []
        header = RENAME_HEADER
        if path.exists() and path.stat().st_size > 0:
            reader = csv.DictReader(path.read_text(encoding="utf-8", errors="replace").splitlines())
            if reader.fieldnames:
                header = list(reader.fieldnames)
            existing_rows = [{k: (r.get(k) or "") for k in header} for r in reader]

        existing_rows.extend([{k: (row.get(k) or "") for k in header} for row in rows])

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(existing_rows)


def _build_method_lookup(src_dir: Path) -> tuple[object, dict[Path, list[tuple[int, int, str]]]]:
    parser = build_java_parser(out_so=Path("build/ts-languages-java.so"))
    cache: dict[Path, list[tuple[int, int, str]]] = {}
    return parser, cache


def _method_for_line(
    *,
    parser: object,
    cache: dict[Path, list[tuple[int, int, str]]],
    src_dir: Path,
    rel_file: str,
    line_no: int,
) -> str:
    rel_path = Path(rel_file)
    abs_path = (src_dir / rel_path).resolve()
    cached = cache.get(abs_path)
    if cached is None:
        try:
            data = abs_path.read_bytes()
        except OSError:
            cache[abs_path] = []
            return ""
        tree = parser.parse(data)
        spans: list[tuple[int, int, str]] = []
        for node in iter_nodes(tree.root_node):
            if node.type != "method_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = data[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace").strip()
            if not name:
                continue
            start_line = int(node.start_point[0]) + 1
            end_line = int(node.end_point[0]) + 1
            spans.append((start_line, end_line, name))
        cache[abs_path] = spans
        cached = spans
    for start_line, end_line, name in cached:
        if start_line <= line_no <= end_line:
            return name
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Add safe scoped fallback rename rows from compile errors into module *.scoped_fallback.csv files.")
    ap.add_argument("--compile-log", type=Path, default=Path("build/compile-refactor.log"))
    ap.add_argument(
        "--symbol-renames",
        type=Path,
        default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"),
    )
    ap.add_argument("--plan-dir", type=Path, default=Path("client/refactor/.refactor-plan"))
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    args = ap.parse_args()

    errors = _parse_compile_errors(args.compile_log.resolve(), args.src_dir.resolve())
    symbol_map = _load_symbol_renames(args.symbol_renames.resolve())
    existing = _load_existing_rewrites(args.plan_dir.resolve())
    parser, method_cache = _build_method_lookup(args.src_dir.resolve())

    added_rows: dict[str, list[dict[str, str]]] = {}
    unresolved = 0
    unresolved_member = 0
    seen_new: set[tuple[str, str, str, str, str]] = set()
    for err in errors:
        symbol = symbol_map.get((err.owner, err.kind, err.name))
        if symbol is None:
            unresolved += 1
            continue
        enclosing_method = _method_for_line(
            parser=parser,
            cache=method_cache,
            src_dir=args.src_dir.resolve(),
            rel_file=err.file,
            line_no=err.line,
        )
        if not enclosing_method:
            unresolved_member += 1
            continue
        key = (err.file, "local", enclosing_method, symbol.old, symbol.new)
        if key in existing or key in seen_new:
            continue
        seen_new.add(key)
        module = _module_for_file(err.file)
        added_rows.setdefault(module, []).append(
            {
                "kind": "local",
                "file": err.file,
                "owner": err.owner,
                "member": enclosing_method,
                "signature": "",
                "param_index": "",
                "old": symbol.old,
                "new": symbol.new,
                "scope": err.owner,
                "phase": "cleanup",
                "confidence": "high",
                "status": "approved",
                "notes": f"Auto scoped fallback from compile log at {err.file}:{err.line}: {err.owner}.{symbol.old}->{symbol.new}",
            }
        )

    if added_rows:
        _append_rows(args.plan_dir.resolve(), added_rows)

    print(
        f"Added {sum(len(v) for v in added_rows.values())} fallback rewrites to {args.plan_dir}. "
        f"errors={len(errors)} unresolved={unresolved} unresolved_member={unresolved_member}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
