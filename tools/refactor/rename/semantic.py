from __future__ import annotations

import re

from tree_sitter import Node

from tools.refactor.rename.index import iter_nodes, iter_owner_closure, node_text, normalize_type_name
from tools.refactor.rename.models import ClassIndex, MethodRename, TypeEnv


JAVA_IDENT_RX = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def parse_signature_types(signature_text: str) -> tuple[str, ...] | None:
    text = (signature_text or "").strip()
    if not text:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if not text:
        return ()
    parts = [part.strip() for part in text.split(",")]
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        part = re.sub(r"@\w+(?:\\([^)]*\\))?\\s*", "", part)
        part = re.sub(r"\\bfinal\\b", "", part)
        part = re.sub(r"\\s+", " ", part).strip()
        tokens = part.split(" ")
        if len(tokens) >= 2 and JAVA_IDENT_RX.match(tokens[-1]):
            part = " ".join(tokens[:-1]).strip()
        part = part.replace("...", "").strip()
        type_name = normalize_type_name(part)
        if type_name:
            out.append(type_name)
    return tuple(out)


def is_field_declarator_name_node(node: Node) -> bool:
    parent = node.parent
    if parent is None or parent.type != "variable_declarator":
        return False
    grand_parent = parent.parent
    if grand_parent is None or grand_parent.type != "field_declaration":
        return False
    name_node = parent.child_by_field_name("name")
    return name_node is not None and name_node.id == node.id


def enclosing_member_name(data: bytes, node: Node) -> str:
    current: Node | None = node
    while current is not None:
        if current.type in ("method_declaration", "constructor_declaration"):
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                return node_text(data, name_node).strip()
            return ""
        current = current.parent
    return ""


def collect_method_env(*, data: bytes, class_index: ClassIndex, current_class: str, scope_node: Node) -> TypeEnv:
    env: dict[str, str] = {}
    local_names: set[str] = set()
    for field_name, field_type in class_index.fields.get(current_class, {}).items():
        env.setdefault(field_name, field_type)
    env["this"] = current_class

    for node in iter_nodes(scope_node):
        if node.type == "formal_parameter":
            type_node = node.child_by_field_name("type")
            name_node = node.child_by_field_name("name")
            if type_node is None or name_node is None:
                continue
            type_name = normalize_type_name(node_text(data, type_node))
            var_name = node_text(data, name_node).strip()
            if type_name and var_name:
                env[var_name] = type_name
                local_names.add(var_name)
        elif node.type == "catch_formal_parameter":
            type_node = node.child_by_field_name("type")
            name_node = node.child_by_field_name("name")
            if type_node is None or name_node is None:
                continue
            type_name = normalize_type_name(node_text(data, type_node))
            var_name = node_text(data, name_node).strip()
            if type_name and var_name:
                env[var_name] = type_name
                local_names.add(var_name)
        elif node.type == "local_variable_declaration":
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
                var_name = node_text(data, name_node).strip()
                if var_name:
                    env[var_name] = type_name
                    local_names.add(var_name)
        elif node.type == "enhanced_for_statement":
            var_node = node.child_by_field_name("variable")
            if var_node is None:
                continue
            type_node = var_node.child_by_field_name("type")
            name_node = var_node.child_by_field_name("name")
            if type_node is None or name_node is None:
                continue
            type_name = normalize_type_name(node_text(data, type_node))
            var_name = node_text(data, name_node).strip()
            if type_name and var_name:
                env[var_name] = type_name
                local_names.add(var_name)
    return TypeEnv(names=env, local_names=frozenset(local_names), current_class=current_class)


def resolve_expr_type(*, data: bytes, class_index: ClassIndex, env: TypeEnv, expr: Node | None) -> str:
    return _resolve_expr_type(
        data=data,
        class_index=class_index,
        env=env,
        expr=expr,
        owner_closure_cache=None,
    )


