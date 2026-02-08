#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


FULL = [
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

RENAME = [
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

EXTRACT = [
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

CLASS_RENAME = [
    "old",
    "new",
    "phase",
    "confidence",
    "status",
    "notes",
]

DEFER = [
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

HEADERS_BY_ACTION = {
    "rename": RENAME,
    "extract": EXTRACT,
    "class_rename": CLASS_RENAME,
    "defer": DEFER,
}


def _read_plan_rows(plan_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for csv_path in sorted(plan_dir.glob("*.csv")):
        if csv_path.name.startswith("_") or csv_path.name == "layout_rules.csv":
            continue
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader((line for line in handle if line.strip() and not line.lstrip().startswith("#")))
            if not reader.fieldnames:
                continue
            stem_parts = csv_path.stem.split(".")
            inferred_module = stem_parts[0]
            inferred_action = stem_parts[1] if len(stem_parts) > 1 and stem_parts[1] in HEADERS_BY_ACTION else ""
            if "action" not in (reader.fieldnames or []) and not inferred_action:
                continue
            fieldnames = set(reader.fieldnames)
            for raw in reader:
                data = {k: (raw.get(k, "") or "").strip() if k in fieldnames else "" for k in FULL}
                if not data["action"]:
                    data["action"] = inferred_action
                if not any(data.values()):
                    continue
                if not data["module"]:
                    data["module"] = inferred_module
                rows.append(data)
    return rows


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})


def main() -> int:
    ap = argparse.ArgumentParser(description="Rewrite plan CSVs into action-focused, human-readable module files.")
    ap.add_argument("--plan-dir", type=Path, default=Path("client/refactor/.refactor-plan"))
    ap.add_argument("--archive-dir", type=Path, default=Path("client/refactor/.refactor-plan/archive/plan-flat-v1"))
    args = ap.parse_args()

    plan_dir = args.plan_dir
    rows = _read_plan_rows(plan_dir)
    if not rows:
        print(f"No plan rows found in {plan_dir}")
        return 0

    # Archive current module csvs before rewrite.
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(plan_dir.glob("*.csv")):
        if src.name.startswith("_") or src.name == "layout_rules.csv":
            continue
        src.rename(args.archive_dir / src.name)

    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        action = row["action"]
        if action not in HEADERS_BY_ACTION:
            continue
        grouped[(row["module"], action)].append(row)

    for (module, action), action_rows in sorted(grouped.items()):
        header = HEADERS_BY_ACTION[action]
        # Improve readability: deterministic sort by file/owner/member/old/new.
        action_rows = sorted(
            action_rows,
            key=lambda r: (
                r.get("file", ""),
                r.get("owner", ""),
                r.get("member", ""),
                r.get("old", ""),
                r.get("new", ""),
            ),
        )
        out_path = plan_dir / f"{module}.{action}.csv"
        _write_csv(out_path, header, action_rows)

    print(f"Rewrote {len(rows)} rows into action-focused module CSVs in {plan_dir}")
    print(f"Archived previous module CSVs in {args.archive_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
