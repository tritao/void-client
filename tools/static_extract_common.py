#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import dataclasses
import json
import re
from pathlib import Path
from typing import Any, Iterable

from tree_sitter import Language, Node, Parser  # type: ignore


JAVA_GRAMMAR_DIR = Path("tools/vendor/tree-sitter-java")
_TS_LIB_HANDLES: dict[str, ctypes.CDLL] = {}


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


@dataclasses.dataclass(frozen=True)
class StaticMember:
    kind: str
    name: str
    start: int
    end: int
    text: str


def _build_java_language(*, out_so: Path) -> Any:
    out_so.parent.mkdir(parents=True, exist_ok=True)
    if not JAVA_GRAMMAR_DIR.exists():
        raise SystemExit(f"Missing vendored Java grammar at {JAVA_GRAMMAR_DIR}")
    if not (JAVA_GRAMMAR_DIR / "src" / "parser.c").exists():
        raise SystemExit(f"Vendored grammar missing generated parser.c: {JAVA_GRAMMAR_DIR}/src/parser.c")
    if not out_so.exists():
        Language.build_library(str(out_so), [str(JAVA_GRAMMAR_DIR)])
    try:
        lib = ctypes.CDLL(str(out_so))
        symbol = getattr(lib, "tree_sitter_java")
        symbol.restype = ctypes.c_void_p
        ptr = symbol()
        _TS_LIB_HANDLES[str(out_so)] = lib
        return Language(ptr, "java")
    except Exception:
        return Language(str(out_so), "java")


def build_java_parser(*, out_so: Path) -> Parser:
    java = _build_java_language(out_so=out_so)
    parser = Parser()
    parser.set_language(java)
    return parser


def iter_nodes(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.children):
            stack.append(child)


def _node_text(data: bytes, node: Node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _has_static_modifier(node: Node, data: bytes) -> bool:
    for child in node.children:
        if child.type != "modifiers":
            continue
        for mod in child.children:
            if mod.type == "marker_annotation":
                continue
            if _node_text(data, mod).strip() == "static":
                return True
    return False


def _first_identifier(node: Node, data: bytes) -> str | None:
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return _node_text(data, child)
    return None


def parse_static_members(path: Path, *, parser: Parser) -> list[StaticMember]:
    data = path.read_bytes()
    tree = parser.parse(data)
    out: list[StaticMember] = []
    for node in iter_nodes(tree.root_node):
        if node.type not in ("method_declaration", "field_declaration"):
            continue
        if not _has_static_modifier(node, data):
            continue
        if node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            name = _node_text(data, name_node) if name_node is not None else None
            if not name:
                for child in node.children:
                    if child.type == "identifier":
                        name = _node_text(data, child)
                        break
            if not name:
                continue
            out.append(
                StaticMember(
                    kind="method",
                    name=name,
                    start=node.start_byte,
                    end=node.end_byte,
                    text=data[node.start_byte : node.end_byte].decode("utf-8", errors="replace"),
                )
            )
            continue
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name = _first_identifier(child, data)
            if not name:
                continue
            out.append(
                StaticMember(
                    kind="field",
                    name=name,
                    start=node.start_byte,
                    end=node.end_byte,
                    text=data[node.start_byte : node.end_byte].decode("utf-8", errors="replace"),
                )
            )
    return out


def _parse_yaml_like_manifest(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("source:"):
            out["source"] = line.split(":", 1)[1].strip().strip("'\"")
            i += 1
            continue
        if line.startswith("target_file:"):
            out["target_file"] = line.split(":", 1)[1].strip().strip("'\"")
            i += 1
            continue
        if line.startswith("target_class:"):
            out["target_class"] = line.split(":", 1)[1].strip().strip("'\"")
            i += 1
            continue
        if line.startswith("moves:"):
            i += 1
            moves: list[dict[str, str]] = []
            current: dict[str, str] | None = None
            while i < len(lines):
                sub = lines[i]
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
                                k, v = part.split(":", 1)
                                current[k.strip()] = v.strip().strip("'\"")
                    i += 1
                    continue
                if current is not None and ":" in stripped:
                    k, v = stripped.split(":", 1)
                    current[k.strip()] = v.strip().strip("'\"")
                i += 1
            if current:
                moves.append(current)
            out["moves"] = moves
            continue
        i += 1
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
        if kind not in ("method", "field") or not name:
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
    return [load_manifest(p) for p in files]


def apply_non_string_comment_replacements(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    mode = "code"
    segment_start = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if mode == "code":
            if ch == "/" and nxt == "/":
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "block_comment"
                i += 2
                continue
            if ch == '"':
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "string"
                i += 1
                continue
            if ch == "'":
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "char"
                i += 1
                continue
            i += 1
            continue
        if mode == "line_comment":
            if ch == "\n":
                out.append(text[segment_start : i + 1])
                segment_start = i + 1
                mode = "code"
            i += 1
            continue
        if mode == "block_comment":
            if ch == "*" and nxt == "/":
                i += 2
                out.append(text[segment_start:i])
                segment_start = i
                mode = "code"
                continue
            i += 1
            continue
        if mode == "string":
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                i += 1
                out.append(text[segment_start:i])
                segment_start = i
                mode = "code"
                continue
            i += 1
            continue
        if mode == "char":
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                i += 1
                out.append(text[segment_start:i])
                segment_start = i
                mode = "code"
                continue
            i += 1
            continue
    tail = text[segment_start:]
    if mode == "code":
        for rx, repl in patterns:
            tail = rx.sub(repl, tail)
    out.append(tail)
    return "".join(out)
