#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import REFACTOR_PLAN_GENERATED_DIR, REFACTOR_SRC_DIR


METHOD_DECL_RX = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|native|synchronized|abstract|strictfp)\s+)*"
    r"(?:<[^>]+>\s+)?(?:[\w\[\]<>.$]+\s+)+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\((?P<params>[^)]*)\)\s*\{"
)
CLASS_DECL_RX = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b")
CLASS_DECL_EXTENDS_RX = re.compile(
    r"\bclass\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)(?:\s+extends\s+(?P<extends>[A-Za-z_$][A-Za-z0-9_$.]*))?"
)
STATIC_FIELD_DECL_RX = re.compile(
    r"^\s*(?:(?:public|protected|private|final|transient|volatile)\s+)*static\s+"
    r"(?P<type>[\w<>\[\], ?$.]+?)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:=\s*[^;]*)?;\s*$"
)
GUARD_IF_RX = re.compile(
    r"^\s*if\s*\(\s*(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?P<op>!=|==|<=|>=|<|>)\s*(?P<const>(?:-?\d+|true|false|null))\s*\)\s*(?P<body>.*)$"
)
TOKEN_TEMPLATE = r"\b{token}\b"
TOKEN_RX = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b")
CALL_HEAD_RX = re.compile(r"(?:(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*)?(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
OWNER_EXPR_TRAIL_RX = re.compile(
    r"(?P<owner>(?:this|super|[A-Za-z_$][A-Za-z0-9_$]*)(?:\s*\([^()]*\))?(?:\s*\[[^\[\]]+\])*(?:\s*\.\s*(?:[A-Za-z_$][A-Za-z0-9_$]*)(?:\s*\([^()]*\))?(?:\s*\[[^\[\]]+\])*)*)\s*\.\s*$"
)
METHOD_DECL_RET_RX = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|native|synchronized|abstract|strictfp)\s+)*"
    r"(?:<[^>]+>\s+)?(?P<ret>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\((?P<params>[^)]*)\)\s*\{"
)
FIELD_DECL_RX = re.compile(
    r"^\s*(?:(?:public|protected|private|static|final|transient|volatile)\s+)*"
    r"(?P<type>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+(?P<rest>[^;]+);"
)
LOCAL_DECL_RX = re.compile(
    r"^\s*(?:(?:final)\s+)?(?P<type>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+(?P<rest>[^;]+);"
)
ASSIGN_NEW_RX = re.compile(
    r"\b(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new\s+(?P<type>[A-Za-z_$][A-Za-z0-9_$.]*)\b"
)
ASSIGN_CAST_RX = re.compile(
    r"\b(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\(\s*(?P<type>[A-Za-z_$][A-Za-z0-9_$.]*)\s*\)"
)
ASSIGN_VAR_RX = re.compile(
    r"\b(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?P<src>[A-Za-z_$][A-Za-z0-9_$]*)\s*;"
)
ASSIGN_THIS_FIELD_RX = re.compile(
    r"\b(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*this\s*\.\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\s*;"
)
ASSIGN_STATIC_FIELD_RX = re.compile(
    r"\b(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?P<owner>[A-Za-z_$][A-Za-z0-9_$.]*)\s*\.\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\s*;"
)
ENHANCED_FOR_RX = re.compile(
    r"for\s*\(\s*(?:(?:final)\s+)?(?P<type>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*:"
)
FOR_INIT_RX = re.compile(
    r"for\s*\(\s*(?:(?:final)\s+)?(?P<type>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*="
)
INDEX_SUFFIX_RX = re.compile(r"\s*\[[^\[\]]+\]\s*$")
LEADING_CAST_RX = re.compile(r"^\(\s*(?P<type>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s*\)\s*(?P<rest>.+)$")


@dataclass(frozen=True)
class Candidate:
    id: str
    file: str
    owner: str
    member: str
    kind: str
    rule_type: str
    match: str
    dependency: str
    confidence: str
    action_hint: str
    notes: str
    gate_status: str = ""
    gate_reason: str = ""


@dataclass(frozen=True)
class MethodContext:
    owner: str
    name: str
    params: tuple[str, ...]
    lines: list[tuple[int, str]]
    brace_balance: int


@dataclass(frozen=True)
class GuardParamRoot:
    candidate_id: str
    file: str
    owner: str
    method: str
    arity: int
    param_index: int
    op: str
    const_raw: str


@dataclass(frozen=True)
class MethodDeclMeta:
    file: str
    owner: str
    method: str
    arity: int
    visibility: str
    is_static: bool
    is_final: bool


@dataclass
class CallArgStats:
    total_calls: int = 0
    literal_calls: int = 0
    nonliteral_calls: int = 0
    literal_values: set[int | bool | None] | None = None

    def __post_init__(self) -> None:
        if self.literal_values is None:
            self.literal_values = set()


def _parse_decl_modifiers(line: str, method_name: str) -> tuple[str, bool, bool]:
    prefix = line.split(method_name, 1)[0]
    tokens = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", prefix))
    if "private" in tokens:
        visibility = "private"
    elif "protected" in tokens:
        visibility = "protected"
    elif "public" in tokens:
        visibility = "public"
    else:
        visibility = "package"
    return visibility, ("static" in tokens), ("final" in tokens)


def _split_params(params_raw: str) -> tuple[str, ...]:
    params = []
    for part in params_raw.split(","):
        value = part.strip()
        if not value:
            continue
        value = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", value).strip()
        token = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*$", value)
        if token:
            params.append(token.group(1))
    return tuple(params)


def _normalize_type_name(type_text: str) -> str:
    value = (type_text or "").strip()
    if not value:
        return ""
    value = re.sub(r"<[^>]*>", "", value)
    value = value.replace("[]", "").replace("...", "").strip()
    value = value.split(".")[-1].strip()
    return value


def _canonicalize_class_name(name: str, class_aliases: dict[str, str]) -> str:
    cur = _normalize_type_name(name)
    if not cur:
        return ""
    seen: set[str] = set()
    while cur in class_aliases and cur not in seen:
        seen.add(cur)
        nxt = _normalize_type_name(class_aliases[cur])
        if not nxt or nxt == cur:
            break
        cur = nxt
    return cur


