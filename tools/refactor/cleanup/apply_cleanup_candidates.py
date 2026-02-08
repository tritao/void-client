#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import (
    REFACTOR_CLEANUP_CANDIDATES_CSV,
    REFACTOR_DROP_MEMBERS_CSV,
    REFACTOR_SRC_DIR,
)
from tools.refactor.common.io import read_csv_dict_rows


def _normalize_file(value: str, src_dir: Path) -> str:
    raw = value.strip()
    if not raw:
        return raw
    p = Path(raw)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(src_dir.resolve()))
        except ValueError:
            return raw
    return raw


def _read_rows(path: Path) -> list[dict[str, str]]:
    _, rows = read_csv_dict_rows(path)
    return rows


def _write_drop_members(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "kind", "name", "phase", "status", "notes"])
        for row in rows:
            w.writerow(
                [
                    row.get("file", "").strip(),
                    row.get("kind", "").strip(),
                    row.get("name", "").strip(),
                    row.get("phase", "").strip() or "cleanup",
                    row.get("status", "").strip() or "approved",
                    row.get("notes", "").strip(),
                ]
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply high-confidence cleanup candidates into drop_members.csv.")
    ap.add_argument(
        "--candidates",
        type=Path,
        default=REFACTOR_CLEANUP_CANDIDATES_CSV,
    )
    ap.add_argument(
        "--drop-members",
        type=Path,
        default=REFACTOR_DROP_MEMBERS_CSV,
    )
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--confidence", default="high")
    args = ap.parse_args()
    resolve_path_args(args, ("candidates", "drop_members", "src_dir"))
    src_dir = args.src_dir

    existing = _read_rows(args.drop_members)
    existing_keys = {
        (
            _normalize_file((row.get("file") or "").strip(), src_dir),
            (row.get("kind") or "").strip(),
            (row.get("name") or "").strip(),
        )
        for row in existing
    }

    candidates = _read_rows(args.candidates)
    to_add: list[dict[str, str]] = []
    for row in candidates:
        confidence = (row.get("confidence") or "").strip().lower()
        if confidence != args.confidence.lower():
            continue
        file_value = _normalize_file((row.get("file") or "").strip(), src_dir)
        key = (
            file_value,
            (row.get("kind") or "").strip(),
            (row.get("name") or "").strip(),
        )
        if not all(key):
            continue
        if key in existing_keys:
            continue
        new_row = {
            "file": file_value,
            "kind": key[1],
            "name": key[2],
            "phase": (row.get("phase") or "cleanup").strip() or "cleanup",
            "status": "approved",
            "notes": (row.get("notes") or "Auto-imported high-confidence cleanup candidate").strip(),
        }
        to_add.append(new_row)
        existing_keys.add(key)

    out_rows = existing + to_add
    _write_drop_members(args.drop_members, out_rows)
    print(f"Applied {len(to_add)} candidates into {args.drop_members}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
