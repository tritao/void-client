#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.common.constants import REFACTOR_PLAN_DIR
from tools.refactor.common.io import read_csv_dict_rows, write_csv_dict_rows


IDENTITY_COLUMNS = ("kind", "file", "owner", "member", "signature", "param_index", "old")
RENAME_DEFAULT_HEADER = [
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


@dataclass(frozen=True)
class PromoteStats:
    promoted: int = 0
    duplicates: int = 0
    conflicts: int = 0
    skipped_status: int = 0
    files_touched: int = 0


def _identity_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple((row.get(c) or "").strip() for c in IDENTITY_COLUMNS)


def _full_key(row: dict[str, str]) -> tuple[str, ...]:
    return _identity_key(row) + ((row.get("new") or "").strip(),)


def _merge_row(row: dict[str, str], header: list[str]) -> dict[str, str]:
    return {col: (row.get(col) or "").strip() for col in header}


def _module_from_fallback(path: Path) -> str:
    stem = path.stem
    suffix = ".scoped_fallback"
    if stem.endswith(suffix):
        return stem[: -len(suffix)]
    return stem.split(".")[0]


def _is_promotable_status(status: str) -> bool:
    return status in {"approved", "applied"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Promote module *.scoped_fallback.csv rows into module *.rename.csv with dedupe/conflict checks."
    )
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_PLAN_DIR)
    ap.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep promoted/duplicate rows in *.scoped_fallback.csv instead of pruning them.",
    )
    ap.add_argument(
        "--include-proposed",
        action="store_true",
        help="Also promote rows with status=proposed (default only approved/applied).",
    )
    args = ap.parse_args()

    plan_dir = args.plan_dir.resolve()
    fallback_files = sorted(plan_dir.glob("*.scoped_fallback.csv"))
    if not fallback_files:
        print(f"No scoped fallback files found under {plan_dir}")
        return 0

    stats = PromoteStats()
    promoted = duplicates = conflicts = skipped_status = files_touched = 0

    for fallback_path in fallback_files:
        module = _module_from_fallback(fallback_path)
        rename_path = plan_dir / f"{module}.rename.csv"

        rename_header, rename_rows = read_csv_dict_rows(rename_path)
        fallback_header, fallback_rows = read_csv_dict_rows(fallback_path)
        if not fallback_header:
            continue

        header = rename_header or RENAME_DEFAULT_HEADER
        rename_rows_norm = [_merge_row(row, header) for row in rename_rows]
        rename_by_identity: dict[tuple[str, ...], str] = {}
        rename_full: set[tuple[str, ...]] = set()
        for row in rename_rows_norm:
            ik = _identity_key(row)
            nv = (row.get("new") or "").strip()
            if ik and nv:
                rename_by_identity[ik] = nv
                rename_full.add(_full_key(row))

        fallback_rows_norm = [_merge_row(row, header) for row in fallback_rows]
        kept_fallback: list[dict[str, str]] = []
        added_any = False

        for row in fallback_rows_norm:
            status = (row.get("status") or "").strip().lower()
            if not args.include_proposed and not _is_promotable_status(status):
                skipped_status += 1
                kept_fallback.append(row)
                continue

            ik = _identity_key(row)
            fk = _full_key(row)
            new_name = (row.get("new") or "").strip()
            if not ik or not new_name:
                kept_fallback.append(row)
                continue

            existing_new = rename_by_identity.get(ik)
            if existing_new and existing_new != new_name:
                conflicts += 1
                kept_fallback.append(row)
                continue

            if fk in rename_full:
                duplicates += 1
                if args.keep_source:
                    kept_fallback.append(row)
                continue

            rename_rows_norm.append(row)
            rename_by_identity[ik] = new_name
            rename_full.add(fk)
            promoted += 1
            added_any = True
            if args.keep_source:
                kept_fallback.append(row)

        file_changed = False
        if added_any:
            write_csv_dict_rows(rename_path, header, rename_rows_norm)
            file_changed = True

        if not args.keep_source:
            if kept_fallback:
                write_csv_dict_rows(fallback_path, header, kept_fallback)
            else:
                fallback_path.unlink(missing_ok=True)
            file_changed = True

        if file_changed:
            files_touched += 1

    stats = PromoteStats(
        promoted=promoted,
        duplicates=duplicates,
        conflicts=conflicts,
        skipped_status=skipped_status,
        files_touched=files_touched,
    )

    print(
        "Promoted scoped fallback rows: "
        f"promoted={stats.promoted} duplicates={stats.duplicates} "
        f"conflicts={stats.conflicts} skipped_status={stats.skipped_status} "
        f"files_touched={stats.files_touched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