def _split_params_typed(params_raw: str, class_aliases: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _split_top_level_args(params_raw):
        value = part.strip()
        if not value:
            continue
        value = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", value).strip()
        m = re.search(
            r"(?:(?:final)\s+)?(?P<type>[A-Za-z_$][A-Za-z0-9_$.<>\[\]]*)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)$",
            value,
        )
        if not m:
            continue
        out[m.group("name")] = _canonicalize_class_name(m.group("type"), class_aliases)
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _token_rx(token: str) -> re.Pattern[str]:
    return re.compile(TOKEN_TEMPLATE.format(token=re.escape(token)))


def _load_class_aliases(src_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    plan_csv = src_dir / ".refactor-plan" / "classes.class_rename.csv"
    if not plan_csv.exists():
        return out
    try:
        with plan_csv.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                old = _normalize_type_name(row.get("old", "") or row.get("src", "") or "")
                new = _normalize_type_name(row.get("new", "") or row.get("dst", "") or "")
                if old and new:
                    out[old] = new
    except OSError:
        return out
    return out


def _load_guard_promotion_allowlist(src_dir: Path) -> list[dict[str, str]]:
    path = src_dir / ".refactor-plan" / "guard_promotion_allowlist.csv"
    if not path.exists():
        return []
    out: list[dict[str, str]] = []
    try:
        lines = []
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            lines.append(raw)
        if not lines:
            return []
        reader = csv.DictReader(lines)
        for row in reader:
            owner = (row.get("owner") or "").strip()
            method = (row.get("method") or "").strip()
            if not owner or not method:
                continue
            out.append(
                {
                    "file": (row.get("file") or "").strip(),
                    "owner": owner,
                    "method": method,
                    "reason": (row.get("reason") or "").strip(),
                }
            )
    except OSError:
        return []
    return out


def _load_guard_promotion_denylist(src_dir: Path) -> list[dict[str, str]]:
    path = src_dir / ".refactor-plan" / "guard_promotion_denylist.csv"
    if not path.exists():
        return []
    out: list[dict[str, str]] = []
    try:
        lines = []
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            lines.append(raw)
        if not lines:
            return []
        reader = csv.DictReader(lines)
        for row in reader:
            owner = (row.get("owner") or "").strip()
            method = (row.get("method") or "").strip()
            if not owner or not method:
                continue
            out.append(
                {
                    "file": (row.get("file") or "").strip(),
                    "owner": owner,
                    "method": method,
                    "reason": (row.get("reason") or "").strip(),
                }
            )
    except OSError:
        return []
    return out


def _is_guard_allowlisted(
    *,
    file: str,
    owner: str,
    method: str,
    reason: str,
    allowlist: list[dict[str, str]],
) -> bool:
    if not allowlist:
        return False
    for row in allowlist:
        if row.get("owner", "") != owner:
            continue
        if row.get("method", "") != method:
            continue
        row_file = row.get("file", "")
        if row_file and row_file != file:
            continue
        row_reason = row.get("reason", "")
        if row_reason and row_reason != reason:
            continue
        return True
    return False


def _guard_deny_reason(
    *,
    file: str,
    owner: str,
    method: str,
    denylist: list[dict[str, str]],
) -> str:
    if not denylist:
        return ""
    for row in denylist:
        if row.get("owner", "") != owner:
            continue
        if row.get("method", "") != method:
            continue
        row_file = row.get("file", "")
        if row_file and row_file != file:
            continue
        return row.get("reason", "") or "denylisted"
    return ""


def _is_simple_guard_line(line: str, param_name: str) -> bool:
    m = GUARD_IF_RX.match(line)
    return bool(m and m.group("var") == param_name)


def _split_top_level_args(args_text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(args_text):
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(args_text[start:i].strip())
            start = i + 1
    tail = args_text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _find_matching_paren(text: str, open_idx: int) -> int:
    depth = 1
    i = open_idx + 1
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _iter_calls_in_line(line: str) -> list[tuple[str, str, str, list[str]]]:
    out: list[tuple[str, str, str, list[str]]] = []
    i = 0
    while i < len(line):
        m = CALL_HEAD_RX.search(line, i)
        if not m:
            break
        method = m.group("method") or ""
        owner = (m.group("owner") or "").strip()
        owner_expr = owner
        prefix = line[: m.start("method")]
        candidate_expr = _extract_owner_expr(prefix)
        if not candidate_expr:
            owner_expr_m = OWNER_EXPR_TRAIL_RX.search(prefix)
            if owner_expr_m:
                candidate_expr = (owner_expr_m.group("owner") or "").strip()
        if candidate_expr and (not owner_expr or candidate_expr.endswith(owner_expr)):
            owner_expr = candidate_expr
        if method in {"if", "for", "while", "switch", "catch", "return", "new", "throw", "synchronized"}:
            i = m.end()
            continue
        open_idx = line.find("(", m.end() - 1)
        if open_idx < 0:
            i = m.end()
            continue
        close_idx = _find_matching_paren(line, open_idx)
        if close_idx < 0:
            i = m.end()
            continue
        args_text = line[open_idx + 1 : close_idx].strip()
        args = _split_top_level_args(args_text) if args_text else []
        out.append((owner, owner_expr, method, args))
        i = close_idx + 1
    return out


def _split_owner_chain(expr: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(expr):
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "." and depth == 0:
            part = expr[start:idx].strip()
            if part:
                parts.append(part)
            start = idx + 1
    tail = expr[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_call_segment(segment: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\((.*)\)", segment.strip())
    if not m:
        return None
    method = (m.group(1) or "").strip()
    args_raw = (m.group(2) or "").strip()
    arity = 0 if not args_raw else len(_split_top_level_args(args_raw))
    return method, arity


def _extract_owner_expr(prefix: str) -> str:
    i = len(prefix) - 1
    while i >= 0 and prefix[i].isspace():
        i -= 1
    if i < 0 or prefix[i] != ".":
        return ""
    end_dot = i
    i -= 1
    depth_paren = 0
    depth_bracket = 0
    depth_brace = 0
    separators = set(";=,+-*/%&|^!<>?:")
    while i >= 0:
        ch = prefix[i]
        if ch == ")":
            depth_paren += 1
        elif ch == "(":
            if depth_paren == 0:
                break
            depth_paren -= 1
        elif ch == "]":
            depth_bracket += 1
        elif ch == "[":
            if depth_bracket == 0:
                break
            depth_bracket -= 1
        elif ch == "}":
            depth_brace += 1
        elif ch == "{":
            if depth_brace == 0:
                break
            depth_brace -= 1
        elif depth_paren == 0 and depth_bracket == 0 and depth_brace == 0 and ch in separators:
            break
        i -= 1
    owner_expr = prefix[i + 1 : end_dot].strip()
    return owner_expr


def _strip_index_suffix(value: str) -> str:
    out = value.strip()
    while True:
        nxt = INDEX_SUFFIX_RX.sub("", out)
        if nxt == out:
            break
        out = nxt.strip()
    return out


def _strip_wrapping_parentheses(value: str) -> str:
    out = value.strip()
    while out.startswith("(") and out.endswith(")"):
        depth = 0
        balanced = True
        encloses_all = False
        for idx, ch in enumerate(out):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
                if depth == 0:
                    encloses_all = idx == len(out) - 1
                    if not encloses_all:
                        balanced = False
                        break
        if not balanced or not encloses_all:
            break
        out = out[1:-1].strip()
    return out


def _strip_casts(value: str) -> str:
    out = value.strip()
    # Drop simple Java casts repeatedly: "(byte) -80", "(int) (x)".
    cast_rx = re.compile(r"^\(\s*[A-Za-z_$][A-Za-z0-9_$\[\]]*\s*\)\s*(.+)$")
    while True:
        m = cast_rx.match(out)
        if not m:
            break
        out = m.group(1).strip()
    return out


def _parse_literal_value(value: str) -> int | bool | None | object:
    text = _strip_casts(value).strip()
    if not text:
        return object()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        try:
            return int(text, 10)
        except ValueError:
            return object()
    if re.fullmatch(r"[-+]?0x[0-9a-fA-F]+", text):
        sign = -1 if text.startswith("-") else 1
        body = text[1:] if text[0] in "+-" else text
        try:
            return sign * int(body, 16)
        except ValueError:
            return object()
    return object()


def _eval_guard(op: str, const_value: int | bool | None, arg_value: int | bool | None) -> bool | None:
    if isinstance(const_value, bool) != isinstance(arg_value, bool) and not (
        isinstance(const_value, int) and isinstance(arg_value, int)
    ):
        return None
    if const_value is None or arg_value is None:
        if op == "==":
            return const_value is arg_value
        if op == "!=":
            return const_value is not arg_value
        return None
    try:
        if op == "==":
            return arg_value == const_value
        if op == "!=":
            return arg_value != const_value
        if op == "<":
            return arg_value < const_value  # type: ignore[operator]
        if op == "<=":
            return arg_value <= const_value  # type: ignore[operator]
        if op == ">":
            return arg_value > const_value  # type: ignore[operator]
        if op == ">=":
            return arg_value >= const_value  # type: ignore[operator]
    except TypeError:
        return None
    return None


def _detect_root_candidates(
    src_dir: Path,
) -> tuple[
    list[Candidate],
    dict[str, str],
    list[GuardParamRoot],
    dict[tuple[str, int], int],
    dict[tuple[str, str, int], list[MethodDeclMeta]],
    dict[tuple[str, str], int],
]:
    candidates: list[Candidate] = []
    field_owner_by_name: dict[str, str] = {}
    guard_roots: list[GuardParamRoot] = []
    method_arity_counts: dict[tuple[str, int], int] = {}
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]] = {}
    owner_method_counts: dict[tuple[str, str], int] = {}

    for java_file in sorted(src_dir.rglob("*.java")):
        rel_file = _rel(java_file, src_dir)
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()

        owner = java_file.stem
        for line in lines:
            class_match = CLASS_DECL_RX.search(line)
            if class_match:
                owner = class_match.group("name")
                break

        field_decl_lines: dict[str, int] = {}
        for idx, line in enumerate(lines, start=1):
            match = STATIC_FIELD_DECL_RX.match(line)
            if not match:
                continue
            field_name = match.group("name")
            field_owner_by_name.setdefault(field_name, owner)
            field_decl_lines[field_name] = idx

        method_ctx: MethodContext | None = None
        for idx, line in enumerate(lines, start=1):
            if method_ctx is None:
                method_match = METHOD_DECL_RX.match(line)
                if method_match:
                    method_name = method_match.group("name")
                    params = _split_params(method_match.group("params"))
                    visibility, is_static, is_final = _parse_decl_modifiers(line, method_name)
                    method_ctx = MethodContext(
                        owner=owner,
                        name=method_name,
                        params=params,
                        lines=[(idx, line)],
                        brace_balance=line.count("{") - line.count("}"),
                    )
                    arity_key = (method_ctx.name, len(method_ctx.params))
                    method_arity_counts[arity_key] = method_arity_counts.get(arity_key, 0) + 1
                    owner_method_key = (owner, method_name)
                    owner_method_counts[owner_method_key] = owner_method_counts.get(owner_method_key, 0) + 1
                    decl_key = (owner, method_name, len(params))
                    method_decl_by_key.setdefault(decl_key, []).append(
                        MethodDeclMeta(
                            file=rel_file,
                            owner=owner,
                            method=method_name,
                            arity=len(params),
                            visibility=visibility,
                            is_static=is_static,
                            is_final=is_final,
                        )
                    )
                    continue
            else:
                method_ctx.lines.append((idx, line))
                next_balance = method_ctx.brace_balance + line.count("{") - line.count("}")
                method_ctx = MethodContext(
                    owner=method_ctx.owner,
                    name=method_ctx.name,
                    params=method_ctx.params,
                    lines=method_ctx.lines,
                    brace_balance=next_balance,
                )
                if method_ctx.brace_balance <= 0:
                    lines_by_param: dict[str, list[tuple[int, str]]] = {}
                    method_decl_line = method_ctx.lines[0][0] if method_ctx.lines else -1
                    for param in method_ctx.params:
                        rx = _token_rx(param)
                        hits = [
                            (ln, text)
                            for ln, text in method_ctx.lines
                            if ln != method_decl_line and rx.search(text)
                        ]
                        lines_by_param[param] = hits

                    for ln, text in method_ctx.lines:
                        guard = GUARD_IF_RX.match(text)
                        if not guard:
                            continue
                        guard_param = guard.group("var")
                        if guard_param not in method_ctx.params:
                            continue
                        usages = lines_by_param.get(guard_param, [])
                        non_guard_uses = [
                            (use_ln, use_text)
                            for use_ln, use_text in usages
                            if not _is_simple_guard_line(use_text, guard_param)
                        ]
                        if non_guard_uses:
                            continue

                        guard_id = f"root:guard_param:{rel_file}:{method_ctx.name}:{ln}"
                        param_index = method_ctx.params.index(guard_param)
                        candidates.append(
                            Candidate(
                                id=guard_id,
                                file=rel_file,
                                owner=method_ctx.owner,
                                member=method_ctx.name,
                                kind="method",
                                rule_type="guard_param",
                                match=text.strip(),
                                dependency="",
                                confidence="high",
                                action_hint="signature_rewrites.drop_param+drop_statement_contains",
                                notes=f"Param `{guard_param}` is only used as a decompiler-style guard.",
                            )
                        )
                        guard_roots.append(
                            GuardParamRoot(
                                candidate_id=guard_id,
                                file=rel_file,
                                owner=method_ctx.owner,
                                method=method_ctx.name,
                                arity=len(method_ctx.params),
                                param_index=param_index,
                                op=guard.group("op"),
                                const_raw=guard.group("const"),
                            )
                        )
                        body = guard.group("body").strip()
                        if re.search(r"[A-Za-z_$][A-Za-z0-9_$.]*\s*\(", body):
                            candidates.append(
                                Candidate(
                                    id=f"root:guard_call:{rel_file}:{method_ctx.name}:{ln}",
                                    file=rel_file,
                                    owner=method_ctx.owner,
                                    member=method_ctx.name,
                                    kind="line_contains",
                                    rule_type="guard_call",
                                    match=text.strip(),
                                    dependency="",
                                    confidence="medium",
                                    action_hint="signature_rewrites.drop_statement_contains",
                                    notes="Guard body is a call-only side effect.",
                                )
                            )
                    method_ctx = None

        # Guard-field roots: static fields only used in guard assignment lines.
        for field_name, decl_line in field_decl_lines.items():
            rx = _token_rx(field_name)
            occurrences = [(ln, text) for ln, text in enumerate(lines, start=1) if rx.search(text) and ln != decl_line]
            if not occurrences:
                continue
            if all(re.search(rf"if\s*\([^)]*\)\s*.*\b{re.escape(field_name)}\b\s*=", text) for _, text in occurrences):
                guard_line, guard_text = occurrences[0]
                candidates.append(
                    Candidate(
                        id=f"root:guard_field:{rel_file}:{field_name}:{guard_line}",
                        file=rel_file,
                        owner=owner,
                        member=field_name,
                        kind="field",
                        rule_type="guard_field",
                        match=guard_text.strip(),
                        dependency="",
                        confidence="high",
                        action_hint="drop_members.field+drop_members.line_contains",
                        notes="Field appears to be only a guard-side-effect sink.",
                    )
                )
    return (
        candidates,
        field_owner_by_name,
        guard_roots,
        method_arity_counts,
        method_decl_by_key,
        owner_method_counts,
    )


def _detect_fallout_candidates(
    *,
    src_dir: Path,
    root_candidates: list[Candidate],
    field_owner_by_name: dict[str, str],
) -> list[Candidate]:
    fallout: list[Candidate] = []
    guard_field_roots = [c for c in root_candidates if c.rule_type == "guard_field"]
    if not guard_field_roots:
        return fallout

    root_by_field: dict[str, Candidate] = {c.member: c for c in guard_field_roots}
    guard_field_names = set(root_by_field.keys())

    for java_file in sorted(src_dir.rglob("*.java")):
        rel_file = _rel(java_file, src_dir)
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines, start=1):
            line_text = line.strip()
            line_tokens = set(TOKEN_RX.findall(line))
            for field_name in line_tokens & guard_field_names:
                root = root_by_field[field_name]
                owner = field_owner_by_name.get(field_name, root.owner)
                plain = rf"\b{re.escape(field_name)}\b"
                qualified = rf"\b{re.escape(owner)}\s*\.\s*{re.escape(field_name)}\b"
                target = rf"(?:{qualified}|{plain})"
                if re.search(rf"{target}\s*(?:\+\+|--|\+=\s*1|-=\s*1)", line):
                    fallout.append(
                        Candidate(
                            id=f"fallout:orphan_increment:{rel_file}:{idx}:{field_name}",
                            file=rel_file,
                            owner=owner,
                            member=field_name,
                            kind="line_contains",
                            rule_type="orphan_increment",
                            match=line_text,
                            dependency=root.id,
                            confidence="high",
                            action_hint="drop_members.line_contains",
                            notes="Likely orphan increment after dropping guard-only field.",
                        )
                    )
                elif re.search(rf"{target}\s*=\s*(?:0|null|false)\s*;", line):
                    fallout.append(
                        Candidate(
                            id=f"fallout:orphan_reset:{rel_file}:{idx}:{field_name}",
                            file=rel_file,
                            owner=owner,
                            member=field_name,
                            kind="line_contains",
                            rule_type="orphan_reset",
                            match=line_text,
                            dependency=root.id,
                            confidence="high",
                            action_hint="drop_members.line_contains",
                            notes="Likely orphan reset after dropping guard-only field.",
                        )
                    )
    return fallout


def _collect_class_field_types(lines: list[str], class_aliases: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        m = FIELD_DECL_RX.match(line.strip())
        if not m:
            continue
        type_name = _canonicalize_class_name(m.group("type"), class_aliases)
        if not type_name:
            continue
        rest = m.group("rest")
        for seg in _split_top_level_args(rest):
            left = seg.split("=", 1)[0].strip()
            name_m = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*$", left)
            if not name_m:
                continue
            out.setdefault(name_m.group(1), type_name)
    return out


def _update_local_type_hints(
    line: str,
    local_types: dict[str, str],
    field_types: dict[str, str],
    class_field_types_by_owner: dict[str, dict[str, str]],
    class_aliases: dict[str, str],
) -> None:
    stripped = line.strip()
    if not stripped:
        return
    if stripped.startswith(("if ", "while ", "switch ", "return ", "throw ", "new ", "case ")):
        pass
    else:
        m_decl = LOCAL_DECL_RX.match(stripped)
        if m_decl:
            type_name = _canonicalize_class_name(m_decl.group("type"), class_aliases)
            if type_name:
                rest = m_decl.group("rest")
                for seg in _split_top_level_args(rest):
                    left = seg.split("=", 1)[0].strip()
                    name_m = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*$", left)
                    if name_m:
                        local_types[name_m.group(1)] = type_name
    m_for = ENHANCED_FOR_RX.search(line) or FOR_INIT_RX.search(line)
    if m_for:
        type_name = _canonicalize_class_name(m_for.group("type"), class_aliases)
        name = (m_for.group("name") or "").strip()
        if type_name and name:
            local_types[name] = type_name

    for m in ASSIGN_NEW_RX.finditer(line):
        name = (m.group("name") or "").strip()
        type_name = _canonicalize_class_name(m.group("type"), class_aliases)
        if name and type_name:
            local_types[name] = type_name
    for m in ASSIGN_CAST_RX.finditer(line):
        name = (m.group("name") or "").strip()
        type_name = _canonicalize_class_name(m.group("type"), class_aliases)
        if name and type_name:
            local_types[name] = type_name
    for m in ASSIGN_VAR_RX.finditer(line):
        name = (m.group("name") or "").strip()
        src = (m.group("src") or "").strip()
        if not name or not src:
            continue
        src_type = local_types.get(src) or field_types.get(src)
        if src_type:
            local_types[name] = src_type
    for m in ASSIGN_THIS_FIELD_RX.finditer(line):
        name = (m.group("name") or "").strip()
        field = (m.group("field") or "").strip()
        if not name or not field:
            continue
        field_type = field_types.get(field)
        if field_type:
            local_types[name] = field_type
    for m in ASSIGN_STATIC_FIELD_RX.finditer(line):
        name = (m.group("name") or "").strip()
        owner = _canonicalize_class_name((m.group("owner") or "").strip().split(".")[-1], class_aliases)
        field = (m.group("field") or "").strip()
        if not name or not field:
            continue
        field_type = class_field_types_by_owner.get(owner, {}).get(field) or field_types.get(field)
        if field_type:
            local_types[name] = field_type


def _resolve_simple_owner_type(
    *,
    token: str,
    current_owner: str,
    local_types: dict[str, str],
    field_types: dict[str, str],
    known_class_names: set[str],
    class_aliases: dict[str, str],
) -> str:
    token = _strip_index_suffix(token)
    token = _strip_wrapping_parentheses(token)
    if not token:
        return ""
    cast_type = ""
    while True:
        cast_match = LEADING_CAST_RX.match(token)
        if not cast_match:
            break
        cast_type = _canonicalize_class_name(cast_match.group("type"), class_aliases)
        token = _strip_wrapping_parentheses(cast_match.group("rest").strip())
    if cast_type:
        return cast_type
    if token in {"this", "super"}:
        return current_owner
    if token in local_types:
        resolved = _canonicalize_class_name(local_types[token], class_aliases)
        if resolved:
            return resolved
    if token in field_types:
        resolved = _canonicalize_class_name(field_types[token], class_aliases)
        if resolved:
            return resolved
    token_norm = _canonicalize_class_name(token, class_aliases)
    if token_norm in known_class_names:
        return token_norm
    if token and token[0].isupper():
        return token_norm or token
    return ""


def _resolve_owner_expr_type(
    *,
    owner_expr: str,
    current_owner: str,
    local_types: dict[str, str],
    field_types: dict[str, str],
    known_class_names: set[str],
    class_field_types_by_owner: dict[str, dict[str, str]],
    method_return_types_by_owner: dict[tuple[str, str, int], str],
    class_aliases: dict[str, str],
) -> str:
    expr = (owner_expr or "").strip()
    if not expr:
        return ""
    segments = _split_owner_chain(expr)
    if not segments:
        return ""

    current_type = ""
    first = segments[0]
    first_call = _parse_call_segment(first)
    if first_call:
        method_name, arity = first_call
        current_type = method_return_types_by_owner.get((current_owner, method_name, arity), "")
        current_type = _canonicalize_class_name(current_type, class_aliases)
        if not current_type:
            return ""
    else:
        first = _strip_index_suffix(first)
        current_type = _resolve_simple_owner_type(
            token=first,
            current_owner=current_owner,
            local_types=local_types,
            field_types=field_types,
            known_class_names=known_class_names,
            class_aliases=class_aliases,
        )
        if not current_type:
            return ""

    for segment in segments[1:]:
        if not current_type:
            return ""
        call = _parse_call_segment(segment)
        if call:
            method_name, arity = call
            current_type = method_return_types_by_owner.get((current_type, method_name, arity), "")
            current_type = _canonicalize_class_name(current_type, class_aliases)
            if not current_type:
                return ""
            continue
        segment = _strip_index_suffix(segment)
        field_type = class_field_types_by_owner.get(current_type, {}).get(segment, "")
        field_type = _canonicalize_class_name(field_type, class_aliases)
        if field_type:
            current_type = field_type
            continue
        segment_norm = _canonicalize_class_name(segment, class_aliases)
        if segment_norm in known_class_names:
            current_type = segment_norm
            continue
        return ""
    return current_type


def _resolve_call_owner(
    *,
    owner_token: str,
    owner_expr: str,
    current_owner: str,
    local_types: dict[str, str],
    field_types: dict[str, str],
    known_class_names: set[str],
    class_field_types_by_owner: dict[str, dict[str, str]],
    method_return_types_by_owner: dict[tuple[str, str, int], str],
    class_aliases: dict[str, str],
) -> tuple[str, str]:
    owner_token_norm = (owner_token or "").strip()
    owner_expr_norm = (owner_expr or "").strip()
    if not owner_expr_norm and not owner_token_norm:
        return current_owner, "unqualified"

    resolved_owner_from_token = ""
    if owner_token_norm:
        resolved_owner_from_token = _resolve_simple_owner_type(
            token=owner_token_norm,
            current_owner=current_owner,
            local_types=local_types,
            field_types=field_types,
            known_class_names=known_class_names,
            class_aliases=class_aliases,
        )

    if owner_expr_norm:
        resolved_expr_owner = _resolve_owner_expr_type(
            owner_expr=owner_expr_norm,
            current_owner=current_owner,
            local_types=local_types,
            field_types=field_types,
            known_class_names=known_class_names,
            class_field_types_by_owner=class_field_types_by_owner,
            method_return_types_by_owner=method_return_types_by_owner,
            class_aliases=class_aliases,
        )
        expr_without_cast = _strip_wrapping_parentheses(_strip_casts(owner_expr_norm))
        if (
            resolved_owner_from_token
            and expr_without_cast == owner_token_norm
        ):
            # A cast on the call-result expression (e.g. "(ChatChannel) iterator")
            # should not override the receiver type inferred from the token.
            return resolved_owner_from_token, "resolved"
        if resolved_expr_owner:
            return resolved_expr_owner, "resolved"
        if not owner_token_norm:
            return "", "unresolved_qualified"

    if resolved_owner_from_token:
        return resolved_owner_from_token, "resolved"
    return "", "unresolved_qualified"


def _collect_call_arg_stats(
    src_dir: Path,
) -> tuple[
    dict[tuple[str, str, int, int], CallArgStats],
    dict[tuple[str, str, int], set[tuple[str, str, str]]],
]:
    stats: dict[tuple[str, str, int, int], CallArgStats] = {}
    observations: dict[tuple[str, str, int], set[tuple[str, str, str]]] = {}
    known_class_names: set[str] = set()
    class_owner_by_file: dict[Path, str] = {}
    class_fields_by_file: dict[Path, dict[str, str]] = {}
    class_field_types_by_owner: dict[str, dict[str, str]] = {}
    method_return_types_by_owner: dict[tuple[str, str, int], str] = {}
    parent_by_class: dict[str, str] = {}
    declared_methods_by_owner: dict[str, set[tuple[str, int]]] = {}
    class_aliases = _load_class_aliases(src_dir)

    for java_file in sorted(src_dir.rglob("*.java")):
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()
        current_owner = java_file.stem
        for line in lines:
            class_ext = CLASS_DECL_EXTENDS_RX.search(line)
            if class_ext:
                cls = _canonicalize_class_name((class_ext.group("name") or "").strip(), class_aliases)
                parent_raw = (class_ext.group("extends") or "").strip()
                if cls and parent_raw:
                    parent_by_class[cls] = _canonicalize_class_name(parent_raw.split(".")[-1].strip(), class_aliases)
            class_match = CLASS_DECL_RX.search(line)
            if class_match:
                current_owner = _canonicalize_class_name(class_match.group("name"), class_aliases)
                break
        class_owner_by_file[java_file] = current_owner
        class_fields = _collect_class_field_types(lines, class_aliases)
        class_fields_by_file[java_file] = class_fields
        merged_fields = dict(class_field_types_by_owner.get(current_owner, {}))
        merged_fields.update(class_fields)
        class_field_types_by_owner[current_owner] = merged_fields
        for line in lines:
            method_decl = METHOD_DECL_RET_RX.match(line)
            if not method_decl:
                continue
            method_name = (method_decl.group("name") or "").strip()
            ret_type = _canonicalize_class_name(method_decl.group("ret"), class_aliases)
            params_raw = method_decl.group("params") or ""
            arity = 0 if not params_raw.strip() else len(_split_top_level_args(params_raw))
            if not method_name or not ret_type:
                continue
            declared_methods_by_owner.setdefault(current_owner, set()).add((method_name, arity))
            method_return_types_by_owner[(current_owner, method_name, arity)] = ret_type
        known_class_names.add(current_owner)

    for java_file in sorted(src_dir.rglob("*.java")):
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()
        current_owner = class_owner_by_file.get(java_file, java_file.stem)
        field_types = class_fields_by_file.get(java_file, {})

        in_method = False
        brace_balance = 0
        local_types: dict[str, str] = {}

        for line in lines:
            line_to_scan = line
            if not in_method:
                method_decl = METHOD_DECL_RX.match(line)
                if not method_decl:
                    continue
                in_method = True
                params_raw = method_decl.group("params")
                local_types = _split_params_typed(params_raw, class_aliases)
                brace_balance = 0
                brace_idx = line.find("{", method_decl.start())
                if brace_idx >= 0:
                    line_to_scan = line[brace_idx + 1 :]
                else:
                    line_to_scan = ""
            else:
                _update_local_type_hints(
                    line,
                    local_types,
                    field_types,
                    class_field_types_by_owner,
                    class_aliases,
                )

            for owner_token, owner_expr, method, args in _iter_calls_in_line(line_to_scan):
                arity = len(args)
                resolved_owner, resolution_kind = _resolve_call_owner(
                    owner_token=owner_token,
                    owner_expr=owner_expr,
                    current_owner=current_owner,
                    local_types=local_types,
                    field_types=field_types,
                    known_class_names=known_class_names,
                    class_field_types_by_owner=class_field_types_by_owner,
                    method_return_types_by_owner=method_return_types_by_owner,
                    class_aliases=class_aliases,
                )
                if resolved_owner:
                    scoped = resolved_owner
                    visited: set[str] = set()
                    while scoped and scoped not in visited:
                        visited.add(scoped)
                        owner_methods = declared_methods_by_owner.get(scoped, set())
                        if (method, arity) in owner_methods:
                            resolved_owner = scoped
                            break
                        scoped = parent_by_class.get(scoped, "")
                scoped_owner = resolved_owner
                obs_key = (scoped_owner, method, arity)
                observations.setdefault(obs_key, set()).add((current_owner, resolved_owner, resolution_kind))
                owner_keys = [scoped_owner] if scoped_owner else []
                # Keep the global fallback bucket conservative: only aggregate
                # unqualified calls. Qualified receiver calls are tracked by
                # scoped owner and should not contaminate cross-owner fallback.
                if resolution_kind == "unqualified":
                    owner_keys.append("")
                for arg_index, arg_text in enumerate(args):
                    val = _parse_literal_value(arg_text)
                    for owner_key in owner_keys:
                        key = (owner_key, method, arity, arg_index)
                        cur = stats.setdefault(key, CallArgStats())
                        cur.total_calls += 1
                        if isinstance(val, object) and val.__class__ is object:
                            cur.nonliteral_calls += 1
                        else:
                            cur.literal_calls += 1
                            cur.literal_values.add(val)  # type: ignore[arg-type]

            brace_balance += line.count("{") - line.count("}")
            if in_method and brace_balance <= 0:
                in_method = False
                brace_balance = 0
                local_types = {}
    return stats, observations


def _collect_reflection_method_names(src_dir: Path) -> set[str]:
    names: set[str] = set()
    get_method_rx = re.compile(r'get(?:Declared)?Method\s*\(\s*"([A-Za-z_$][A-Za-z0-9_$]*)"')
    lookup_method_rx = re.compile(
        r'find(?:Virtual|Static|Special)\s*\(\s*[^,]+,\s*"([A-Za-z_$][A-Za-z0-9_$]*)"',
    )
    for java_file in sorted(src_dir.rglob("*.java")):
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            for rx in (get_method_rx, lookup_method_rx):
                for match in rx.finditer(line):
                    names.add(match.group(1))
    return names


def _collect_class_hierarchy(src_dir: Path) -> tuple[dict[str, str], dict[str, set[str]]]:
    parent_by_class: dict[str, str] = {}
    children_by_class: dict[str, set[str]] = {}
    for java_file in sorted(src_dir.rglob("*.java")):
        lines = java_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            m = CLASS_DECL_EXTENDS_RX.search(line)
            if not m:
                continue
            owner = (m.group("name") or "").strip()
            parent_raw = (m.group("extends") or "").strip()
            if not owner:
                break
            if parent_raw:
                parent = parent_raw.split(".")[-1].strip()
                if parent:
                    parent_by_class[owner] = parent
                    children_by_class.setdefault(parent, set()).add(owner)
            break
    return parent_by_class, children_by_class


def _iter_descendants(owner: str, children_by_class: dict[str, set[str]]) -> set[str]:
    out: set[str] = set()
    stack = list(children_by_class.get(owner, set()))
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children_by_class.get(cur, set()))
    return out


def _has_descendant_method_decl(
    *,
    owner: str,
    method: str,
    arity: int,
    children_by_class: dict[str, set[str]],
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]],
) -> bool:
    for descendant in _iter_descendants(owner, children_by_class):
        if method_decl_by_key.get((descendant, method, arity)):
            return True
    return False


def _has_ancestor_method_decl(
    *,
    owner: str,
    method: str,
    arity: int,
    parent_by_class: dict[str, str],
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]],
) -> bool:
    cur = parent_by_class.get(owner, "")
    seen: set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        if method_decl_by_key.get((cur, method, arity)):
            return True
        cur = parent_by_class.get(cur, "")
    return False