def _resolve_expr_type(
    *,
    data: bytes,
    class_index: ClassIndex,
    env: TypeEnv,
    expr: Node | None,
    owner_closure_cache: dict[str, tuple[str, ...]] | None,
) -> str:
    def _owners(type_name: str) -> tuple[str, ...]:
        if owner_closure_cache is None:
            return tuple(iter_owner_closure(type_name, class_index))
        cached = owner_closure_cache.get(type_name)
        if cached is None:
            cached = tuple(iter_owner_closure(type_name, class_index))
            owner_closure_cache[type_name] = cached
        return cached

    if expr is None:
        return env.current_class
    expr_type = expr.type
    if expr_type in ("identifier", "type_identifier"):
        name = node_text(data, expr).strip()
        if not name:
            return ""
        if name in env.names:
            return env.names[name]
        if (
            name in class_index.fields
            or name in class_index.methods
            or name in class_index.extends_of
            or name in class_index.implements_of
            or name in class_index.extends_of.values()
            or any(name in value for value in class_index.implements_of.values())
        ):
            return name
        return ""
    if expr_type == "this":
        return env.current_class
    if expr_type == "field_access":
        obj = expr.child_by_field_name("object")
        field = expr.child_by_field_name("field")
        if field is None:
            return ""
        field_name = node_text(data, field).strip()
        if not field_name:
            return ""
        owner_type = _resolve_expr_type(
            data=data,
            class_index=class_index,
            env=env,
            expr=obj,
            owner_closure_cache=owner_closure_cache,
        )
        if not owner_type:
            owner_type = env.current_class
        for owner in _owners(owner_type):
            field_type = class_index.fields.get(owner, {}).get(field_name, "")
            if field_type:
                return field_type
        return ""
    if expr_type == "parenthesized_expression":
        inner = expr.child_by_field_name("expression")
        if inner is None:
            inner = expr.named_children[0] if getattr(expr, "named_children", None) else None
        return _resolve_expr_type(
            data=data,
            class_index=class_index,
            env=env,
            expr=inner,
            owner_closure_cache=owner_closure_cache,
        )
    if expr_type == "cast_expression":
        type_node = expr.child_by_field_name("type")
        if type_node is None:
            return ""
        return normalize_type_name(node_text(data, type_node))
    if expr_type == "object_creation_expression":
        type_node = expr.child_by_field_name("type")
        if type_node is None:
            return ""
        return normalize_type_name(node_text(data, type_node))
    if expr_type == "method_invocation":
        name_node = expr.child_by_field_name("name")
        if name_node is None:
            return ""
        method_name = node_text(data, name_node).strip()
        if not method_name:
            return ""
        obj = expr.child_by_field_name("object")
        recv_type = _resolve_expr_type(
            data=data,
            class_index=class_index,
            env=env,
            expr=obj,
            owner_closure_cache=owner_closure_cache,
        )
        if not recv_type:
            recv_type = env.current_class
        for owner in _owners(recv_type):
            return_type = class_index.methods.get(owner, {}).get(method_name, "")
            if return_type:
                return return_type
        return ""
    if expr_type == "scoped_identifier":
        return normalize_type_name(node_text(data, expr))
    if expr_type == "array_access":
        array_node = expr.child_by_field_name("array")
        if array_node is None and expr.named_children:
            array_node = expr.named_children[0]
        return _resolve_expr_type(
            data=data,
            class_index=class_index,
            env=env,
            expr=array_node,
            owner_closure_cache=owner_closure_cache,
        )
    return ""


def _iter_args(node: Node) -> list[Node]:
    args = node.child_by_field_name("arguments")
    if args is None:
        for child in node.children:
            if child.type == "argument_list":
                args = child
                break
    if args is None:
        return []
    return list(getattr(args, "named_children", []) or [])


def _resolve_literal_type(expr: Node) -> str:
    expr_type = expr.type
    if expr_type in ("true", "false"):
        return "boolean"
    if expr_type == "string_literal":
        return "String"
    if expr_type == "character_literal":
        return "char"
    if expr_type == "null_literal":
        return ""
    if expr_type.endswith("_integer_literal"):
        return "int"
    if expr_type.endswith("_floating_point_literal"):
        return "float"
    return ""


