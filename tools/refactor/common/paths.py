from __future__ import annotations

from pathlib import Path


def repo_root_from_file(file_path: str | Path, levels_up: int) -> Path:
    return Path(file_path).resolve().parents[levels_up]


def resolve_repo_root(
    explicit_repo_root: str | Path | None,
    file_path: str | Path,
    *,
    levels_up: int,
) -> Path:
    if explicit_repo_root is None:
        return repo_root_from_file(file_path, levels_up)
    return Path(explicit_repo_root).resolve()
