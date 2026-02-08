#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from tools.reporting import fan_graph


CONF_ORDER = {"": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class Row:
    raw: dict[str, str]
    order: int

    @property
    def kind(self) -> str:
        return (self.raw.get("kind") or "").strip()

    @property
    def file(self) -> str:
        return (self.raw.get("file") or "").strip()

    @property
    def old(self) -> str:
        return (self.raw.get("old") or "").strip()

    @property
    def new(self) -> str:
        return (self.raw.get("new") or "").strip()

    @property
    def module(self) -> str:
        return (self.raw.get("module") or "").strip()

    @property
    def phase(self) -> str:
        return (self.raw.get("phase") or "").strip()

    @property
    def confidence(self) -> str:
        return (self.raw.get("confidence") or "").strip()

    @property
    def status(self) -> str:
        return (self.raw.get("status") or "").strip()


def _iter_java_files(src_dir: Path) -> list[Path]:
    return sorted(src_dir.rglob("*.java"))


def _build_stem_index(java_files: list[Path]) -> dict[str, list[Path]]:
    stem_index: dict[str, list[Path]] = {}
    for p in java_files:
        stem_index.setdefault(p.stem, []).append(p)
    return stem_index


def _resolve_scope_files(src_dir: Path, java_files: list[Path], stem_index: dict[str, list[Path]], file_value: str) -> list[Path]:
    if not file_value:
        return []
    value = file_value.strip()
    if "/" in value or value.endswith(".java"):
        p = Path(value)
        if p.suffix != ".java":
            p = p.with_suffix(".java")
        abs_path = (src_dir / p).resolve()
        if abs_path.exists():
            return [abs_path]
        # If it was already absolute, accept it.
        if p.is_absolute() and p.exists():
            return [p.resolve()]
        return []
    stem = value[:-5] if value.endswith(".java") else value
    return [p.resolve() for p in stem_index.get(stem, [])]


def _strip_java(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = fan_graph.remove_package_and_imports(text)
    return fan_graph.strip_java(text)


def _is_effective(row: Row, *, src_dir: Path, java_files: list[Path], stem_index: dict[str, list[Path]], cache: dict[Path, str]) -> bool:
    if not row.old or not row.new:
        return False
    scope_files = _resolve_scope_files(src_dir, java_files, stem_index, row.file)
    if not scope_files:
        # We intentionally avoid global scans by default.
        return False

    kind = row.kind.lower().strip()
    if kind == "method":
        rx_old = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(row.old)}\s*\(")
        rx_new = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(row.new)}\s*\(")
    else:
        rx_old = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(row.old)}(?![A-Za-z0-9_$])")
        rx_new = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(row.new)}(?![A-Za-z0-9_$])")

    for p in scope_files:
        stripped = cache.get(p)
        if stripped is None:
            stripped = _strip_java(p)
            cache[p] = stripped
        if rx_old.search(stripped):
            return True
        # If it already looks fully renamed, treat as ineffective.
        if rx_new.search(stripped):
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Select next N effective rename mappings (skip no-ops).")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--in-csv", type=Path, required=True)
    ap.add_argument("--out-csv", type=Path, required=True)
    ap.add_argument("--max", type=int, default=80, help="Max effective mapping rows to emit.")
    ap.add_argument("--module", default="", help="If set, require row.module to match.")
    ap.add_argument("--phase", action="append", default=[], help="Allowed phase (repeatable). Empty means allow all.")
    ap.add_argument("--status", action="append", default=["approved"], help="Allowed status (repeatable).")
    ap.add_argument("--min-confidence", default="high", choices=["low", "medium", "high"])
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    java_files = _iter_java_files(src_dir)
    stem_index = _build_stem_index(java_files)

    with args.in_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit(f"Missing header: {args.in_csv}")
        header = list(reader.fieldnames)
        rows = [Row(raw={k: (v or "").strip() for k, v in r.items()}, order=i) for i, r in enumerate(reader, start=1)]

    allowed_phase = {p.strip() for p in (args.phase or []) if p.strip()}
    allowed_status = {s.strip() for s in (args.status or []) if s.strip()}
    min_conf = CONF_ORDER.get(args.min_confidence.strip().lower(), 0)

    selected: list[Row] = []
    cache: dict[Path, str] = {}

    def _ok(r: Row) -> bool:
        if args.module and r.module and r.module != args.module:
            return False
        if allowed_phase and r.phase and r.phase not in allowed_phase:
            return False
        if allowed_status and r.status and r.status not in allowed_status:
            return False
        if CONF_ORDER.get(r.confidence.lower(), 0) < min_conf:
            return False
        return True

    for r in rows:
        if len(selected) >= args.max:
            break
        if not _ok(r):
            continue
        if _is_effective(r, src_dir=src_dir, java_files=java_files, stem_index=stem_index, cache=cache):
            selected.append(r)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for r in selected:
            writer.writerow(r.raw)

    print(f"Selected {len(selected)} effective mappings (requested {args.max}). Wrote {args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