def _caller_guard_safety_reason(
    *,
    root: GuardParamRoot,
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]],
    owner_method_counts: dict[tuple[str, str], int],
    reflection_method_names: set[str],
) -> str | None:
    method_key = (root.owner, root.method, root.arity)
    decls = method_decl_by_key.get(method_key, [])
    if len(decls) != 1:
        return "ambiguous_or_missing_declaration"
    decl = decls[0]
    if decl.visibility != "private":
        return "non_private_method"
    if owner_method_counts.get((root.owner, root.method), 0) != 1:
        return "overloaded_method"
    if root.method in reflection_method_names:
        return "reflection_named_lookup"
    return None


def _can_auto_promote_project_closed_world(
    *,
    safety_reason: str | None,
    used_fallback_stats: bool,
    root: GuardParamRoot,
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]],
    owner_method_counts: dict[tuple[str, str], int],
    method_arity_counts: dict[tuple[str, int], int],
    call_observations: dict[tuple[str, str, int], set[tuple[str, str, str]]],
    reflection_method_names: set[str],
    parent_by_class: dict[str, str],
    children_by_class: dict[str, set[str]],
) -> tuple[bool, str | None]:
    if safety_reason is None:
        return True, None
    if safety_reason not in {"non_private_method", "overloaded_method"}:
        return False, safety_reason
    if used_fallback_stats:
        if root.owner in parent_by_class or children_by_class.get(root.owner):
            return False, "instance_inheritance_boundary"
        return False, "fallback_unscoped_callsites"
    if root.method in reflection_method_names:
        return False, "reflection_named_lookup"
    if safety_reason == "overloaded_method":
        return False, "overloaded_method"
    # non_private_method: allow auto-promotion only when method identity is
    # unambiguous inside owner and callsite observations remain closed-world.
    decls = method_decl_by_key.get((root.owner, root.method, root.arity), [])
    if len(decls) != 1:
        return False, "ambiguous_or_missing_declaration"
    decl = decls[0]
    if owner_method_counts.get((root.owner, root.method), 0) != 1:
        return False, "overloaded_method"
    if method_arity_counts.get((root.method, root.arity), 0) <= 0:
        return False, "missing_method_arity_stats"
    if decl.is_static:
        return True, None
    # Inheritance can still be auto-safe when the method cannot participate in
    # polymorphic dispatch (final) or no ancestor/descendant declares same
    # method signature.
    in_hierarchy = root.owner in parent_by_class or bool(children_by_class.get(root.owner))
    if in_hierarchy:
        descendant_decl = _has_descendant_method_decl(
            owner=root.owner,
            method=root.method,
            arity=root.arity,
            children_by_class=children_by_class,
            method_decl_by_key=method_decl_by_key,
        )
        ancestor_decl = _has_ancestor_method_decl(
            owner=root.owner,
            method=root.method,
            arity=root.arity,
            parent_by_class=parent_by_class,
            method_decl_by_key=method_decl_by_key,
        )
        if (descendant_decl or ancestor_decl) and not decl.is_final:
            return False, "instance_inheritance_boundary"
    # Instance-method conservative gate: every observed owner-scoped call must
    # be local to owner class and non-polymorphic under lightweight inference.
    obs_key = (root.owner, root.method, root.arity)
    obs = call_observations.get(obs_key, set())
    if not obs:
        return False, "instance_no_observed_callsites"
    for caller_owner, resolved_owner, resolution_kind in obs:
        if resolution_kind == "unresolved_qualified":
            return False, "fallback_unscoped_callsites"
        if caller_owner != root.owner:
            return False, "instance_external_callsite"
        if resolution_kind == "resolved" and resolved_owner and resolved_owner != root.owner:
            return False, "instance_qualified_external_owner"
    return True, None


