from __future__ import annotations

import json
import hashlib
from pathlib import Path

from tools.refactor.rename.models import CachedIndexes, ClassIndex


CACHE_SCHEMA_VERSION = 1


def java_tree_fingerprint(*, src_dir: Path, java_files: list[Path]) -> str:
    h = hashlib.sha256()
    h.update(f"schema={CACHE_SCHEMA_VERSION}\n".encode("utf-8"))
    h.update(f"src={src_dir}\n".encode("utf-8"))
    for path in java_files:
        try:
            stat = path.stat()
        except OSError:
            continue
        try:
            rel = str(path.relative_to(src_dir))
        except ValueError:
            rel = str(path)
        h.update(rel.encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(str(stat.st_size).encode("ascii"))
        h.update(b"\0")
        h.update(str(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def load_cached_indexes(cache_path: Path) -> CachedIndexes | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    try:
        field_decls_raw = data.get("field_decls", {})
        class_index_raw = data.get("class_index", {})
        field_decls: dict[str, set[Path]] = {
            str(k): {Path(p) for p in (v or [])} for k, v in (field_decls_raw or {}).items()
        }
        class_index = ClassIndex(
            fields=dict(class_index_raw.get("fields") or {}),
            extends_of=dict(class_index_raw.get("extends_of") or {}),
            methods=dict(class_index_raw.get("methods") or {}),
            implements_of=dict(class_index_raw.get("implements_of") or {}),
        )
        return CachedIndexes(field_decls=field_decls, class_index=class_index)
    except Exception:
        return None


def write_cached_indexes(cache_path: Path, *, field_decls: dict[str, set[Path]], class_index: ClassIndex) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "field_decls": {k: sorted({str(p) for p in v}) for k, v in field_decls.items()},
        "class_index": {
            "fields": class_index.fields,
            "extends_of": class_index.extends_of,
            "methods": class_index.methods,
            "implements_of": class_index.implements_of,
        },
    }
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

