from __future__ import annotations

import ctypes
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tree_sitter import Language, Node, Parser  # type: ignore


JAVA_GRAMMAR_DIR = Path("tools/vendor/tree-sitter-java")
_TS_LIB_HANDLES: dict[str, ctypes.CDLL] = {}


def build_java_language(*, out_so: Path) -> Any:
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
    java = build_java_language(out_so=out_so)
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


def node_text(data: bytes, node: Node) -> str:
    return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