def _can_auto_promote_external_callsite_strict(
    *,
    root: GuardParamRoot,
    stats: CallArgStats,
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]],
    reflection_method_names: set[str],
    parent_by_class: dict[str, str],
    children_by_class: dict[str, set[str]],
    call_observations: dict[tuple[str, str, int], set[tuple[str, str, str]]],
) -> bool:
    if root.method in reflection_method_names:
        return False
    decls = method_decl_by_key.get((root.owner, root.method, root.arity), [])
    if len(decls) != 1:
        return False
    decl = decls[0]
    if decl.visibility == "public" and not decl.is_final:
        return False
    in_hierarchy = root.owner in parent_by_class or bool(children_by_class.get(root.owner))
    if in_hierarchy:
        descendant_decl = _has_descendant_method_decl(
            owner=root.owner,
            method=root.method,
            arity=root.arity,
            children_by_class=children_by_class,
            method_decl_by_key=method_decl_by_key,
        )
        ancestor_decl = _has_ancestor_method_decl(
            owner=root.owner,
            method=root.method,
            arity=root.arity,
            parent_by_class=parent_by_class,
            method_decl_by_key=method_decl_by_key,
        )
        if (descendant_decl or ancestor_decl) and not decl.is_final:
            return False
    if stats.nonliteral_calls > 0 or stats.literal_calls != stats.total_calls:
        return False
    if stats.total_calls <= 0:
        return False
    obs = call_observations.get((root.owner, root.method, root.arity), set())
    if not obs:
        return False
    external_seen = False
    for caller_owner, resolved_owner, resolution_kind in obs:
        if resolution_kind == "unresolved_qualified":
            return False
        if resolved_owner and resolved_owner != root.owner:
            return False
        if caller_owner != root.owner:
            if resolution_kind not in {"resolved", "in_owner_scope"}:
                return False
            if resolution_kind == "resolved" and not resolved_owner:
                return False
            external_seen = True
    return external_seen


