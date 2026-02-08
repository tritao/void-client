from __future__ import annotations

import dataclasses
from pathlib import Path

from tree_sitter import Parser  # type: ignore

from tools.refactor.common.ts_java import iter_nodes, node_text


@dataclasses.dataclass(frozen=True)
class StaticMember:
    kind: str
    name: str
    start: int
    end: int
    text: str


def _has_static_modifier(node, data: bytes) -> bool:
    for child in node.children:
        if child.type != "modifiers":
            continue
        for mod in child.children:
            if mod.type == "marker_annotation":
                continue
            if node_text(data, mod).strip() == "static":
                return True
    return False


def _first_identifier(node, data: bytes) -> str | None:
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return node_text(data, child)
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
            name = node_text(data, name_node) if name_node is not None else None
            if not name:
                for child in node.children:
                    if child.type == "identifier":
                        name = node_text(data, child)
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

