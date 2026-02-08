from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from tree_sitter import Node, Parser

from tools.refactor.rename.models import CachedIndexes, ClassIndex


def iter_nodes(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.children):
            stack.append(child)


def node_text(data: bytes, node: Node) -> str:
    try:
        return data[node.start_byte : node.end_byte].decode("utf-8")
    except UnicodeDecodeError:
        return ""


def normalize_type_name(type_text: str) -> str:
    text = type_text.strip()
    if not text:
        return ""
    text = re.sub(r"\b(final|static|volatile|transient)\b", "", text).strip()
    if "<" in text:
        text = text.split("<", 1)[0].strip()
    if text.endswith("..."):
        text = text[:-3].strip()
    while text.endswith("[]"):
        text = text[:-2].strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1].strip()
    return text


def collect_field_declarations_from_tree(*, data: bytes, root: Node, path: Path) -> dict[str, set[Path]]:
    out: dict[str, set[Path]] = {}
    for node in iter_nodes(root):
        if node.type != "variable_declarator":
            continue
        parent = node.parent
        if parent is None or parent.type != "field_declaration":
            continue
        ident = None
        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                ident = data[child.start_byte : child.end_byte]
                break
        if ident is None:
            continue
        try:
            name = ident.decode("utf-8")
        except UnicodeDecodeError:
            continue
        out.setdefault(name, set()).add(path.resolve())
    return out


def build_indexes(*, parser: Parser, java_files: list[Path]) -> CachedIndexes:
    field_decls: dict[str, set[Path]] = {}
    fields: dict[str, dict[str, str]] = {}
    extends_of: dict[str, str] = {}
    methods: dict[str, dict[str, str]] = {}
    implements_of: dict[str, list[str]] = {}

    for path in java_files:
        data = path.read_bytes()
        tree = parser.parse(data)
        per_file = collect_field_declarations_from_tree(data=data, root=tree.root_node, path=path)
        for name, ps in per_file.items():
            field_decls.setdefault(name, set()).update(ps)

        class_name = ""
        superclass_name = ""
        interface_names: list[str] = []
        for node in iter_nodes(tree.root_node):
            if node.type not in ("class_declaration", "interface_declaration", "enum_declaration"):
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            class_name = node_text(data, name_node).strip()
            if node.type == "class_declaration":
                superclass_node = node.child_by_field_name("superclass")
                if superclass_node is not None:
                    raw = node_text(data, superclass_node).strip()
                    raw = re.sub(r"^\s*extends\s+", "", raw).strip()
                    superclass_name = normalize_type_name(raw)
            interfaces_node = node.child_by_field_name("interfaces") or node.child_by_field_name("super_interfaces")
            if interfaces_node is not None:
                raw = node_text(data, interfaces_node).strip()
                raw = re.sub(r"^\s*(?:implements|extends)\s+", "", raw).strip()
                parts = [p.strip() for p in raw.split(",") if p.strip()]
                interface_names = [normalize_type_name(p) for p in parts if normalize_type_name(p)]
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

        for node in iter_nodes(tree.root_node):
            if node.type != "field_declaration":
                continue
            type_node = node.child_by_field_name("type")
            if type_node is None:
                continue
            type_name = normalize_type_name(node_text(data, type_node))
            if not type_name:
                continue
            for child in node.children:
                if child.type != "variable_declarator":
                    continue
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                field_name = node_text(data, name_node).strip()
                if field_name:
                    class_fields[field_name] = type_name

        for node in iter_nodes(tree.root_node):
            if node.type != "method_declaration":
                continue
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            if name_node is None or type_node is None:
                continue
            method_name = node_text(data, name_node).strip()
            return_type = normalize_type_name(node_text(data, type_node))
            if method_name and return_type and method_name not in class_methods:
                class_methods[method_name] = return_type

    return CachedIndexes(
        field_decls=field_decls,
        class_index=ClassIndex(fields=fields, extends_of=extends_of, methods=methods, implements_of=implements_of),
    )


def iter_owner_closure(type_name: str, class_index: ClassIndex) -> Iterable[str]:
    seen: set[str] = set()
    queue: list[str] = [type_name]
    while queue:
        current = queue.pop(0)
        if not current or current in seen:
            continue
        seen.add(current)
        yield current
        sup = class_index.extends_of.get(current, "")
        if sup and sup not in seen:
            queue.append(sup)
        for iface in class_index.implements_of.get(current, []):
            if iface and iface not in seen:
                queue.append(iface)
        for iface2 in class_index.implements_of.get(current, []):
            for child in class_index.implements_of.get(iface2, []):
                if child and child not in seen:
                    queue.append(child)