def _detect_guard_dead_by_callers(
    *,
    guard_roots: list[GuardParamRoot],
    call_stats: dict[tuple[str, str, int, int], CallArgStats],
    call_observations: dict[tuple[str, str, int], set[tuple[str, str, str]]],
    method_arity_counts: dict[tuple[str, int], int],
    method_decl_by_key: dict[tuple[str, str, int], list[MethodDeclMeta]],
    owner_method_counts: dict[tuple[str, str], int],
    reflection_method_names: set[str],
    parent_by_class: dict[str, str],
    children_by_class: dict[str, set[str]],
    guard_promotion_allowlist: list[dict[str, str]] | None = None,
    guard_promotion_denylist: list[dict[str, str]] | None = None,
    caller_auto_mode: str = "closed-world",
) -> list[Candidate]:
    out: list[Candidate] = []
    allowlist = guard_promotion_allowlist or []
    denylist = guard_promotion_denylist or []
    for root in guard_roots:
        scoped_key = (root.owner, root.method, root.arity, root.param_index)
        stats = call_stats.get(scoped_key)
        used_fallback_stats = False
        if stats is None:
            # only use global fallback when method+arity appears uniquely in declarations
            if method_arity_counts.get((root.method, root.arity), 0) != 1:
                continue
            stats = call_stats.get(("", root.method, root.arity, root.param_index))
            used_fallback_stats = True
        if stats is None:
            continue
        if stats.total_calls == 0 or stats.nonliteral_calls > 0 or stats.literal_calls != stats.total_calls:
            continue
        const_value = _parse_literal_value(root.const_raw)
        if isinstance(const_value, object) and const_value.__class__ is object:
            continue
        evaluations: list[bool] = []
        for value in sorted(stats.literal_values, key=lambda v: str(v)):
            result = _eval_guard(root.op, const_value, value)
            if result is None:
                evaluations = []
                break
            evaluations.append(result)
        if not evaluations:
            continue
        if all(v is False for v in evaluations):
            vals = ",".join(str(v) for v in sorted(stats.literal_values, key=lambda v: str(v)))
            safety_reason = _caller_guard_safety_reason(
                root=root,
                method_decl_by_key=method_decl_by_key,
                owner_method_counts=owner_method_counts,
                reflection_method_names=reflection_method_names,
            )
            is_safe_auto = False
            project_block_reason: str | None = None
            if caller_auto_mode == "closed-world":
                is_safe_auto = safety_reason is None
            elif caller_auto_mode == "project-closed-world":
                is_safe_auto, project_block_reason = _can_auto_promote_project_closed_world(
                    safety_reason=safety_reason,
                    used_fallback_stats=used_fallback_stats,
                    root=root,
                    method_decl_by_key=method_decl_by_key,
                    owner_method_counts=owner_method_counts,
                    method_arity_counts=method_arity_counts,
                    call_observations=call_observations,
                    reflection_method_names=reflection_method_names,
                    parent_by_class=parent_by_class,
                    children_by_class=children_by_class,
                )
                if (
                    not is_safe_auto
                    and project_block_reason == "instance_external_callsite"
                    and _can_auto_promote_external_callsite_strict(
                        root=root,
                        stats=stats,
                        method_decl_by_key=method_decl_by_key,
                        reflection_method_names=reflection_method_names,
                        parent_by_class=parent_by_class,
                        children_by_class=children_by_class,
                        call_observations=call_observations,
                    )
                ):
                    is_safe_auto = True
                    project_block_reason = None
            block_reason = project_block_reason or safety_reason or "insufficient_proof"
            deny_reason = _guard_deny_reason(
                file=root.file,
                owner=root.owner,
                method=root.method,
                denylist=denylist,
            )
            denylisted = bool(deny_reason)
            if denylisted:
                is_safe_auto = False
                project_block_reason = deny_reason
                block_reason = deny_reason
            if (
                not is_safe_auto
                and not denylisted
                and _is_guard_allowlisted(
                    file=root.file,
                    owner=root.owner,
                    method=root.method,
                    reason=block_reason,
                    allowlist=allowlist,
                )
            ):
                is_safe_auto = True
                project_block_reason = None
            confidence = "high" if is_safe_auto else "medium"
            if is_safe_auto:
                notes = (
                    f"Observed literal callers ({vals}) make guard condition always false; "
                    f"{caller_auto_mode} semantic gate passed."
                )
            elif denylisted:
                notes = (
                    f"Observed literal callers ({vals}) make guard condition always false; "
                    "review-only: intentionally blocked by guard_promotion_denylist.csv."
                )
            elif project_block_reason:
                notes = (
                    f"Observed literal callers ({vals}) make guard condition always false; "
                    f"review-only: semantic gate blocked ({project_block_reason})."
                )
            elif safety_reason:
                notes = (
                    f"Observed literal callers ({vals}) make guard condition always false; "
                    f"review-only: semantic gate blocked ({safety_reason})."
                )
            else:
                notes = f"Observed literal callers ({vals}) make guard condition always false."
            out.append(
                Candidate(
                    id=f"caller:guard_dead:{root.file}:{root.method}:{root.param_index}",
                    file=root.file,
                    owner=root.owner,
                    member=root.method,
                    kind="line_contains",
                    rule_type="guard_dead_by_callers",
                    match=f"{root.method}[{root.param_index}] {root.op} {root.const_raw}",
                    dependency=root.candidate_id,
                    confidence=confidence,
                    action_hint="signature_rewrites.drop_statement_contains",
                    notes=notes,
                    gate_status="auto" if is_safe_auto else "review",
                    gate_reason="" if is_safe_auto else (project_block_reason or safety_reason or "insufficient_proof"),
                )
            )
    return out


