from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


@dataclasses.dataclass(frozen=True)
class MoveSpec:
    kind: str
    name: str


@dataclasses.dataclass(frozen=True)
class ExtractManifest:
    source: Path
    target_file: Path
    target_class: str
    moves: tuple[MoveSpec, ...]
    path: Path


def _parse_yaml_like_manifest(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("source:"):
            out["source"] = line.split(":", 1)[1].strip().strip("'\"")
            index += 1
            continue
        if line.startswith("target_file:"):
            out["target_file"] = line.split(":", 1)[1].strip().strip("'\"")
            index += 1
            continue
        if line.startswith("target_class:"):
            out["target_class"] = line.split(":", 1)[1].strip().strip("'\"")
            index += 1
            continue
        if line.startswith("moves:"):
            index += 1
            moves: list[dict[str, str]] = []
            current: dict[str, str] | None = None
            while index < len(lines):
                sub = lines[index]
                if not sub.startswith("  "):
                    break
                stripped = sub.strip()
                if stripped.startswith("- "):
                    if current:
                        moves.append(current)
                    current = {}
                    stripped = stripped[2:].strip()
                    if stripped:
                        for part in stripped.split(","):
                            if ":" in part:
                                key, value = part.split(":", 1)
                                current[key.strip()] = value.strip().strip("'\"")
                    index += 1
                    continue
                if current is not None and ":" in stripped:
                    key, value = stripped.split(":", 1)
                    current[key.strip()] = value.strip().strip("'\"")
                index += 1
            if current:
                moves.append(current)
            out["moves"] = moves
            continue
        index += 1
    return out


def load_manifest(path: Path) -> ExtractManifest:
    text = path.read_text(encoding="utf-8", errors="replace")
    data: dict[str, Any]
    try:
        data = json.loads(text)
    except Exception:
        data = _parse_yaml_like_manifest(text)
    source = Path(str(data.get("source", "")).strip())
    target_file = Path(str(data.get("target_file", "")).strip())
    target_class = str(data.get("target_class", "")).strip()
    raw_moves = data.get("moves") or []
    moves: list[MoveSpec] = []
    if not isinstance(raw_moves, list):
        raise SystemExit(f"{path}: moves must be a list")
    for raw in raw_moves:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        name = str(raw.get("name", "")).strip()
        if kind not in ("method", "field", "static_init") or not name:
            continue
        moves.append(MoveSpec(kind=kind, name=name))
    if not source or not target_file or not target_class:
        raise SystemExit(f"{path}: required keys: source, target_file, target_class")
    if not moves:
        raise SystemExit(f"{path}: no valid moves")
    return ExtractManifest(
        source=source,
        target_file=target_file,
        target_class=target_class,
        moves=tuple(moves),
        path=path,
    )


def load_manifests(path: Path) -> list[ExtractManifest]:
    files: list[Path]
    if path.is_file():
        files = [path]
    else:
        files = sorted(path.rglob("*.yaml"))
    return [load_manifest(file_path) for file_path in files]

