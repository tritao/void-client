#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import REFACTOR_PLAN_GENERATED_DIR


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _reason_of(row: dict[str, str]) -> str:
    reason = (row.get("gate_reason") or "").strip()
    if reason:
        return reason
    return "unspecified"


def _build_markdown(
    *,
    rows: list[dict[str, str]],
    by_reason: Counter[str],
    by_file: Counter[str],
) -> str:
    lines: list[str] = []
    lines.append("# Caller Guard Medium Report")
    lines.append("")
    lines.append(f"Rows: {len(rows)}")
    lines.append("")
    lines.append("## By Reason")
    lines.append("")
    lines.append("| reason | count |")
    lines.append("|---|---:|")
    for reason, count in by_reason.most_common():
        lines.append(f"| `{reason}` | {count} |")
    lines.append("")
    lines.append("## Top Files")
    lines.append("")
    lines.append("| file | count |")
    lines.append("|---|---:|")
    for file, count in by_file.most_common(30):
        lines.append(f"| `{file}` | {count} |")
    lines.append("")
    return "\n".join(lines)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report remaining medium guard_dead_by_callers candidates by gate_reason.")
    ap.add_argument(
        "--in-csv",
        type=Path,
        default=REFACTOR_PLAN_GENERATED_DIR / "unified_cleanup_candidates.csv",
    )
    ap.add_argument("--out-md", type=Path, default=Path("docs/cleanup-unified-mediums.md"))
    args = ap.parse_args(argv)
    resolve_path_args(args, ("in_csv", "out_md"))

    rows = _read_rows(args.in_csv)
    medium_rows = [
        row
        for row in rows
        if (row.get("rule_type") or "").strip() == "guard_dead_by_callers"
        and (row.get("confidence") or "").strip() == "medium"
    ]

    by_reason: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    for row in medium_rows:
        by_reason[_reason_of(row)] += 1
        by_file[(row.get("file") or "").strip()] += 1

    report = _build_markdown(rows=medium_rows, by_reason=by_reason, by_file=by_file)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(report + "\n", encoding="utf-8")

    print(f"medium_rows={len(medium_rows)}")
    for reason, count in by_reason.most_common():
        print(f"{reason},{count}")
    print(f"report={args.out_md}")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