def _resolve_arg_type(
    *,
    data: bytes,
    class_index: ClassIndex,
    env: TypeEnv,
    expr: Node,
    owner_closure_cache: dict[str, tuple[str, ...]] | None,
) -> str:
    literal_type = _resolve_literal_type(expr)
    if literal_type:
        return literal_type
    return _resolve_expr_type(
        data=data,
        class_index=class_index,
        env=env,
        expr=expr,
        owner_closure_cache=owner_closure_cache,
    )


def pick_method_rename(*, candidates: list[MethodRename], arg_types: list[str]) -> str:
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0].new
    unique_new = {candidate.new for candidate in candidates}
    if len(unique_new) == 1:
        return next(iter(unique_new))

    argc = len(arg_types)
    exact: list[MethodRename] = []
    partial: list[MethodRename] = []
    wildcard: list[MethodRename] = []
    for candidate in candidates:
        if candidate.signature_types is None:
            wildcard.append(candidate)
            continue
        if len(candidate.signature_types) != argc:
            continue
        ok = True
        all_known = True
        for want, got in zip(candidate.signature_types, arg_types, strict=False):
            if not got:
                all_known = False
                continue
            if got != want:
                ok = False
                break
        if not ok:
            continue
        if all_known:
            exact.append(candidate)
        else:
            partial.append(candidate)
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


