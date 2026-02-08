from __future__ import annotations

import csv
from pathlib import Path

from tools.refactor.common.io import non_comment_csv_lines
from tools.refactor.rename.models import Mapping


def load_static_extract_moves(manifest_dir: Path) -> dict[tuple[str, str, str], str]:
    moved: dict[tuple[str, str, str], str] = {}
    if not manifest_dir.exists():
        return moved

    for p in sorted(manifest_dir.glob("*.yaml")):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        source_stem = ""
        target_class = ""
        in_moves = False
        cur_kind = ""
        cur_name = ""

        def _flush_move() -> None:
            nonlocal cur_kind, cur_name
            if not source_stem or not target_class or not cur_kind or not cur_name:
                cur_kind = ""
                cur_name = ""
                return
            key = (source_stem, cur_kind, cur_name)
            moved.setdefault(key, target_class)
            cur_kind = ""
            cur_name = ""

        for raw in lines:
            line = raw.rstrip("\n")
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("source:"):
                val = s.split(":", 1)[1].strip()
                source_stem = Path(val).stem
                continue
            if s.startswith("target_class:"):
                target_class = s.split(":", 1)[1].strip()
                continue
            if s.startswith("moves:"):
                in_moves = True
                continue
            if not in_moves:
                continue
            if s.startswith("- "):
                _flush_move()
                s = s[2:].strip()
            if s.startswith("kind:"):
                cur_kind = s.split(":", 1)[1].strip().lower()
                continue
            if s.startswith("name:"):
                cur_name = s.split(":", 1)[1].strip()
                continue

        _flush_move()

    return moved


def _load_mappings_from_csv(path: Path, *, label: str) -> list[Mapping]:
    raw_lines = non_comment_csv_lines(path)
    if not raw_lines:
        return []

    reader = csv.DictReader(raw_lines)
    if not reader.fieldnames:
        return []
    if "old" not in reader.fieldnames or "new" not in reader.fieldnames:
        raise SystemExit(f"{label} must have header containing at least: old,new (got: {reader.fieldnames})")

    out: list[Mapping] = []
    for row in reader:
        old = (row.get("old") or "").strip()
        new = (row.get("new") or "").strip()
        if not old or not new:
            continue
        out.append(
            Mapping(
                old=old,
                new=new,
                kind=(row.get("kind") or "").strip(),
                owner=(row.get("owner") or "").strip(),
                member=(row.get("member") or "").strip(),
                file=(row.get("file") or "").strip(),
                signature=(row.get("signature") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
        )
    return out


def load_mappings(*, csv_files: list[Path], csv_dir: Path | None) -> list[Mapping]:
    out: list[Mapping] = []
    for p in csv_files:
        if p.exists():
            out.extend(_load_mappings_from_csv(p, label=str(p)))

    if csv_dir and csv_dir.exists():
        for p in sorted(csv_dir.rglob("*.csv")):
            out.extend(_load_mappings_from_csv(p, label=str(p)))
    return out
