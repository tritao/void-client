from __future__ import annotations

from pathlib import Path


def build_stem_index(java_files: list[Path]) -> dict[str, list[Path]]:
    stem_index: dict[str, list[Path]] = {}
    for path in java_files:
        stem_index.setdefault(path.stem, []).append(path)
    return stem_index


def resolve_scope_files_with_index(
    *,
    mapping_file: str,
    src_dir: Path,
    stem_index: dict[str, list[Path]],
) -> set[Path]:
    if not mapping_file:
        return set()

    value = mapping_file.strip()
    out: set[Path] = set()
    looks_like_path = "/" in value or value.endswith(".java")
    if looks_like_path:
        path = Path(value)
        candidates: list[Path] = []
        if path.is_absolute():
            candidates.append(path.resolve())
        else:
            candidates.append((Path.cwd() / path).resolve())
            candidates.append((src_dir / path).resolve())
        for candidate in candidates:
            if candidate.exists() and candidate.suffix == ".java":
                out.add(candidate)
    else:
        stem = value[:-5] if value.endswith(".java") else value
        for path in stem_index.get(stem, []):
            out.add(path.resolve())
    return out


def resolve_scope_files(*, mapping_file: str, src_dir: Path, java_files: list[Path]) -> set[Path]:
    return resolve_scope_files_with_index(
        mapping_file=mapping_file,
        src_dir=src_dir,
        stem_index=build_stem_index(java_files),
    )
