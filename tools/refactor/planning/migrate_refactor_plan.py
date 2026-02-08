#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


SCHEMA = [
    "id",
    "module",
    "action",
    "kind",
    "file",
    "owner",
    "member",
    "signature",
    "param_index",
    "old",
    "new",
    "target_class",
    "target_file",
    "scope",
    "phase",
    "confidence",
    "status",
    "notes",
]

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

EXTRACT_HEADER = [
    "file",
    "kind",
    "old",
    "target_class",
    "target_file",
    "scope",
    "phase",
    "confidence",
    "status",
    "notes",
]


def _ensure_action_file(plan_dir: Path, module: str, action: str, header: list[str]) -> Path:
    path = plan_dir / f"{module}.{action}.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
    return path


def _append_rows(path: Path, rows: list[dict[str, str]], header: list[str]) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})


def _module_from_symbol_path(path: Path, symbol_root: Path) -> str:
    rel = path.relative_to(symbol_root)
    if len(rel.parts) == 1:
        return "global"
    return rel.parts[0]


def _read_symbol_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader((line for line in handle if line.strip() and not line.lstrip().startswith("#")))
        out = []
        for row in reader:
            old = (row.get("old") or "").strip()
            new = (row.get("new") or "").strip()
            if not old or not new:
                continue
            out.append({k: (row.get(k, "") or "").strip() for k in reader.fieldnames or []})
        return out


def migrate_symbols(plan_dir: Path, symbols_root_csv: Path, symbols_dir: Path) -> int:
    count = 0
    sources: list[Path] = []
    if symbols_root_csv.exists():
        sources.append(symbols_root_csv)
    if symbols_dir.exists():
        sources.extend(sorted(symbols_dir.rglob("*.csv")))
    for source in sources:
        module = _module_from_symbol_path(source, symbols_dir) if source != symbols_root_csv else "global"
        rows = _read_symbol_csv(source)
        output_rows: list[dict[str, str]] = []
        for row in rows:
            output_rows.append(
                {
                    "kind": row.get("kind", ""),
                    "file": row.get("file", ""),
                    "owner": row.get("owner", ""),
                    "member": row.get("member", ""),
                    "signature": row.get("signature", ""),
                    "param_index": row.get("param_index", ""),
                    "old": row.get("old", ""),
                    "new": row.get("new", ""),
                    "scope": row.get("file", ""),
                    "phase": "module",
                    "confidence": "high",
                    "status": "approved",
                    "notes": row.get("notes", ""),
                }
            )
        if output_rows:
            _append_rows(_ensure_action_file(plan_dir, module, "rename", RENAME_HEADER), output_rows, RENAME_HEADER)
            count += len(output_rows)
    return count


def migrate_extracts(plan_dir: Path, extract_dir: Path) -> int:
    count = 0
    for yaml in sorted(extract_dir.glob("*.yaml")):
        text = yaml.read_text(encoding="utf-8", errors="replace")
        source = ""
        target_file = ""
        target_class = ""
        moves: list[tuple[str, str]] = []
        kind = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("source:"):
                source = line.split(":", 1)[1].strip()
            elif line.startswith("target_file:"):
                target_file = line.split(":", 1)[1].strip()
            elif line.startswith("target_class:"):
                target_class = line.split(":", 1)[1].strip()
            elif line.startswith("- kind:"):
                kind = line.split(":", 1)[1].strip()
            elif line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
                if kind and name:
                    moves.append((kind, name))
                    kind = ""
        if not source or not target_file or not target_class:
            continue
        module = Path(source).parts[0] if len(Path(source).parts) > 1 else "global"
        output_rows: list[dict[str, str]] = []
        for move_kind, move_name in moves:
            output_rows.append(
                {
                    "file": source,
                    "kind": move_kind,
                    "old": move_name,
                    "target_class": target_class,
                    "target_file": target_file,
                    "scope": source,
                    "phase": "split",
                    "confidence": "high",
                    "status": "approved",
                    "notes": f"migrated from {yaml.name}",
                }
            )
        if output_rows:
            _append_rows(_ensure_action_file(plan_dir, module, "extract", EXTRACT_HEADER), output_rows, EXTRACT_HEADER)
            count += len(output_rows)
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate existing symbol/extract artifacts into canonical .refactor-plan CSVs.")
    ap.add_argument("--plan-dir", type=Path, default=Path("client/refactor/.refactor-plan"))
    ap.add_argument("--symbols-root-csv", type=Path, default=Path("client/refactor/.refactor-plan/import/symbol_renames.csv"))
    ap.add_argument("--symbols-dir", type=Path, default=Path("client/refactor/.refactor-plan/import/symbol-renames"))
    ap.add_argument("--extract-dir", type=Path, default=Path("client/refactor/.refactor-plan/import/extract-statics"))
    ap.add_argument("--clean", action="store_true", help="Delete existing module CSVs before migration (keeps _* files).")
    args = ap.parse_args()

    args.plan_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for csv_path in args.plan_dir.glob("*.csv"):
            if csv_path.name.startswith("_"):
                continue
            if csv_path.name == "layout_rules.csv":
                continue
            csv_path.unlink()

    symbol_rows = migrate_symbols(args.plan_dir, args.symbols_root_csv, args.symbols_dir)
    extract_rows = migrate_extracts(args.plan_dir, args.extract_dir)
    print(f"Migrated rows: symbols={symbol_rows}, extract={extract_rows} into {args.plan_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
