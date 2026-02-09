#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import REFACTOR_CLEANUP_PLAN_DIR
from tools.refactor.common.io import read_csv_dict_rows, write_csv_dict_rows


def _row_matches(row: dict[str, str], row_key: dict[str, str]) -> bool:
    for key, value in row_key.items():
        if (row.get(key) or "").strip() != (value or "").strip():
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune generated cleanup-plan rows known to be no-op.")
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_CLEANUP_PLAN_DIR)
    ap.add_argument("--summary-json", type=Path, default=Path("build/refactor-state/cleanup-apply-summary.json"))
    ap.add_argument("--out-summary-json", type=Path, default=Path("build/refactor-state/cleanup-prune-summary.json"))
    ap.add_argument("--reason", default="no_match", help="Only prune unmatched examples with this reason.")
    args = ap.parse_args()
    resolve_path_args(args, ("plan_dir", "summary_json", "out_summary_json"))

    if not args.summary_json.exists():
        print(f"Prune cleanup plan skipped: summary not found: {args.summary_json}")
        return 0

    payload = json.loads(args.summary_json.read_text(encoding="utf-8"))
    unmatched = payload.get("unmatched_rows") or payload.get("unmatched_examples", [])
    by_plan_file: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in unmatched:
        if (item.get("reason") or "").strip() != args.reason:
            continue
        plan_file = (item.get("plan_file") or "").strip()
        row_key = item.get("row_key") or {}
        if not plan_file or not isinstance(row_key, dict) or not row_key:
            continue
        by_plan_file[plan_file].append({str(k): str(v) for k, v in row_key.items()})

    files_scanned = 0
    rows_before = 0
    rows_after = 0
    rows_removed = 0
    removed_by_file: dict[str, int] = {}
    for plan_file, row_keys in sorted(by_plan_file.items()):
        csv_path = args.plan_dir / plan_file
        header, rows = read_csv_dict_rows(csv_path)
        if not header:
            continue
        files_scanned += 1
        rows_before += len(rows)
        kept: list[dict[str, str]] = []
        removed = 0
        for row in rows:
            should_drop = any(_row_matches(row, row_key) for row_key in row_keys)
            if should_drop:
                removed += 1
            else:
                kept.append(row)
        if removed > 0:
            write_csv_dict_rows(csv_path, header, kept)
        rows_after += len(kept)
        rows_removed += removed
        removed_by_file[plan_file] = removed

    summary = {
        "reason": args.reason,
        "files_scanned": files_scanned,
        "rows_before": rows_before,
        "rows_after": rows_after,
        "rows_removed": rows_removed,
        "removed_by_file": removed_by_file,
        "plan_dir": str(args.plan_dir),
        "summary_json": str(args.summary_json),
    }
    args.out_summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "Pruned generated cleanup plan. "
        f"files_scanned={files_scanned} rows_removed={rows_removed} out_summary={args.out_summary_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