def detect_unified_cleanup_candidates(
    src_dir: Path,
    *,
    caller_auto_mode: str = "project-closed-world",
) -> list[Candidate]:
    (
        roots,
        field_owner_by_name,
        guard_roots,
        method_arity_counts,
        method_decl_by_key,
        owner_method_counts,
    ) = _detect_root_candidates(src_dir)
    fallout = _detect_fallout_candidates(
        src_dir=src_dir,
        root_candidates=roots,
        field_owner_by_name=field_owner_by_name,
    )
    call_stats, call_observations = _collect_call_arg_stats(src_dir)
    reflection_method_names = _collect_reflection_method_names(src_dir)
    parent_by_class, children_by_class = _collect_class_hierarchy(src_dir)
    guard_promotion_allowlist = _load_guard_promotion_allowlist(src_dir)
    guard_promotion_denylist = _load_guard_promotion_denylist(src_dir)
    caller_dead = _detect_guard_dead_by_callers(
        guard_roots=guard_roots,
        call_stats=call_stats,
        call_observations=call_observations,
        method_arity_counts=method_arity_counts,
        method_decl_by_key=method_decl_by_key,
        owner_method_counts=owner_method_counts,
        reflection_method_names=reflection_method_names,
        parent_by_class=parent_by_class,
        children_by_class=children_by_class,
        guard_promotion_allowlist=guard_promotion_allowlist,
        guard_promotion_denylist=guard_promotion_denylist,
        caller_auto_mode=caller_auto_mode,
    )
    dedup: dict[tuple[str, str, str], Candidate] = {}
    for candidate in roots + fallout + caller_dead:
        key = (candidate.file, candidate.rule_type, candidate.match)
        dedup[key] = candidate
    return sorted(dedup.values(), key=lambda c: (c.file, c.member, c.rule_type, c.id))


