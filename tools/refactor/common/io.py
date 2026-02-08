from __future__ import annotations

import csv
from pathlib import Path


def non_comment_csv_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(raw)
    return lines


def read_csv_dict_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        return [], []
    lines = non_comment_csv_lines(path)
    if not lines:
        return [], []
    reader = csv.DictReader(lines)
    fieldnames = list(reader.fieldnames or [])
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({k: (row.get(k) or "").strip() for k in fieldnames})
    return fieldnames, rows


def write_csv_dict_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
