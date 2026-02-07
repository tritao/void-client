#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import ctypes
import dataclasses
import json
import os
import re
import sys
import hashlib
from pathlib import Path
from typing import Any, Iterable


try:
    from tree_sitter import Language, Node, Parser  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "\n".join(
            [
                f"Missing tree-sitter dependency: {e}",
                "",
                "Run:",
                "  make bootstrap-refactor-tools",
                "",
                "Then rerun this command using:",
                "  ./.venv/bin/python tools/ts_rename_identifiers.py ...",
            ]
        )
    )


JAVA_GRAMMAR_DIR = Path("tools/vendor/tree-sitter-java")
_TS_LIB_HANDLES: dict[str, ctypes.CDLL] = {}

CACHE_SCHEMA_VERSION = 1

# Conservative default: only rename classic JODE-style numbered identifiers.
#
# Notes:
# - JODE commonly emits object-typed fields like `aClass45_4286`, `aClass348Array4374`,
#   `aClass318_Sub1Array4293`, `aClass190ArrayArray3335`, etc.
# - It also emits primitive multi-dimensional arrays like `anIntArrayArray1234`.
OBF_NAME_RX = re.compile(
    r"^(?:"
    r"anInt(?:Array)*|"
    r"aSoftReference(?:Array)*|"
    r"aByte(?:Array)*|aShort(?:Array)*|aLong(?:Array)*|aChar(?:Array)*|"
    r"aBoolean(?:Array)*|aFloat(?:Array)*|aDouble(?:Array)*|aString(?:Array)*|"
    r"anObject(?:Array)*|"
    r"aD_?|"
    r"aPlayer(?:Array)*_?|"
    r"aClass\d+(?:_Sub\d+)*(?:Array)*_?|"
    r"aBigInteger"
    r")\d+$"
)
OBF_METHOD_RX = re.compile(r"^method\d+$")
OBF_PARAM_LOCAL_RX = re.compile(
    r"^(?:"
    r"[ijl]|[ijl]_\d+_|"
    r"is(?:_\d+_)?|"
    r"interface\d+s?(?:_\d+_)?|"
    r"bool(?:_\d+_)?|"
    r"byte(?:_\d+_)?|short(?:_\d+_)?|char(?:_\d+_)?|float(?:_\d+_)?|double(?:_\d+_)?|"
    r"long(?:_\d+_)?|int(?:_\d+_)?|"
    r"string(?:_\d+_)?|text(?:\d+)?|object(?:_\d+_)?|"
    r"flag\d*|"
    r"class\d+(?:_sub\d+)*(?:s)?(?:_\d+_)?"
    r")$",
    re.IGNORECASE,
)

JAVA_IDENT_RX = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_obfuscated_name(old: str, kind: str) -> bool:
    k = (kind or "").strip().lower()
    if k == "method":
        return bool(OBF_METHOD_RX.match(old)) or bool(OBF_NAME_RX.match(old))
    if k in ("param", "local"):
        return bool(OBF_PARAM_LOCAL_RX.match(old)) or bool(OBF_NAME_RX.match(old))
    return bool(OBF_NAME_RX.match(old))


@dataclasses.dataclass(frozen=True)
class Mapping:
    old: str
    new: str
    kind: str = ""
    owner: str = ""
    file: str = ""
    signature: str = ""
    notes: str = ""


@dataclasses.dataclass(frozen=True)
class PreparedMapping:
    old: str
    new: str
    kind: str
    owner: str
    file: str
    signature: str
    signature_types: tuple[str, ...] | None
    notes: str
    scope_files: frozenset[Path]


@dataclasses.dataclass(frozen=True)
class ClassIndex:
    # Map: ClassName -> { fieldName -> TypeName }
    fields: dict[str, dict[str, str]]
    extends_of: dict[str, str]
    # Map: ClassName -> { methodName -> ReturnTypeName }
    methods: dict[str, dict[str, str]]
    implements_of: dict[str, list[str]]


@dataclasses.dataclass(frozen=True)
class TypeEnv:
    # Map: identifier -> TypeName (best-effort, Java simple names).
    names: dict[str, str]
    current_class: str