def _write_csv(path: Path, candidates: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "id",
                "kind",
                "file",
                "owner",
                "member",
                "rule_type",
                "match",
                "dependency",
                "confidence",
                "action_hint",
                "notes",
                "gate_status",
                "gate_reason",
            ]
        )
        for c in candidates:
            writer.writerow(
                [
                    c.id,
                    c.kind,
                    c.file,
                    c.owner,
                    c.member,
                    c.rule_type,
                    c.match,
                    c.dependency,
                    c.confidence,
                    c.action_hint,
                    c.notes,
                    c.gate_status,
                    c.gate_reason,
                ]
            )


def _write_graph(path: Path, candidates: list[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": [asdict(c) for c in candidates],
        "edges": [
            {"from": c.dependency, "to": c.id}
            for c in candidates
            if c.dependency
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Unified cleanup detector for guard roots and fallout.")
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument(
        "--out-csv",
        type=Path,
        default=REFACTOR_PLAN_GENERATED_DIR / "unified_cleanup_candidates.csv",
    )
    ap.add_argument(
        "--out-graph",
        type=Path,
        default=REFACTOR_PLAN_GENERATED_DIR / "unified_cleanup_graph.json",
    )
    ap.add_argument(
        "--caller-guard-auto",
        choices=("off", "closed-world", "project-closed-world"),
        default="project-closed-world",
        help="Promotion mode for guard_dead_by_callers: off=review-only, closed-world=strict auto-promote, project-closed-world=broader auto-promote with closed-world assumptions.",
    )
    args = ap.parse_args()
    resolve_path_args(args, ("src_dir", "out_csv", "out_graph"))

    candidates = detect_unified_cleanup_candidates(
        args.src_dir,
        caller_auto_mode=args.caller_guard_auto,
    )
    _write_csv(args.out_csv, candidates)
    _write_graph(args.out_graph, candidates)
    print(
        f"Detected {len(candidates)} unified cleanup candidates. "
        f"CSV: {args.out_csv} Graph: {args.out_graph}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