def rename_member_accesses(
    *,
    data: bytes,
    class_index: ClassIndex,
    env: TypeEnv,
    scope_node: Node,
    field_map: dict[tuple[str, str], str],
    method_map: dict[tuple[str, str], list[MethodRename]],
    skip_unqualified_identifiers: bool = False,
    owner_closure_cache: dict[str, tuple[str, ...]] | None = None,
) -> list[tuple[int, int, bytes]]:
    def _owners(type_name: str) -> tuple[str, ...]:
        if owner_closure_cache is None:
            return tuple(iter_owner_closure(type_name, class_index))
        cached = owner_closure_cache.get(type_name)
        if cached is None:
            cached = tuple(iter_owner_closure(type_name, class_index))
            owner_closure_cache[type_name] = cached
        return cached

    edits: list[tuple[int, int, bytes]] = []
    scope_is_static = False
    if scope_node.type == "method_declaration":
        for child in scope_node.children:
            if child.type != "modifiers":
                continue
            for mod in child.children:
                if node_text(data, mod).strip() == "static":
                    scope_is_static = True
                    break
            if scope_is_static:
                break

    def _is_decl_name(node: Node) -> bool:
        parent = node.parent
        if parent is None:
            return False
        name_node = parent.child_by_field_name("name")
        return name_node is not None and name_node.id == node.id

    def _is_known_type_name(name: str) -> bool:
        return (
            name in class_index.fields
            or name in class_index.methods
            or name in class_index.extends_of
            or name in class_index.implements_of
            or name in class_index.extends_of.values()
            or any(name in value for value in class_index.implements_of.values())
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
            for owner in _owners(owner_type):
                next_type = class_index.fields.get(owner, {}).get(field_name, "")
                if next_type:
                    break
            if not next_type:
                return ""
            owner_type = next_type
        return owner_type

    for node in iter_nodes(scope_node):
        if node.type == "identifier":
            if skip_unqualified_identifiers:
                continue
            if _is_decl_name(node):
                continue
            old = node_text(data, node).strip()
            if not old or old in env.local_names:
                continue
            new = ""
            for owner in _owners(env.current_class):
                new = field_map.get((owner, old), "")
                if new:
                    break
            if new and new != old:
                replacement = new
                if new in env.local_names and not scope_is_static:
                    replacement = f"this.{new}"
                edits.append((node.start_byte, node.end_byte, replacement.encode("utf-8")))
                continue

        if node.type == "cast_expression":
            type_node = node.child_by_field_name("type")
            if type_node is not None and type_node.type == "scoped_type_identifier":
                chain_text = node_text(data, type_node).strip()
                if "." in chain_text:
                    parts = [part.strip() for part in chain_text.split(".") if part.strip()]
                    if len(parts) >= 2:
                        receiver_type = _resolve_chain_type(parts[:-1])
                        leaf = parts[-1]
                        if receiver_type:
                            new_leaf = ""
                            for owner in _owners(receiver_type):
                                new_leaf = field_map.get((owner, leaf), "")
                                if new_leaf:
                                    break
                                picked = pick_method_rename(
                                    candidates=method_map.get((owner, leaf), []),
                                    arg_types=[],
                                )
                                if picked:
                                    new_leaf = picked
                                    break
                            if new_leaf and new_leaf != leaf:
                                last_ident: Node | None = None
                                for child in iter_nodes(type_node):
                                    if child.type in ("identifier", "type_identifier"):
                                        last_ident = child
                                if last_ident is not None:
                                    edits.append((last_ident.start_byte, last_ident.end_byte, new_leaf.encode("utf-8")))
                                    continue

        if node.type == "field_access":
            obj = node.child_by_field_name("object")
            field = node.child_by_field_name("field")
            if field is None:
                continue
            old = node_text(data, field).strip()
            if not old:
                continue
            owner_type = _resolve_expr_type(
                data=data,
                class_index=class_index,
                env=env,
                expr=obj,
                owner_closure_cache=owner_closure_cache,
            )
            if not owner_type:
                continue
            new = ""
            for owner in _owners(owner_type):
                new = field_map.get((owner, old), "")
                if new:
                    break
            if new and new != old:
                edits.append((field.start_byte, field.end_byte, new.encode("utf-8")))
                continue

        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            old = node_text(data, name_node).strip()
            if not old:
                continue
            obj = node.child_by_field_name("object")
            owner_type = _resolve_expr_type(
                data=data,
                class_index=class_index,
                env=env,
                expr=obj,
                owner_closure_cache=owner_closure_cache,
            )
            if not owner_type:
                owner_type = env.current_class
            arg_exprs = _iter_args(node)
            arg_types = [
                _resolve_arg_type(
                    data=data,
                    class_index=class_index,
                    env=env,
                    expr=arg,
                    owner_closure_cache=owner_closure_cache,
                )
                for arg in arg_exprs
            ]
            new = ""
            for owner in _owners(owner_type):
                picked = pick_method_rename(candidates=method_map.get((owner, old), []), arg_types=arg_types)
                if picked:
                    new = picked
                    break
            if new and new != old:
                edits.append((name_node.start_byte, name_node.end_byte, new.encode("utf-8")))
                continue

        if node.type in ("scoped_identifier", "scoped_type_identifier"):
            chain_text = node_text(data, node).strip()
            if "." not in chain_text:
                continue
            parts = [part.strip() for part in chain_text.split(".") if part.strip()]
            if len(parts) < 2:
                continue
            id_nodes: list[Node] = [child for child in iter_nodes(node) if child.type in ("identifier", "type_identifier")]
            if not id_nodes:
                continue
            head = parts[0]
            if head not in env.local_names:
                new_head = ""
                for owner in _owners(env.current_class):
                    new_head = field_map.get((owner, head), "")
                    if new_head:
                        break
                if new_head and new_head != head:
                    replacement = new_head
                    if new_head in env.local_names and not scope_is_static:
                        replacement = f"this.{new_head}"
                    edits.append((id_nodes[0].start_byte, id_nodes[0].end_byte, replacement.encode("utf-8")))
            receiver_type = _resolve_chain_type(parts[:-1])
            if receiver_type:
                leaf = parts[-1]
                new_leaf = ""
                for owner in _owners(receiver_type):
                    new_leaf = field_map.get((owner, leaf), "")
                    if new_leaf:
                        break
                if new_leaf and new_leaf != leaf:
                    edits.append((id_nodes[-1].start_byte, id_nodes[-1].end_byte, new_leaf.encode("utf-8")))
            continue
    return edits