@dataclasses.dataclass
class Report:
    renamed_files: dict[str, int]
    renamed_total: int
    skipped: list[str]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rename report (identifiers via tree-sitter)")
        lines.append("")
        lines.append(f"Edited files: {len(self.renamed_files)}  ")
        lines.append(f"Renamed identifiers: {self.renamed_total}")
        lines.append("")
        lines.append("## Files")
        lines.append("")
        lines.append("| file | edits |")
        lines.append("|---|---:|")
        for f, n in sorted(self.renamed_files.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{f}` | {n} |")
        lines.append("")
        if self.skipped:
            lines.append("## Skipped")
            lines.append("")
            for s in self.skipped:
                lines.append(f"- {s}")
            lines.append("")
        return "\n".join(lines)

    def to_summary_json(self) -> dict:
        def _reason(s: str) -> str:
            # Heuristic buckets: stable reporting without threading structured
            # codes through every skip site.
            if ":" in s:
                return s.split(":", 1)[0].strip()
            return s.split("(", 1)[0].strip()

        reasons: dict[str, int] = {}
        for s in self.skipped:
            r = _reason(s)
            reasons[r] = reasons.get(r, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        return {
            "edited_files": len(self.renamed_files),
            "renamed_identifiers": self.renamed_total,
            "skipped": len(self.skipped),
            "skipped_reasons": {k: v for k, v in top},
        }


@dataclasses.dataclass(frozen=True)
class CachedIndexes:
    field_decls: dict[str, set[Path]]
    class_index: ClassIndex


def _java_tree_fingerprint(*, src_dir: Path, java_files: list[Path]) -> str:
    """
    Fingerprint the Java tree using cheap stat metadata.

    This intentionally avoids hashing file contents (too expensive) and is good enough
    for our loop: any edit should bump mtime and/or size for affected files.
    """
    h = hashlib.sha256()
    h.update(f"schema={CACHE_SCHEMA_VERSION}\n".encode("utf-8"))
    # Include src_dir to avoid cross-tree collisions.
    h.update(f"src={src_dir}\n".encode("utf-8"))
    for p in java_files:
        try:
            st = p.stat()
        except OSError:
            continue
        try:
            rel = str(p.relative_to(src_dir))
        except ValueError:
            rel = str(p)
        h.update(rel.encode("utf-8", errors="replace"))
        h.update(b"\0")
        h.update(str(st.st_size).encode("ascii"))
        h.update(b"\0")
        h.update(str(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()[:16]


def _load_cached_indexes(cache_path: Path) -> CachedIndexes | None:
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
        ci = ClassIndex(
            fields=dict(class_index_raw.get("fields") or {}),
            extends_of=dict(class_index_raw.get("extends_of") or {}),
            methods=dict(class_index_raw.get("methods") or {}),
            implements_of=dict(class_index_raw.get("implements_of") or {}),
        )
        return CachedIndexes(field_decls=field_decls, class_index=ci)
    except Exception:
        return None


def _write_cached_indexes(cache_path: Path, *, field_decls: dict[str, set[Path]], class_index: ClassIndex) -> None:
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

def _load_static_extract_moves(manifest_dir: Path) -> dict[tuple[str, str, str], str]:
    """
    Load best-effort moved-member mapping from extract-statics manifests.

    The manifests are small YAML-ish files written by our extractor, e.g.:
      source: collections/reference/HardReferenceNode.java
      target_class: InboundPacketDispatcher
      moves:
        - kind: field
          name: anInt1234

    Returns:
      (source_class, kind, member_old_name) -> target_class
    """
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
            # If conflicting manifests claim the same move, keep first (deterministic).
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

            # Start of an item: "- kind: field"
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


def _non_comment_csv_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        out.append(raw)
    return out


def _load_mappings_from_csv(path: Path, *, label: str) -> list[Mapping]:
    raw_lines = _non_comment_csv_lines(path.read_text(encoding="utf-8", errors="replace"))
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
                file=(row.get("file") or "").strip(),
                signature=(row.get("signature") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
        )
    return out


def _parse_signature_types(signature_text: str) -> tuple[str, ...] | None:
    """
    Parse a signature string into a tuple of normalized parameter type names.

    Conventions supported (best-effort):
    - "(int, String)" / "int, String"
    - "int i, String s"
    - "byte[] data"
    - "String... args"

    Returns:
    - None when signature_text is empty (unspecified)
    - () for an explicitly empty signature like "()" (zero args)
    """
    s = (signature_text or "").strip()
    if not s:
        return None
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    if not s:
        return ()
    parts = [p.strip() for p in s.split(",")]
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        # Drop common modifiers/annotations and collapse whitespace.
        p = re.sub(r"@\w+(?:\\([^)]*\\))?\\s*", "", p)
        p = re.sub(r"\\bfinal\\b", "", p)
        p = re.sub(r"\\s+", " ", p).strip()
        # If it looks like "Type name", drop trailing param name.
        toks = p.split(" ")
        if len(toks) >= 2 and JAVA_IDENT_RX.match(toks[-1]):
            p = " ".join(toks[:-1]).strip()
        p = p.replace("...", "").strip()
        t = _normalize_type_name(p)
        if t:
            out.append(t)
    return tuple(out)


def load_mappings(*, csv_files: list[Path], csv_dir: Path | None) -> list[Mapping]:
    out: list[Mapping] = []
    for p in csv_files:
        if p.exists():
            out.extend(_load_mappings_from_csv(p, label=str(p)))

    if csv_dir and csv_dir.exists():
        for p in sorted(csv_dir.rglob("*.csv")):
            out.extend(_load_mappings_from_csv(p, label=str(p)))
    return out


def _build_java_language(*, out_so: Path) -> Any:
    out_so.parent.mkdir(parents=True, exist_ok=True)
    if not JAVA_GRAMMAR_DIR.exists():
        raise SystemExit(f"Missing vendored Java grammar at {JAVA_GRAMMAR_DIR}")
    if not (JAVA_GRAMMAR_DIR / "src" / "parser.c").exists():
        raise SystemExit(f"Vendored grammar missing generated parser.c: {JAVA_GRAMMAR_DIR}/src/parser.c")
    # Build once per output path.
    if not out_so.exists():
        Language.build_library(str(out_so), [str(JAVA_GRAMMAR_DIR)])
    # Prefer pointer-based API to avoid Language(path, name) deprecation warnings.
    try:
        lib = ctypes.CDLL(str(out_so))
        symbol = getattr(lib, "tree_sitter_java")
        symbol.restype = ctypes.c_void_p
        ptr = symbol()
        _TS_LIB_HANDLES[str(out_so)] = lib
        return Language(ptr, "java")
    except Exception:
        return Language(str(out_so), "java")


def _iter_nodes(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        # Reverse so traversal is stable-ish.
        for ch in reversed(n.children):
            stack.append(ch)


def _apply_edits_bytes(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    # Apply back-to-front.
    for start, end, repl in sorted(edits, key=lambda t: (t[0], t[1]), reverse=True):
        data = data[:start] + repl + data[end:]
    return data


def _dedupe_and_prune_overlaps(
    edits: list[tuple[int, int, bytes]],
) -> tuple[list[tuple[int, int, bytes]], list[str]]:
    """
    tree-sitter normally produces non-overlapping identifier spans, but in the
    presence of parse errors we can end up with overlapping edits. Overlapping
    byte-range edits can corrupt output (e.g. drop punctuation).

    Strategy:
    - Deduplicate exact (start,end) spans: keep one if replacement identical;
      if replacement differs, keep the shorter replacement deterministically
      and record a skip.
    - Prune overlaps: prefer the smallest span (most specific); record skips.
    """
    if not edits:
        return [], []
    skipped: list[str] = []

    # Deduplicate exact spans.
    by_span: dict[tuple[int, int], bytes] = {}
    for start, end, repl in edits:
        key = (start, end)
        if key not in by_span:
            by_span[key] = repl
            continue
        if by_span[key] == repl:
            continue
        # Deterministic: keep shorter replacement to reduce disruption.
        old = by_span[key]
        keep = repl if len(repl) < len(old) else old
        drop = old if keep is repl else repl
        by_span[key] = keep
        skipped.append(f"Conflicting edit span {start}:{end} (kept {keep!r}, dropped {drop!r})")

    spans = [(s, e, r) for (s, e), r in by_span.items()]
    spans.sort(key=lambda t: (t[0], t[1]))

    # Prune overlaps by preferring smaller spans.
    pruned: list[tuple[int, int, bytes]] = []
    for start, end, repl in spans:
        if not pruned:
            pruned.append((start, end, repl))
            continue
        p_start, p_end, p_repl = pruned[-1]
        if start >= p_end:
            pruned.append((start, end, repl))
            continue
        # Overlap: keep the smaller span.
        cur_len = end - start
        prev_len = p_end - p_start
        if cur_len < prev_len:
            skipped.append(f"Overlapping edit dropped previous span {p_start}:{p_end} for {start}:{end}")
            pruned[-1] = (start, end, repl)
        else:
            skipped.append(f"Overlapping edit skipped span {start}:{end} (kept {p_start}:{p_end})")
    return pruned, skipped


def _resolve_scope_files(*, mapping_file: str, src_dir: Path, java_files: list[Path]) -> set[Path]:
    if not mapping_file:
        return set()
    stem_index: dict[str, list[Path]] = {}
    for p in java_files:
        stem_index.setdefault(p.stem, []).append(p)

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
        for c in candidates:
            if c.exists() and c.suffix == ".java":
                out.add(c)
    else:
        stem = value[:-5] if value.endswith(".java") else value
        for p in stem_index.get(stem, []):
            out.add(p.resolve())
    return out


def _collect_field_declarations_from_tree(*, data: bytes, root: Node, path: Path) -> dict[str, set[Path]]:
    out: dict[str, set[Path]] = {}
    for n in _iter_nodes(root):
        if n.type != "variable_declarator":
            continue
        parent = n.parent
        if parent is None or parent.type != "field_declaration":
            continue
        ident = None
        for ch in n.children:
            if ch.type in ("identifier", "type_identifier"):
                ident = data[ch.start_byte : ch.end_byte]
                break
        if ident is None:
            continue
        try:
            name = ident.decode("utf-8")
        except UnicodeDecodeError:
            continue
        out.setdefault(name, set()).add(path.resolve())
    return out


def _node_text(data: bytes, n: Node) -> str:
    try:
        return data[n.start_byte : n.end_byte].decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _normalize_type_name(type_text: str) -> str:
    # Best-effort type normalization: strip generics and array suffixes.
    t = type_text.strip()
    if not t:
        return ""
    # Strip annotations/modifiers that can appear in extracted text.
    t = re.sub(r"\b(final|static|volatile|transient)\b", "", t).strip()
    # Strip generics.
    if "<" in t:
        t = t.split("<", 1)[0].strip()
    # Strip varargs suffix.
    if t.endswith("..."):
        t = t[:-3].strip()
    # Strip array suffixes.
    while t.endswith("[]"):
        t = t[:-2].strip()
    # For qualified names, prefer simple name (no packages in refactor tree, but keep safe).
    if "." in t:
        t = t.rsplit(".", 1)[-1].strip()
    return t


def _build_class_index(*, parser: Parser, java_files: list[Path]) -> ClassIndex:
    fields: dict[str, dict[str, str]] = {}
    extends_of: dict[str, str] = {}
    methods: dict[str, dict[str, str]] = {}
    implements_of: dict[str, list[str]] = {}
    for path in java_files:
        data = path.read_bytes()
        tree = parser.parse(data)
        class_name = ""
        superclass_name = ""
        interface_names: list[str] = []
        # Prefer first top-level class declaration name.
        for n in _iter_nodes(tree.root_node):
            if n.type not in ("class_declaration", "interface_declaration", "enum_declaration"):
                continue
            name_node = n.child_by_field_name("name")
            if name_node is None:
                continue
            class_name = _node_text(data, name_node).strip()
            if n.type == "class_declaration":
                sc = n.child_by_field_name("superclass")
                if sc is not None:
                    raw = _node_text(data, sc).strip()
                    # raw usually looks like: "extends Foo" or "extends pkg.Foo"
                    raw = re.sub(r"^\s*extends\s+", "", raw).strip()
                    superclass_name = _normalize_type_name(raw)
            iface_node = n.child_by_field_name("interfaces") or n.child_by_field_name("super_interfaces")
            if iface_node is not None:
                raw = _node_text(data, iface_node).strip()
                raw = re.sub(r"^\s*(?:implements|extends)\s+", "", raw).strip()
                parts = [p.strip() for p in raw.split(",") if p.strip()]
                interface_names = [_normalize_type_name(p) for p in parts if _normalize_type_name(p)]
            if class_name:
                break
        if not class_name:
            continue
        if superclass_name:
            extends_of[class_name] = superclass_name
        if interface_names:
            implements_of[class_name] = interface_names
        class_fields: dict[str, str] = fields.setdefault(class_name, {})
        class_methods: dict[str, str] = methods.setdefault(class_name, {})
        for n in _iter_nodes(tree.root_node):
            if n.type != "field_declaration":
                continue
            type_node = n.child_by_field_name("type")
            if type_node is None:
                continue
            type_name = _normalize_type_name(_node_text(data, type_node))
            if not type_name:
                continue
            for ch in n.children:
                if ch.type != "variable_declarator":
                    continue
                name_node = ch.child_by_field_name("name")
                if name_node is None:
                    continue
                field_name = _node_text(data, name_node).strip()
                if not field_name:
                    continue
                class_fields[field_name] = type_name

        for n in _iter_nodes(tree.root_node):
            if n.type != "method_declaration":
                continue
            name_node = n.child_by_field_name("name")
            type_node = n.child_by_field_name("type")
            if name_node is None or type_node is None:
                continue
            mname = _node_text(data, name_node).strip()
            rtype = _normalize_type_name(_node_text(data, type_node))
            if mname and rtype and mname not in class_methods:
                class_methods[mname] = rtype
    return ClassIndex(fields=fields, extends_of=extends_of, methods=methods, implements_of=implements_of)


def _build_indexes(*, parser: Parser, java_files: list[Path]) -> CachedIndexes:
    """
    Build all heavyweight indexes in a single parse pass per file.
    """
    field_decls: dict[str, set[Path]] = {}
    fields: dict[str, dict[str, str]] = {}
    extends_of: dict[str, str] = {}
    methods: dict[str, dict[str, str]] = {}
    implements_of: dict[str, list[str]] = {}

    for path in java_files:
        data = path.read_bytes()
        tree = parser.parse(data)

        # Field declarations index (used by safe-preflight).
        per_file = _collect_field_declarations_from_tree(data=data, root=tree.root_node, path=path)
        for name, ps in per_file.items():
            field_decls.setdefault(name, set()).update(ps)

        # Class index (used by semantic member renames).
        class_name = ""
        superclass_name = ""
        interface_names: list[str] = []
        for n in _iter_nodes(tree.root_node):
            if n.type not in ("class_declaration", "interface_declaration", "enum_declaration"):
                continue
            name_node = n.child_by_field_name("name")
            if name_node is None:
                continue
            class_name = _node_text(data, name_node).strip()
            if n.type == "class_declaration":
                sc = n.child_by_field_name("superclass")
                if sc is not None:
                    raw = _node_text(data, sc).strip()
                    raw = re.sub(r"^\s*extends\s+", "", raw).strip()
                    superclass_name = _normalize_type_name(raw)
            iface_node = n.child_by_field_name("interfaces") or n.child_by_field_name("super_interfaces")
            if iface_node is not None:
                raw = _node_text(data, iface_node).strip()
                raw = re.sub(r"^\s*(?:implements|extends)\s+", "", raw).strip()
                parts = [p.strip() for p in raw.split(",") if p.strip()]
                interface_names = [_normalize_type_name(p) for p in parts if _normalize_type_name(p)]
            if class_name:
                break
        if not class_name:
            continue
        if superclass_name:
            extends_of[class_name] = superclass_name
        if interface_names:
            implements_of[class_name] = interface_names
        class_fields: dict[str, str] = fields.setdefault(class_name, {})
        class_methods: dict[str, str] = methods.setdefault(class_name, {})

        for n in _iter_nodes(tree.root_node):
            if n.type != "field_declaration":
                continue
            type_node = n.child_by_field_name("type")
            if type_node is None:
                continue
            type_name = _normalize_type_name(_node_text(data, type_node))
            if not type_name:
                continue
            for ch in n.children:
                if ch.type != "variable_declarator":
                    continue
                name_node = ch.child_by_field_name("name")
                if name_node is None:
                    continue
                field_name = _node_text(data, name_node).strip()
                if not field_name:
                    continue
                class_fields[field_name] = type_name

        for n in _iter_nodes(tree.root_node):
            if n.type != "method_declaration":
                continue
            name_node = n.child_by_field_name("name")
            type_node = n.child_by_field_name("type")
            if name_node is None or type_node is None:
                continue
            mname = _node_text(data, name_node).strip()
            rtype = _normalize_type_name(_node_text(data, type_node))
            if mname and rtype and mname not in class_methods:
                class_methods[mname] = rtype

    return CachedIndexes(
        field_decls=field_decls,
        class_index=ClassIndex(fields=fields, extends_of=extends_of, methods=methods, implements_of=implements_of),
    )


def _iter_owner_chain(type_name: str, class_index: ClassIndex) -> Iterable[str]:
    cur = type_name
    seen: set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        yield cur
        cur = class_index.extends_of.get(cur, "")


def _iter_owner_closure(type_name: str, class_index: ClassIndex) -> Iterable[str]:
    """
    Owner closure for member resolution:
    - The class itself
    - Its superclass chain
    - Interfaces implemented by any class in that chain (non-recursive is enough in practice here,
      but we include a small recursion to handle interfaces extending other interfaces when indexed).
    """
    seen: set[str] = set()
    queue: list[str] = [type_name]
    while queue:
        cur = queue.pop(0)
        if not cur or cur in seen:
            continue
        seen.add(cur)
        yield cur
        sup = class_index.extends_of.get(cur, "")
        if sup and sup not in seen:
            queue.append(sup)
        for iface in class_index.implements_of.get(cur, []):
            if iface and iface not in seen:
                queue.append(iface)
        # Some interface declarations may appear in the index as classes; allow chaining.
        for iface2 in class_index.implements_of.get(cur, []):
            for sub in class_index.implements_of.get(iface2, []):
                if sub and sub not in seen:
                    queue.append(sub)

def _collect_method_env(*, data: bytes, class_index: ClassIndex, current_class: str, scope_node: Node) -> TypeEnv:
    env: dict[str, str] = {}
    # Add fields of the current class (so `this.field` or implicit lookups can resolve).
    for fname, ftype in class_index.fields.get(current_class, {}).items():
        env.setdefault(fname, ftype)
    env["this"] = current_class

    # Params and locals only exist inside method/ctor scopes.
    for n in _iter_nodes(scope_node):
        if n.type == "formal_parameter":
            t = n.child_by_field_name("type")
            name = n.child_by_field_name("name")
            if t is None or name is None:
                continue
            type_name = _normalize_type_name(_node_text(data, t))
            var_name = _node_text(data, name).strip()
            if type_name and var_name:
                env[var_name] = type_name
        elif n.type == "catch_formal_parameter":
            t = n.child_by_field_name("type")
            name = n.child_by_field_name("name")
            if t is None or name is None:
                continue
            type_name = _normalize_type_name(_node_text(data, t))
            var_name = _node_text(data, name).strip()
            if type_name and var_name:
                env[var_name] = type_name
        elif n.type == "local_variable_declaration":
            t = n.child_by_field_name("type")
            if t is None:
                continue
            type_name = _normalize_type_name(_node_text(data, t))
            if not type_name:
                continue
            for ch in n.children:
                if ch.type != "variable_declarator":
                    continue
                name_node = ch.child_by_field_name("name")
                if name_node is None:
                    continue
                var_name = _node_text(data, name_node).strip()
                if var_name:
                    env[var_name] = type_name
        elif n.type == "enhanced_for_statement":
            # `for (Type name : expr)`
            var_node = n.child_by_field_name("variable")
            if var_node is None:
                continue
            t = var_node.child_by_field_name("type")
            name = var_node.child_by_field_name("name")
            if t is None or name is None:
                continue
            type_name = _normalize_type_name(_node_text(data, t))
            var_name = _node_text(data, name).strip()
            if type_name and var_name:
                env[var_name] = type_name
    return TypeEnv(names=env, current_class=current_class)


def _resolve_expr_type(*, data: bytes, class_index: ClassIndex, env: TypeEnv, expr: Node | None) -> str:
    if expr is None:
        return env.current_class
    t = expr.type
    if t in ("identifier", "type_identifier"):
        name = _node_text(data, expr).strip()
        if not name:
            return ""
        if name in env.names:
            return env.names[name]
        # Treat unknown identifier as a type name if we have it indexed.
        if (
            name in class_index.fields
            or name in class_index.methods
            or name in class_index.extends_of
            or name in class_index.implements_of
            or name in class_index.extends_of.values()
            or any(name in v for v in class_index.implements_of.values())
        ):
            return name
        return ""
    if t == "this":
        return env.current_class
    if t == "field_access":
        obj = expr.child_by_field_name("object")
        field = expr.child_by_field_name("field")
        if field is None:
            return ""
        field_name = _node_text(data, field).strip()
        if not field_name:
            return ""
        owner_type = _resolve_expr_type(data=data, class_index=class_index, env=env, expr=obj)
        if not owner_type:
            owner_type = env.current_class
        for owner in _iter_owner_closure(owner_type, class_index):
            ftype = class_index.fields.get(owner, {}).get(field_name, "")
            if ftype:
                return ftype
        return ""
    if t == "parenthesized_expression":
        inner = expr.child_by_field_name("expression")
        if inner is None:
            # tree-sitter-java doesn't consistently label this field; fall back to first named child.
            inner = expr.named_children[0] if getattr(expr, "named_children", None) else None
        return _resolve_expr_type(data=data, class_index=class_index, env=env, expr=inner)
    if t == "cast_expression":
        type_node = expr.child_by_field_name("type")
        if type_node is None:
            return ""
        return _normalize_type_name(_node_text(data, type_node))
    if t == "object_creation_expression":
        type_node = expr.child_by_field_name("type")
        if type_node is None:
            return ""
        return _normalize_type_name(_node_text(data, type_node))
    if t == "method_invocation":
        name_node = expr.child_by_field_name("name")
        if name_node is None:
            return ""
        mname = _node_text(data, name_node).strip()
        if not mname:
            return ""
        obj = expr.child_by_field_name("object")
        recv_type = _resolve_expr_type(data=data, class_index=class_index, env=env, expr=obj)
        if not recv_type:
            recv_type = env.current_class
        for owner in _iter_owner_closure(recv_type, class_index):
            rtype = class_index.methods.get(owner, {}).get(mname, "")
            if rtype:
                return rtype
        return ""
    if t == "scoped_identifier":
        # Treat as qualified name; best-effort simple-name.
        return _normalize_type_name(_node_text(data, expr))
    if t == "array_access":
        arr = expr.child_by_field_name("array")
        if arr is None and expr.named_children:
            arr = expr.named_children[0]
        return _resolve_expr_type(data=data, class_index=class_index, env=env, expr=arr)
    return ""


@dataclasses.dataclass(frozen=True)
class MethodRename:
    signature_types: tuple[str, ...] | None
    new: str


def _iter_args(node: Node) -> list[Node]:
    args = node.child_by_field_name("arguments")
    if args is None:
        for ch in node.children:
            if ch.type == "argument_list":
                args = ch
                break
    if args is None:
        return []
    # named_children should be expressions only.
    return list(getattr(args, "named_children", []) or [])


def _resolve_literal_type(data: bytes, expr: Node) -> str:
    t = expr.type
    if t in ("true", "false"):
        return "boolean"
    if t == "string_literal":
        return "String"
    if t == "character_literal":
        return "char"
    if t == "null_literal":
        return ""
    if t.endswith("_integer_literal"):
        return "int"
    if t.endswith("_floating_point_literal"):
        return "float"
    return ""


def _resolve_arg_type(*, data: bytes, class_index: ClassIndex, env: TypeEnv, expr: Node) -> str:
    lit = _resolve_literal_type(data, expr)
    if lit:
        return lit
    return _resolve_expr_type(data=data, class_index=class_index, env=env, expr=expr)


def _pick_method_rename(
    *,
    candidates: list[MethodRename],
    arg_types: list[str],
) -> str:
    if not candidates:
        return ""
    # Fast path: single candidate.
    if len(candidates) == 1:
        return candidates[0].new

    # If all candidates map to the same new name, we can rename safely.
    unique_new = {c.new for c in candidates}
    if len(unique_new) == 1:
        return next(iter(unique_new))

    argc = len(arg_types)
    # Prefer exact signature match when provided.
    exact: list[MethodRename] = []
    partial: list[MethodRename] = []
    wildcard: list[MethodRename] = []
    for c in candidates:
        if c.signature_types is None:
            wildcard.append(c)
            continue
        if len(c.signature_types) != argc:
            continue
        # Match known arg types; unknown args don't disqualify.
        ok = True
        all_known = True
        for want, got in zip(c.signature_types, arg_types, strict=False):
            if not got:
                all_known = False
                continue
            if got != want:
                ok = False
                break
        if not ok:
            continue
        if all_known:
            exact.append(c)
        else:
            partial.append(c)
    if len(exact) == 1:
        return exact[0].new
    if len(exact) > 1:
        return ""
    if len(partial) == 1:
        return partial[0].new
    if len(partial) > 1:
        return ""
    if len(wildcard) == 1:
        return wildcard[0].new
    return ""


def _rename_member_accesses(
    *,
    data: bytes,
    class_index: ClassIndex,
    env: TypeEnv,
    scope_node: Node,
    field_map: dict[tuple[str, str], str],
    method_map: dict[tuple[str, str], list[MethodRename]],
) -> list[tuple[int, int, bytes]]:
    edits: list[tuple[int, int, bytes]] = []

    def _is_decl_name(n: Node) -> bool:
        p = n.parent
        if p is None:
            return False
        name_node = p.child_by_field_name("name")
        return name_node is not None and name_node.id == n.id

    def _is_known_type_name(name: str) -> bool:
        return (
            name in class_index.fields
            or name in class_index.methods
            or name in class_index.extends_of
            or name in class_index.implements_of
            or name in class_index.extends_of.values()
            or any(name in v for v in class_index.implements_of.values())
        )

    def _resolve_chain_type(parts: list[str]) -> str:
        if not parts:
            return ""
        head = parts[0]
        if head == "this":
            owner_type = env.current_class
        elif head in env.names:
            owner_type = env.names[head]
        elif _is_known_type_name(head):
            owner_type = head
        else:
            return ""
        for field_name in parts[1:]:
            next_type = ""
            for owner in _iter_owner_closure(owner_type, class_index):
                next_type = class_index.fields.get(owner, {}).get(field_name, "")
                if next_type:
                    break
            if not next_type:
                return ""
            owner_type = next_type
        return owner_type

    for n in _iter_nodes(scope_node):
        # Rename bare inherited/static field uses (e.g. `aD4579`) that don't appear as field_access.
        if n.type == "identifier":
            if _is_decl_name(n):
                continue
            old = _node_text(data, n).strip()
            if not old:
                continue
            if old in env.names:
                continue
            new = ""
            for owner in _iter_owner_closure(env.current_class, class_index):
                new = field_map.get((owner, old), "")
                if new:
                    break
            if new and new != old:
                edits.append((n.start_byte, n.end_byte, new.encode("utf-8")))
                continue

        # Handle tree-sitter misparse: expressions parsed as cast_expression with scoped_type_identifier.
        if n.type == "cast_expression":
            type_node = n.child_by_field_name("type")
            if type_node is not None and type_node.type == "scoped_type_identifier":
                chain_text = _node_text(data, type_node).strip()
                if "." in chain_text:
                    parts = [p.strip() for p in chain_text.split(".") if p.strip()]
                    if len(parts) >= 2:
                        receiver_parts = parts[:-1]
                        leaf = parts[-1]
                        receiver_type = _resolve_chain_type(receiver_parts)
                        if receiver_type:
                            new_leaf = ""
                            for owner in _iter_owner_closure(receiver_type, class_index):
                                new_leaf = field_map.get((owner, leaf), "")
                                if new_leaf:
                                    break
                                picked = _pick_method_rename(
                                    candidates=method_map.get((owner, leaf), []), arg_types=[]
                                )
                                if picked:
                                    new_leaf = picked
                                    break
                            if new_leaf and new_leaf != leaf:
                                last_ident: Node | None = None
                                for ch in _iter_nodes(type_node):
                                    if ch.type in ("identifier", "type_identifier"):
                                        last_ident = ch
                                if last_ident is not None:
                                    edits.append((last_ident.start_byte, last_ident.end_byte, new_leaf.encode("utf-8")))
                                    continue

        if n.type == "field_access":
            obj = n.child_by_field_name("object")
            field = n.child_by_field_name("field")
            if field is None:
                continue
            old = _node_text(data, field).strip()
            if not old:
                continue
            owner_type = _resolve_expr_type(data=data, class_index=class_index, env=env, expr=obj)
            if not owner_type:
                continue
            new = ""
            for owner in _iter_owner_closure(owner_type, class_index):
                new = field_map.get((owner, old), "")
                if new:
                    break
            if new and new != old:
                edits.append((field.start_byte, field.end_byte, new.encode("utf-8")))
                continue

        if n.type == "method_invocation":
            name_node = n.child_by_field_name("name")
            if name_node is None:
                continue
            old = _node_text(data, name_node).strip()
            if not old:
                continue
            obj = n.child_by_field_name("object")
            owner_type = _resolve_expr_type(data=data, class_index=class_index, env=env, expr=obj)
            if not owner_type:
                owner_type = env.current_class
            arg_exprs = _iter_args(n)
            arg_types = [_resolve_arg_type(data=data, class_index=class_index, env=env, expr=a) for a in arg_exprs]
            new = ""
            for owner in _iter_owner_closure(owner_type, class_index):
                picked = _pick_method_rename(candidates=method_map.get((owner, old), []), arg_types=arg_types)
                if picked:
                    new = picked
                    break
            if new and new != old:
                edits.append((name_node.start_byte, name_node.end_byte, new.encode("utf-8")))
                continue

    return edits


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, action="append", default=[Path("client/refactor/.refactor-plan/generated/symbol_renames.csv")])
    ap.add_argument("--csv-dir", type=Path, default=Path("client/refactor/.refactor-plan/symbol-renames/generated"))
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"))
    ap.add_argument(
        "--extract-manifest-dir",
        type=Path,
        default=None,
        help="Optional directory of extract-statics manifests; when present, member renames are also applied to moved targets.",
    )
    ap.add_argument("--report", type=Path, default=Path("docs/rename-report-refactor.md"))
    ap.add_argument("--report-json", type=Path, default=Path("build/refactor-state/rename-summary.json"))
    ap.add_argument("--max-mappings", type=int, default=25, help="Apply at most N mapping rows (default 25). Use -1 for all.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-non-obfuscated", action="store_true", help="Allow renaming identifiers not matching the default obfuscated-name regex.")
    ap.add_argument(
        "--safe-preflight",
        action="store_true",
        help="Preflight mappings and skip unsafe rows (e.g. ambiguous field declarations).",
    )
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    ap.add_argument("--cache-dir", type=Path, default=Path("build/refactor-cache/ts_rename_identifiers"))
    ap.add_argument("--no-cache", action="store_true", help="Disable disk cache for indexes.")
    ap.add_argument(
        "--semantic-members",
        action="store_true",
        default=True,
        help="Rename method/field uses based on best-effort receiver type (default on).",
    )
    args = ap.parse_args(argv)

    root = Path.cwd()
    src_dir = args.src_dir.resolve()
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")
    extract_manifest_dir = (args.extract_manifest_dir.resolve() if args.extract_manifest_dir else None)

    mappings = load_mappings(csv_files=[Path(p) for p in (args.csv or [])], csv_dir=args.csv_dir)
    if not mappings:
        raise SystemExit("No mappings found (expected --csv files and/or --csv-dir).")
    if args.max_mappings >= 0:
        mappings = mappings[: args.max_mappings]

    java_files = sorted(src_dir.rglob("*.java"))
    java = _build_java_language(out_so=args.language_so)
    parser = Parser()
    parser.set_language(java)
    field_decls: dict[str, set[Path]] = {}
    class_index = ClassIndex(fields={}, extends_of={}, methods={}, implements_of={})
    need_indexes = bool(args.safe_preflight or args.semantic_members)
    if need_indexes:
        fingerprint = _java_tree_fingerprint(src_dir=src_dir, java_files=java_files)
        cache_path = (args.cache_dir / f"indexes-{fingerprint}.json").resolve()
        cached = None if args.no_cache else _load_cached_indexes(cache_path)
        if cached is not None:
            field_decls = cached.field_decls
            class_index = cached.class_index
        else:
            built = _build_indexes(parser=parser, java_files=java_files)
            field_decls = built.field_decls
            class_index = built.class_index
            if not args.no_cache:
                _write_cached_indexes(cache_path, field_decls=field_decls, class_index=class_index)

    moved_members: dict[tuple[str, str, str], str] = {}
    if extract_manifest_dir:
        moved_members = _load_static_extract_moves(extract_manifest_dir)

    # Validate + build prepared mapping table (supports per-file scoping for all kinds).
    prepared: list[PreparedMapping] = []
    skipped: list[str] = []
    for m in mappings:
        if not JAVA_IDENT_RX.match(m.old) or not JAVA_IDENT_RX.match(m.new):
            skipped.append(f"Invalid java identifier: {m.old!r} -> {m.new!r}")
            continue
        if not args.allow_non_obfuscated and not _is_obfuscated_name(m.old, m.kind):
            skipped.append(f"Not obfuscated (use --allow-non-obfuscated): {m.old} -> {m.new}")
            continue
        scope_files = _resolve_scope_files(mapping_file=m.file, src_dir=src_dir, java_files=java_files)
        sig_types = _parse_signature_types(m.signature) if (m.kind or "").strip().lower() == "method" else None
        if args.safe_preflight and m.kind.lower() == "field":
            old_decl_files = field_decls.get(m.old, set())
            new_decl_files = field_decls.get(m.new, set())
            owner_stem = (m.owner or "").strip()

            if scope_files:
                old_decl_files = {p for p in old_decl_files if p in scope_files}
                new_decl_files = {p for p in new_decl_files if p in scope_files}
            if owner_stem:
                old_decl_files_owner = {p for p in old_decl_files if p.stem == owner_stem}
                new_decl_files_owner = {p for p in new_decl_files if p.stem == owner_stem}
            else:
                old_decl_files_owner = set()
                new_decl_files_owner = set()

            # Normal case: declaration still uses `old`.
            if old_decl_files:
                if len(old_decl_files) > 1 and not (owner_stem and len(old_decl_files_owner) == 1):
                    skipped.append(
                        f"Ambiguous field declaration ({len(old_decl_files)} files): {m.old} -> {m.new}"
                    )
                    continue
            # Idempotent case: declaration already renamed to `new` in a prior run,
            # but callsites may still contain `old`.
            elif new_decl_files:
                if len(new_decl_files) > 1 and not (owner_stem and len(new_decl_files_owner) == 1):
                    skipped.append(
                        f"Ambiguous renamed field declaration ({len(new_decl_files)} files): {m.old} -> {m.new}"
                    )
                    continue
            else:
                skipped.append(f"No field declaration found (old/new): {m.old} -> {m.new}")
                continue
        prepared.append(
            PreparedMapping(
                old=m.old,
                new=m.new,
                kind=m.kind,
                owner=m.owner,
                file=m.file,
                signature=m.signature,
                signature_types=sig_types,
                notes=m.notes,
                scope_files=frozenset(scope_files),
            )
        )

    if not prepared:
        raise SystemExit("No applicable mappings after filtering.")

    def _scopes_overlap(a: PreparedMapping, b: PreparedMapping) -> bool:
        # Empty scope means global (all files), therefore overlaps everything.
        if not a.scope_files or not b.scope_files:
            return True
        return bool(a.scope_files.intersection(b.scope_files))

    # Validate conflicts for same-key mappings, taking scope overlap into account.
    by_key: dict[tuple[str, str, tuple[str, ...] | None], list[PreparedMapping]] = {}
    for m in prepared:
        k = (m.kind or "").strip().lower()
        sig = m.signature_types if k == "method" else None
        by_key.setdefault((k, m.old, sig), []).append(m)
    for (k, old, sig), entries in by_key.items():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                left = entries[i]
                right = entries[j]
                if left.new == right.new:
                    continue
                if _scopes_overlap(left, right):
                    raise SystemExit(
                        f"Conflicting mappings for {k}:{old}{' ' + str(sig) if sig is not None else ''}: {left.new} vs {right.new} "
                        f"(scope overlap: {left.file or '<global>'} / {right.file or '<global>'})"
                    )

    renamed_files: dict[str, int] = {}
    renamed_total = 0
    overlap_skips: list[str] = []

    # Index mappings by old identifier for fast lookup during traversal.
    by_old_index: dict[str, list[PreparedMapping]] = {}
    for m in prepared:
        by_old_index.setdefault(m.old, []).append(m)

    for path in java_files:
        data = path.read_bytes()
        tree = parser.parse(data)
        resolved_path = path.resolve()
        edits: list[tuple[int, int, bytes]] = []
        for n in _iter_nodes(tree.root_node):
            # Only rename real identifiers. Renaming `type_identifier` globally is unsafe
            # (tree-sitter can misclassify expressions as types and we can corrupt syntax).
            if n.type not in ("identifier", "type_identifier"):
                continue
            if n.type == "type_identifier":
                # Only rename `type_identifier` tokens when they appear under a cast-expression.
                # This recovers mis-parsed parenthesized expressions like `(LoginManager.aPlayer_1907.x)`
                # without risking global syntax corruption.
                p = n.parent
                is_cast_context = False
                hops = 0
                while p is not None and hops < 6:
                    if p.type == "cast_expression":
                        is_cast_context = True
                        break
                    p = p.parent
                    hops += 1
                if not is_cast_context:
                    continue
            tok = data[n.start_byte : n.end_byte]
            try:
                s = tok.decode("utf-8")
            except UnicodeDecodeError:
                continue
            candidates = by_old_index.get(s)
            if not candidates:
                continue
            scoped = [m for m in candidates if not m.scope_files or resolved_path in m.scope_files]
            if not scoped:
                continue
            # Methods are renamed semantically (signature-aware) to support overloads.
            if args.semantic_members and all((m.kind or "").strip().lower() == "method" for m in scoped):
                continue
            if args.semantic_members:
                scoped_non_method = [m for m in scoped if (m.kind or "").strip().lower() != "method"]
                if scoped_non_method:
                    scoped = scoped_non_method
            # Overlapping conflicts are validated above; any matching candidate has same target.
            new = scoped[0].new
            if new == s:
                continue
            edits.append((n.start_byte, n.end_byte, new.encode("utf-8")))

        # Second pass: type-aware member access renames for method/field uses.
        if args.semantic_members:
            field_map: dict[tuple[str, str], str] = {}
            method_map: dict[tuple[str, str], list[MethodRename]] = {}
            for m in prepared:
                k = (m.kind or "").strip().lower()
                if k not in ("field", "method"):
                    continue
                owner = (m.owner or "").strip()
                if not owner:
                    continue
                if k == "field":
                    field_map[(owner, m.old)] = m.new
                else:
                    method_map.setdefault((owner, m.old), []).append(
                        MethodRename(signature_types=m.signature_types, new=m.new)
                    )
                # If extraction moved this member, also apply the mapping under the target owner.
                if moved_members:
                    cur_owner = owner
                    # Allow a short chain in case a member is moved more than once.
                    for _ in range(5):
                        target = moved_members.get((cur_owner, k, m.old))
                        if not target or target == cur_owner:
                            break
                        if k == "field":
                            field_map[(target, m.old)] = m.new
                        else:
                            method_map.setdefault((target, m.old), []).append(
                                MethodRename(signature_types=m.signature_types, new=m.new)
                            )
                        cur_owner = target
            if field_map or method_map:
                # Determine current class name (best-effort from first class declaration in the file).
                current_class = ""
                for n in _iter_nodes(tree.root_node):
                    if n.type not in ("class_declaration", "interface_declaration", "enum_declaration"):
                        continue
                    name_node = n.child_by_field_name("name")
                    if name_node is None:
                        continue
                    current_class = _node_text(data, name_node).strip()
                    if current_class:
                        break
                if current_class:
                    # Rename method declarations in subclasses when they match a mapped superclass method.
                    # This fixes "does not override abstract method ..." errors without requiring full
                    # semantic resolution.
                    for n in _iter_nodes(tree.root_node):
                        if n.type != "method_declaration":
                            continue
                        name_node = n.child_by_field_name("name")
                        if name_node is None:
                            continue
                        old_name = _node_text(data, name_node).strip()
                        if not old_name:
                            continue
                        # Compute declared parameter types for overload resolution.
                        params_node = n.child_by_field_name("parameters")
                        declared_types: list[str] = []
                        if params_node is not None:
                            for p in getattr(params_node, "named_children", []) or []:
                                if p.type != "formal_parameter":
                                    continue
                                tnode = p.child_by_field_name("type")
                                if tnode is None:
                                    continue
                                declared_types.append(_normalize_type_name(_node_text(data, tnode)))
                        new_name = ""
                        for owner in _iter_owner_closure(current_class, class_index):
                            cands = method_map.get((owner, old_name), [])
                            if not cands:
                                continue
                            picked = _pick_method_rename(candidates=cands, arg_types=declared_types)
                            if picked:
                                new_name = picked
                                break
                        if new_name and new_name != old_name:
                            edits.append((name_node.start_byte, name_node.end_byte, new_name.encode("utf-8")))

                    scopes: list[Node] = []
                    for n in _iter_nodes(tree.root_node):
                        if n.type in (
                            "method_declaration",
                            "constructor_declaration",
                            "static_initializer",
                            "instance_initializer",
                            "field_declaration",
                        ):
                            scopes.append(n)
                    for scope in scopes:
                        env = _collect_method_env(
                            data=data, class_index=class_index, current_class=current_class, scope_node=scope
                        )
                        edits.extend(
                            _rename_member_accesses(
                                data=data,
                                class_index=class_index,
                                env=env,
                                scope_node=scope,
                                field_map=field_map,
                                method_map=method_map,
                            )
                        )

        if not edits:
            continue
        edits, skips = _dedupe_and_prune_overlaps(edits)
        if skips:
            overlap_skips.extend([f"{path.name}: {s}" for s in skips[:20]])
            if len(skips) > 20:
                overlap_skips.append(f"{path.name}: (plus {len(skips) - 20} more overlap skips)")
        out = _apply_edits_bytes(data, edits)
        if out != data:
            rel = str(path.relative_to(root))
            renamed_files[rel] = len(edits)
            renamed_total += len(edits)
            if not args.dry_run:
                path.write_bytes(out)

    report_obj = Report(
        renamed_files=renamed_files,
        renamed_total=renamed_total,
        skipped=(skipped + overlap_skips),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_obj.to_markdown() + "\n", encoding="utf-8")
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report_obj.to_summary_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Renamed {renamed_total} identifiers across {len(renamed_files)} files. Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
