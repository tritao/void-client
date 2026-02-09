#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import REFACTOR_PLAN_DIR, REFACTOR_SRC_DIR
from tools.refactor.common.io import non_comment_csv_lines
from tools.refactor.common.ts_java import build_java_parser, iter_nodes


@dataclass(frozen=True)
class ResetMethodRule:
    owner_class: str
    target_file: str
    reset_method: str
    field_name: str
    reset_value: str


@dataclass(frozen=True)
class DelegationRewriteRule:
    file: str
    method: str
    owner_class: str
    field_name: str
    reset_value: str
    rewrite_to: str


@dataclass(frozen=True)
class SignatureRewriteRule:
    file: str
    owner: str
    method: str
    signature_before: str
    signature_after: str
    op: str
    param_index: int
    new_name: str
    match_text: str


@dataclass(frozen=True)
class SignatureCallsiteDropRule:
    method: str
    owner_class: str
    arg_arity: int
    arg_index: int


@dataclass(frozen=True)
class NoArgCallRewriteRule:
    file: str
    method: str


@dataclass(frozen=True)
class DropFirstArgCallRewriteRule:
    file: str
    owner_class: str
    method: str
    arg_text: str
    arg_index: int


@dataclass(frozen=True)
class DropMemberRule:
    file: str
    kind: str
    name: str


@dataclass
class RuleStageStats:
    total: int = 0
    matched: int = 0
    unmatched: int = 0
    missing_file: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "missing_file": self.missing_file,
        }


def _push_unmatched(
    entries: list[dict[str, object]],
    *,
    stage: str,
    file: str,
    reason: str,
    rule: str,
    plan_file: str = "",
    row_key: dict[str, str] | None = None,
) -> None:
    entry = {
        "stage": stage,
        "file": file,
        "reason": reason,
        "rule": rule,
    }
    if plan_file:
        entry["plan_file"] = plan_file
    if row_key:
        entry["row_key"] = {k: (v or "") for k, v in row_key.items()}
    entries.append(entry)


def _load_reset_rules(path: Path) -> list[ResetMethodRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[ResetMethodRule] = []
    for row in reader:
        owner_class = (row.get("owner_class") or "").strip()
        target_file = (row.get("target_file") or "").strip()
        reset_method = (row.get("reset_method") or "").strip()
        field_name = (row.get("field_name") or "").strip()
        reset_value = (row.get("reset_value") or "").strip()
        if not (owner_class and target_file and reset_method and field_name):
            continue
        out.append(
            ResetMethodRule(
                owner_class=owner_class,
                target_file=target_file,
                reset_method=reset_method,
                field_name=field_name,
                reset_value=reset_value or "null",
            )
        )
    return out


def _load_rewrite_rules(path: Path) -> list[DelegationRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[DelegationRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        method = (row.get("method") or "").strip()
        owner_class = (row.get("owner_class") or "").strip()
        field_name = (row.get("field_name") or "").strip()
        reset_value = (row.get("reset_value") or "").strip()
        rewrite_to = (row.get("rewrite_to") or "").strip()
        if not (file and method and owner_class and field_name and rewrite_to):
            continue
        out.append(
            DelegationRewriteRule(
                file=file,
                method=method,
                owner_class=owner_class,
                field_name=field_name,
                reset_value=reset_value or "null",
                rewrite_to=rewrite_to,
            )
        )
    return out


def _load_signature_rules(path: Path) -> list[SignatureRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[SignatureRewriteRule] = []
    valid_ops = {"drop_param", "rename_param", "drop_statement_contains"}
    for row in reader:
        file = (row.get("file") or "").strip()
        owner = (row.get("owner") or "").strip()
        method = (row.get("method") or "").strip()
        signature_before = (row.get("signature_before") or "").strip()
        signature_after = (row.get("signature_after") or "").strip()
        op = (row.get("op") or "").strip().lower()
        param_index_raw = (row.get("param_index") or "").strip()
        new_name = (row.get("new_name") or "").strip()
        match_text = (row.get("match_text") or "").strip()
        if not (file and method and op):
            continue
        if op not in valid_ops:
            continue
        param_index = -1
        if op in {"drop_param", "rename_param"}:
            if not param_index_raw:
                continue
            try:
                param_index = int(param_index_raw)
            except ValueError:
                continue
            if param_index < 0:
                continue
        if op == "rename_param" and not new_name:
            continue
        if op == "drop_statement_contains" and not match_text:
            continue
        out.append(
            SignatureRewriteRule(
                file=file,
                owner=owner,
                method=method,
                signature_before=signature_before,
                signature_after=signature_after,
                op=op,
                param_index=param_index,
                new_name=new_name,
                match_text=match_text,
            )
        )
    return out


def _load_noarg_call_rules(path: Path) -> list[NoArgCallRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[NoArgCallRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        method = (row.get("method") or "").strip()
        if not (file and method):
            continue
        out.append(NoArgCallRewriteRule(file=file, method=method))
    return out


def _load_drop_first_arg_call_rules(path: Path) -> list[DropFirstArgCallRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[DropFirstArgCallRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        owner_class = (row.get("owner_class") or "").strip()
        method = (row.get("method") or "").strip()
        arg_text = ((row.get("arg_text") or "").strip()) or ((row.get("first_arg_text") or "").strip())
        arg_index_raw = (row.get("arg_index") or "").strip()
        arg_index = 0
        if arg_index_raw:
            try:
                arg_index = int(arg_index_raw)
            except ValueError:
                continue
        if arg_index < 0:
            continue
        if not (file and method and arg_text):
            continue
        out.append(
            DropFirstArgCallRewriteRule(
                file=file,
                owner_class=owner_class,
                method=method,
                arg_text=arg_text,
                arg_index=arg_index,
            )
        )
    return out


def _load_drop_member_rules(path: Path) -> list[DropMemberRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[DropMemberRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        kind = (row.get("kind") or "").strip().lower()
        name = (row.get("name") or "").strip()
        if not (file and kind and name):
            continue
        out.append(DropMemberRule(file=file, kind=kind, name=name))
    return out


def _ensure_method(text: str, method_name: str, method_src: str) -> tuple[str, bool]:
    if re.search(rf"\b{re.escape(method_name)}\s*\(", text):
        return text, False
    idx = text.rfind("}")
    if idx < 0:
        raise SystemExit("Could not find class closing brace.")
    insertion = "\n\n" + method_src.rstrip() + "\n"
    return text[:idx] + insertion + text[idx:], True


def _find_method_body_span(text: str, method_name: str) -> tuple[int, int] | None:
    sig_rx = re.compile(rf"\b{re.escape(method_name)}\s*\([^)]*\)\s*\{{")
    m = sig_rx.search(text)
    if not m:
        return None
    open_brace = text.find("{", m.start())
    if open_brace < 0:
        return None
    depth = 0
    for i in range(open_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (open_brace + 1, i)
    return None


def _rewrite_method_body(text: str, method_name: str, new_body_lines: list[str]) -> tuple[str, bool]:
    span = _find_method_body_span(text, method_name)
    if span is None:
        return text, False
    start, end = span
    body = "\n" + "\n".join(new_body_lines).rstrip() + "\n"
    if text[start:end] == body:
        return text, False
    return text[:start] + body + text[end:], True


def _apply_delegation_rewrite(text: str, rule: DelegationRewriteRule) -> tuple[str, bool]:
    span = _find_method_body_span(text, rule.method)
    if span is None:
        return text, False
    start, end = span
    body = text[start:end]
    assign_rx = re.compile(
        rf"\b{re.escape(rule.owner_class)}\s*\.\s*{re.escape(rule.field_name)}\s*=\s*{re.escape(rule.reset_value)}\s*;"
    )
    replacement = f"{rule.rewrite_to};"
    new_body, count = assign_rx.subn(replacement, body)
    if count == 0:
        return text, False
    return text[:start] + new_body + text[end:], True


def _find_matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    depth = 1
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _normalize_type_name(type_text: str) -> str:
    t = (type_text or "").strip()
    if not t:
        return ""
    t = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", t)
    t = re.sub(r"\b(final|volatile|transient)\b", "", t).strip()
    if "<" in t:
        t = t.split("<", 1)[0].strip()
    if t.endswith("..."):
        t = t[:-3].strip()
    while t.endswith("[]"):
        t = t[:-2].strip()
    if "." in t:
        t = t.rsplit(".", 1)[-1].strip()
    return t


def _parse_signature_types(sig_text: str) -> tuple[str, ...] | None:
    s = (sig_text or "").strip()
    if not s:
        return None
    if s == "()":
        return ()
    parts = [_normalize_type_name(p.strip()) for p in s.split(",")]
    return tuple(p for p in parts if p)


def _split_top_level_commas(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    depth = 0
    for i, ch in enumerate(text):
        if ch in "(<[{":
            depth += 1
        elif ch in ")>]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _param_type_from_decl(param_decl: str) -> str:
    s = (param_decl or "").strip()
    if not s:
        return ""
    s = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", s).strip()
    s = re.sub(r"\bfinal\s+", "", s).strip()
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", s)
    if not m:
        return _normalize_type_name(s)
    name_start = m.start(1)
    type_part = s[:name_start].strip()
    return _normalize_type_name(type_part)


def _rename_param_decl(param_decl: str, new_name: str) -> str:
    return re.sub(r"([A-Za-z_$][\w$]*)\s*$", new_name, param_decl.strip())


def _param_name_from_decl(param_decl: str) -> str:
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", (param_decl or "").strip())
    return m.group(1) if m else ""


def _char_to_byte_offset(text: str, char_offset: int) -> int:
    if char_offset <= 0:
        return 0
    return len(text[:char_offset].encode("utf-8"))


def _rewrite_identifier_usages_in_span_ts(
    text: str,
    *,
    body_start: int,
    body_end: int,
    old_name: str,
    new_name: str,
) -> tuple[str, bool]:
    if not old_name or old_name == new_name:
        return text, False
    try:
        data = text.encode("utf-8")
        parser = build_java_parser(out_so=Path("build/ts-languages-java.so"))
        tree = parser.parse(data)
    except Exception:
        return text, False

    start_b = _char_to_byte_offset(text, body_start)
    end_b = _char_to_byte_offset(text, body_end)
    edits: list[tuple[int, int]] = []
    for node in iter_nodes(tree.root_node):
        if node.type != "identifier":
            continue
        if node.start_byte < start_b or node.end_byte > end_b:
            continue
        token = data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        if token != old_name:
            continue
        edits.append((node.start_byte, node.end_byte))

    if not edits:
        return text, False

    out = bytearray(data)
    repl = new_name.encode("utf-8")
    for s, e in sorted(edits, key=lambda p: p[0], reverse=True):
        out[s:e] = repl
    return out.decode("utf-8", errors="replace"), True


def _find_method_spans(text: str, method_name: str) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    rx = re.compile(rf"\b{re.escape(method_name)}\s*\(")
    pos = 0
    while True:
        m = rx.search(text, pos)
        if not m:
            break
        name_start = m.start()
        prev = text[name_start - 1] if name_start > 0 else ""
        if prev == ".":
            pos = m.end()
            continue
        line_start = text.rfind("\n", 0, name_start)
        line_start = 0 if line_start < 0 else line_start + 1
        prefix = text[line_start:name_start].strip()
        if not prefix or "=" in prefix:
            pos = m.end()
            continue
        open_paren = text.find("(", name_start)
        if open_paren < 0:
            pos = m.end()
            continue
        close_paren = _find_matching(text, open_paren, "(", ")")
        if close_paren < 0:
            pos = m.end()
            continue
        k = close_paren + 1
        while k < len(text) and text[k].isspace():
            k += 1
        while k < len(text) and text[k] not in "{;":
            k += 1
        if k >= len(text):
            pos = m.end()
            continue
        body_start: int | None = None
        body_end: int | None = None
        if text[k] == "{":
            close_brace = _find_matching(text, k, "{", "}")
            if close_brace < 0:
                pos = m.end()
                continue
            body_start = k + 1
            body_end = close_brace
            end = close_brace + 1
        else:
            end = k + 1
        params_text = text[open_paren + 1 : close_paren]
        params = _split_top_level_commas(params_text)
        sig = tuple(_param_type_from_decl(p) for p in params if p.strip())
        spans.append(
            {
                "decl_start": line_start,
                "name_start": name_start,
                "params_start": open_paren + 1,
                "params_end": close_paren,
                "params": params,
                "sig": sig,
                "body_start": body_start,
                "body_end": body_end,
                "end": end,
            }
        )
        pos = end
    return spans


def _rewrite_signature_semantic(text: str, rule: SignatureRewriteRule) -> tuple[str, bool]:
    sig_before = _parse_signature_types(rule.signature_before)
    sig_after = _parse_signature_types(rule.signature_after)
    spans = _find_method_spans(text, rule.method)
    if not spans:
        return text, False

    def _matches(sig: tuple[str, ...], target: tuple[str, ...] | None) -> bool:
        if target is None:
            return True
        return sig == target

    target = None
    for span in spans:
        if _matches(span["sig"], sig_before):
            target = span
            break
    if target is None and sig_after is not None:
        for span in spans:
            if span["sig"] == sig_after:
                return text, False
    if target is None:
        if sig_before is None and len(spans) == 1:
            target = spans[0]
        else:
            return text, False

    if rule.op in {"drop_param", "rename_param"}:
        params = list(target["params"])
        idx = rule.param_index
        if idx >= len(params):
            return text, False
        if rule.op == "drop_param":
            del params[idx]
            old_param_name = ""
        else:
            old_param_name = _param_name_from_decl(params[idx])
            renamed = _rename_param_decl(params[idx], rule.new_name)
            if renamed == params[idx]:
                return text, False
            params[idx] = renamed
        new_params = ", ".join(params)
        start = int(target["params_start"])
        end = int(target["params_end"])
        if text[start:end] == new_params:
            return text, False
        out = text[:start] + new_params + text[end:]
        changed = True
        if rule.op == "rename_param" and old_param_name:
            body_start = target["body_start"]
            body_end = target["body_end"]
            if body_start is not None and body_end is not None:
                delta = len(new_params) - (end - start)
                new_body_start = int(body_start) + delta
                new_body_end = int(body_end) + delta
                out2, body_changed = _rewrite_identifier_usages_in_span_ts(
                    out,
                    body_start=new_body_start,
                    body_end=new_body_end,
                    old_name=old_param_name,
                    new_name=rule.new_name,
                )
                out = out2
                changed = changed or body_changed
        return out, changed

    if rule.op == "drop_statement_contains":
        body_start = target["body_start"]
        body_end = target["body_end"]
        if body_start is None or body_end is None:
            return text, False
        body = text[body_start:body_end]
        kept: list[str] = []
        changed = False
        for raw in body.splitlines():
            if rule.match_text in raw:
                changed = True
                continue
            kept.append(raw)
        if not changed:
            return text, False
        new_body = "\n".join(kept)
        return text[:body_start] + new_body + text[body_end:], True

    return text, False


def _identifier_tail_token(text: str) -> str:
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", text.strip())
    return m.group(1) if m else ""


def _build_signature_callsite_drop_rules(signature_rules: list[SignatureRewriteRule]) -> list[SignatureCallsiteDropRule]:
    out: list[SignatureCallsiteDropRule] = []
    seen: set[tuple[str, str, int, int]] = set()
    for rule in signature_rules:
        if rule.op != "drop_param":
            continue
        before_types = _parse_signature_types(rule.signature_before)
        if before_types is None:
            continue
        arg_arity = len(before_types)
        if rule.param_index < 0 or rule.param_index >= arg_arity:
            continue
        key = (rule.method, rule.owner, arg_arity, rule.param_index)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            SignatureCallsiteDropRule(
                method=rule.method,
                owner_class=rule.owner,
                arg_arity=arg_arity,
                arg_index=rule.param_index,
            )
        )
    return out


def _drop_invocation_arg(
    data: bytes,
    arg_list_start: int,
    arg_list_end: int,
    arg_ranges: list[tuple[int, int]],
    arg_index: int,
) -> tuple[bytes, bool]:
    if arg_index < 0 or arg_index >= len(arg_ranges):
        return data, False
    open_paren = data.find(b"(", arg_list_start, arg_list_end)
    close_paren = data.rfind(b")", arg_list_start, arg_list_end)
    if open_paren < 0 or close_paren < 0 or open_paren >= close_paren:
        return data, False
    if len(arg_ranges) == 1:
        return data[: open_paren + 1] + data[close_paren:], True

    if arg_index == 0:
        cut_start = arg_ranges[0][0]
        cut_end = arg_ranges[1][0]
    elif arg_index == len(arg_ranges) - 1:
        cut_start = arg_ranges[arg_index - 1][1]
        cut_end = arg_ranges[arg_index][1]
    else:
        cut_start = arg_ranges[arg_index - 1][1]
        cut_end = arg_ranges[arg_index + 1][0]

    if cut_start >= cut_end:
        return data, False
    return data[:cut_start] + data[cut_end:], True


def _rewrite_calls_for_signature_drop_rules(
    text: str,
    rules: list[SignatureCallsiteDropRule],
) -> tuple[str, bool, set[tuple[str, str, int, int]]]:
    if not rules:
        return text, False, set()
    try:
        data = text.encode("utf-8")
        parser = build_java_parser(out_so=Path("build/ts-languages-java.so"))
        tree = parser.parse(data)
    except Exception:
        return text, False, set()

    rules_by_method: dict[str, list[SignatureCallsiteDropRule]] = {}
    for rule in rules:
        rules_by_method.setdefault(rule.method, []).append(rule)

    edits: list[tuple[int, int, list[tuple[int, int]], int]] = []
    matched_rules: set[tuple[str, str, int, int]] = set()
    for node in iter_nodes(tree.root_node):
        if node.type != "method_invocation":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        method_name = data[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace").strip()
        if not method_name:
            continue
        candidates = rules_by_method.get(method_name)
        if not candidates:
            continue
        args_node = node.child_by_field_name("arguments")
        if args_node is None:
            for child in node.children:
                if child.type == "argument_list":
                    args_node = child
                    break
        if args_node is None:
            continue
        arg_nodes = list(getattr(args_node, "named_children", []) or [])
        argc = len(arg_nodes)
        if argc == 0:
            continue
        object_node = node.child_by_field_name("object")
        owner_tail = ""
        if object_node is not None:
            object_text = data[object_node.start_byte : object_node.end_byte].decode("utf-8", errors="replace")
            owner_tail = _identifier_tail_token(object_text)

        same_arity = [rule for rule in candidates if rule.arg_arity == argc]
        scoped = [rule for rule in same_arity if rule.owner_class and rule.owner_class == owner_tail]
        unscoped = [rule for rule in same_arity if not rule.owner_class]
        active = scoped or unscoped
        if not active:
            continue
        arg_indexes = {rule.arg_index for rule in active}
        if len(arg_indexes) != 1:
            continue
        arg_index = next(iter(arg_indexes))
        if arg_index < 0 or arg_index >= argc:
            continue
        for active_rule in active:
            matched_rules.add((active_rule.method, active_rule.owner_class, active_rule.arg_arity, active_rule.arg_index))
        arg_ranges = [(child.start_byte, child.end_byte) for child in arg_nodes]
        edits.append((args_node.start_byte, args_node.end_byte, arg_ranges, arg_index))

    if not edits:
        return text, False, set()

    out = data
    changed = False
    for args_start, args_end, arg_ranges, arg_index in sorted(edits, key=lambda x: x[0], reverse=True):
        out, did_change = _drop_invocation_arg(out, args_start, args_end, arg_ranges, arg_index)
        changed = changed or did_change
    return out.decode("utf-8", errors="replace"), changed, matched_rules


def _rewrite_noarg_calls(text: str, rule: NoArgCallRewriteRule) -> tuple[str, bool]:
    method_token = f".{rule.method}"
    out: list[str] = []
    changed = False
    i = 0
    n = len(text)
    while i < n:
        idx = text.find(method_token, i)
        if idx < 0:
            out.append(text[i:])
            break
        out.append(text[i:idx])
        j = idx + len(method_token)
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] != "(":
            out.append(text[idx:j])
            i = j
            continue
        k = j + 1
        depth = 1
        while k < n and depth > 0:
            ch = text[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            k += 1
        if depth != 0:
            out.append(text[idx:k])
            i = k
            continue
        out.append(f"{method_token}()")
        changed = True
        i = k
    return "".join(out), changed


def _rewrite_drop_first_arg_calls(text: str, rule: DropFirstArgCallRewriteRule) -> tuple[str, bool]:
    def _find_matching_paren(src: str, open_idx: int) -> int:
        depth = 1
        i = open_idx + 1
        while i < len(src):
            ch = src[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    def _split_top_level_args(arg_text: str) -> list[str]:
        args: list[str] = []
        start = 0
        depth = 0
        i = 0
        while i < len(arg_text):
            ch = arg_text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                args.append(arg_text[start:i].strip())
                start = i + 1
            i += 1
        tail = arg_text[start:].strip()
        if tail:
            args.append(tail)
        return args

    out: list[str] = []
    changed = False
    i = 0
    n = len(text)
    method = rule.method
    while i < n:
        idx = text.find(method, i)
        if idx < 0:
            out.append(text[i:])
            break
        if rule.owner_class:
            owner_token = f"{rule.owner_class}."
            owner_start = idx - len(owner_token)
            if owner_start < 0 or text[owner_start:idx] != owner_token:
                out.append(text[i:idx + len(method)])
                i = idx + len(method)
                continue
        prev = text[idx - 1] if idx > 0 else ""
        next_ch = text[idx + len(method)] if idx + len(method) < n else ""
        if (prev and (prev.isalnum() or prev == "_")) or (next_ch and (next_ch.isalnum() or next_ch == "_")):
            out.append(text[i:idx + len(method)])
            i = idx + len(method)
            continue
        j = idx + len(method)
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] != "(":
            out.append(text[i:j])
            i = j
            continue
        close_idx = _find_matching_paren(text, j)
        if close_idx < 0:
            out.append(text[i:])
            break
        args = _split_top_level_args(text[j + 1 : close_idx])
        if not args:
            out.append(text[i:close_idx + 1])
            i = close_idx + 1
            continue
        if len(args) <= rule.arg_index or args[rule.arg_index] != rule.arg_text:
            out.append(text[i:close_idx + 1])
            i = close_idx + 1
            continue
        new_args = ", ".join(args[: rule.arg_index] + args[rule.arg_index + 1 :])
        out.append(text[i:j + 1])
        out.append(new_args)
        out.append(")")
        changed = True
        i = close_idx + 1
    return "".join(out), changed


def _find_method_block_span(text: str, method_name: str) -> tuple[int, int] | None:
    sig_rx = re.compile(rf"\b{re.escape(method_name)}\s*\([^)]*\)\s*\{{")
    m = sig_rx.search(text)
    if not m:
        return None
    open_brace = text.find("{", m.start())
    if open_brace < 0:
        return None
    depth = 0
    for i in range(open_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (m.start(), i + 1)
    return None


def _is_matching_field_declaration(line: str, member_name: str) -> bool:
    if not line.strip().endswith(";"):
        return False
    if not re.search(rf"\b{re.escape(member_name)}\b", line):
        return False
    # Restrict to declaration-style lines, not arbitrary statements.
    return bool(re.match(r"^\s*(?:public|protected|private|static|final|transient|volatile|\s)+[\w<>\[\], ?$.]+\s+\w[\w$]*\s*(?:=\s*[^;]*)?;\s*$", line))


def _drop_members(text: str, rules: list[DropMemberRule]) -> tuple[str, bool]:
    if not rules:
        return text, False
    changed = False
    out_text = text

    for rule in rules:
        if rule.kind != "method":
            continue
        span = _find_method_block_span(out_text, rule.name)
        if span is None:
            continue
        start, end = span
        line_start = out_text.rfind("\n", 0, start)
        if line_start == -1:
            start = 0
        else:
            start = line_start + 1
        prefix = out_text[:start].rstrip()
        suffix = out_text[end:].lstrip("\n")
        if prefix and suffix:
            out_text = f"{prefix}\n\n{suffix}"
        else:
            out_text = f"{prefix}{suffix}"
        changed = True

    lines = out_text.splitlines(keepends=True)
    depth = 0
    out: list[str] = []
    for line in lines:
        current_depth = depth
        drop_line = False
        for rule in rules:
            if rule.kind == "line_contains" and rule.name in line:
                drop_line = True
                changed = True
                break
            if current_depth == 1 and rule.kind == "field" and _is_matching_field_declaration(line, rule.name):
                drop_line = True
                changed = True
                break
        if not drop_line:
            out.append(line)
        depth += line.count("{") - line.count("}")
    return "".join(out), changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply data-driven post-rebuild cleanups in client/refactor.")
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_PLAN_DIR)
    ap.add_argument("--summary-json", type=Path, default=Path("build/refactor-state/cleanup-apply-summary.json"))
    ap.add_argument("--dry-run", action="store_true", help="Analyze matches/unmatches without writing files.")
    args = ap.parse_args()
    resolve_path_args(args, ("src_dir", "plan_dir", "summary_json"))

    src_dir = args.src_dir.resolve()
    plan_dir = args.plan_dir.resolve()
    reset_rules = _load_reset_rules(plan_dir / "reset_methods.csv")
    rewrite_rules = _load_rewrite_rules(plan_dir / "delegation_rewrites.csv")
    signature_rules = _load_signature_rules(plan_dir / "signature_rewrites.csv")
    signature_callsite_drop_rules = _build_signature_callsite_drop_rules(signature_rules)
    noarg_call_rules = _load_noarg_call_rules(plan_dir / "call_rewrites.csv")
    drop_first_arg_call_rules = _load_drop_first_arg_call_rules(plan_dir / "call_arg_rewrites.csv")
    drop_member_rules = _load_drop_member_rules(plan_dir / "drop_members.csv")

    changed_files = 0
    unmatched_entries: list[dict[str, object]] = []
    stage_stats: dict[str, RuleStageStats] = {
        "reset_methods": RuleStageStats(total=len(reset_rules)),
        "delegation_rewrites": RuleStageStats(total=len(rewrite_rules)),
        "signature_rewrites": RuleStageStats(total=len(signature_rules)),
        "signature_callsite_drop": RuleStageStats(total=len(signature_callsite_drop_rules)),
        "call_rewrites": RuleStageStats(total=len(noarg_call_rules)),
        "call_arg_rewrites": RuleStageStats(total=len(drop_first_arg_call_rules)),
        "drop_members": RuleStageStats(total=len(drop_member_rules)),
    }

    for rule in reset_rules:
        path = (src_dir / rule.target_file).resolve()
        if not path.exists():
            stage_stats["reset_methods"].missing_file += 1
            _push_unmatched(
                unmatched_entries,
                stage="reset_methods",
                file=rule.target_file,
                reason="missing_file",
                rule=f"{rule.owner_class}.{rule.reset_method} -> {rule.field_name}",
                plan_file="reset_methods.csv",
                row_key={
                    "owner_class": rule.owner_class,
                    "target_file": rule.target_file,
                    "reset_method": rule.reset_method,
                    "field_name": rule.field_name,
                    "reset_value": rule.reset_value,
                },
            )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        desired_body = [f"        {rule.field_name} = {rule.reset_value};"]
        out, changed = _rewrite_method_body(text, rule.reset_method, desired_body)
        if not changed:
            method_src = (
                f"    static final void {rule.reset_method}() {{\n"
                f"{desired_body[0]}\n"
                f"    }}"
            )
            out, changed = _ensure_method(text, rule.reset_method, method_src)
        if changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1
            stage_stats["reset_methods"].matched += 1
        else:
            stage_stats["reset_methods"].unmatched += 1
            _push_unmatched(
                unmatched_entries,
                stage="reset_methods",
                file=rule.target_file,
                reason="no_match",
                rule=f"{rule.owner_class}.{rule.reset_method} -> {rule.field_name}",
                plan_file="reset_methods.csv",
                row_key={
                    "owner_class": rule.owner_class,
                    "target_file": rule.target_file,
                    "reset_method": rule.reset_method,
                    "field_name": rule.field_name,
                    "reset_value": rule.reset_value,
                },
            )

    by_file: dict[Path, list[DelegationRewriteRule]] = {}
    for rule in rewrite_rules:
        by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            stage_stats["delegation_rewrites"].missing_file += len(rules)
            for rule in rules:
                _push_unmatched(
                    unmatched_entries,
                    stage="delegation_rewrites",
                    file=rule.file,
                    reason="missing_file",
                    rule=f"{rule.owner_class}.{rule.method}: {rule.field_name}={rule.reset_value}",
                    plan_file="delegation_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "method": rule.method,
                        "owner_class": rule.owner_class,
                        "field_name": rule.field_name,
                        "reset_value": rule.reset_value,
                        "rewrite_to": rule.rewrite_to,
                    },
                )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _apply_delegation_rewrite(out, rule)
            any_changed = any_changed or changed
            if changed:
                stage_stats["delegation_rewrites"].matched += 1
            else:
                stage_stats["delegation_rewrites"].unmatched += 1
                _push_unmatched(
                    unmatched_entries,
                    stage="delegation_rewrites",
                    file=rule.file,
                    reason="no_match",
                    rule=f"{rule.owner_class}.{rule.method}: {rule.field_name}={rule.reset_value}",
                    plan_file="delegation_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "method": rule.method,
                        "owner_class": rule.owner_class,
                        "field_name": rule.field_name,
                        "reset_value": rule.reset_value,
                        "rewrite_to": rule.rewrite_to,
                    },
                )
        if any_changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1

    sig_by_file: dict[Path, list[SignatureRewriteRule]] = {}
    for rule in signature_rules:
        sig_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(sig_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            stage_stats["signature_rewrites"].missing_file += len(rules)
            for rule in rules:
                _push_unmatched(
                    unmatched_entries,
                    stage="signature_rewrites",
                    file=rule.file,
                    reason="missing_file",
                    rule=f"{rule.owner}.{rule.method}:{rule.op}",
                    plan_file="signature_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "owner": rule.owner,
                        "method": rule.method,
                        "signature_before": rule.signature_before,
                        "signature_after": rule.signature_after,
                        "op": rule.op,
                        "param_index": str(rule.param_index if rule.param_index >= 0 else ""),
                        "new_name": rule.new_name,
                        "match_text": rule.match_text,
                    },
                )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _rewrite_signature_semantic(out, rule)
            any_changed = any_changed or changed
            if changed:
                stage_stats["signature_rewrites"].matched += 1
            else:
                stage_stats["signature_rewrites"].unmatched += 1
                _push_unmatched(
                    unmatched_entries,
                    stage="signature_rewrites",
                    file=rule.file,
                    reason="no_match",
                    rule=f"{rule.owner}.{rule.method}:{rule.op}",
                    plan_file="signature_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "owner": rule.owner,
                        "method": rule.method,
                        "signature_before": rule.signature_before,
                        "signature_after": rule.signature_after,
                        "op": rule.op,
                        "param_index": str(rule.param_index if rule.param_index >= 0 else ""),
                        "new_name": rule.new_name,
                        "match_text": rule.match_text,
                    },
                )
        if any_changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1

    matched_signature_callsite_rules: set[tuple[str, str, int, int]] = set()
    for path in sorted(src_dir.rglob("*.java")):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out, changed, matched_rules = _rewrite_calls_for_signature_drop_rules(text, signature_callsite_drop_rules)
        matched_signature_callsite_rules.update(matched_rules)
        if changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1
    stage_stats["signature_callsite_drop"].matched = len(matched_signature_callsite_rules)
    stage_stats["signature_callsite_drop"].unmatched = (
        max(0, stage_stats["signature_callsite_drop"].total - stage_stats["signature_callsite_drop"].matched)
    )
    for rule in signature_callsite_drop_rules:
        key = (rule.method, rule.owner_class, rule.arg_arity, rule.arg_index)
        if key in matched_signature_callsite_rules:
            continue
        _push_unmatched(
            unmatched_entries,
            stage="signature_callsite_drop",
            file="",
            reason="no_callsite_match",
            rule=f"{rule.owner_class}.{rule.method}/{rule.arg_arity} drop_arg[{rule.arg_index}]",
            plan_file="signature_rewrites.csv",
        )

    calls_by_file: dict[Path, list[NoArgCallRewriteRule]] = {}
    for rule in noarg_call_rules:
        calls_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(calls_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            stage_stats["call_rewrites"].missing_file += len(rules)
            for rule in rules:
                _push_unmatched(
                    unmatched_entries,
                    stage="call_rewrites",
                    file=rule.file,
                    reason="missing_file",
                    rule=rule.method,
                    plan_file="call_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "method": rule.method,
                    },
                )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _rewrite_noarg_calls(out, rule)
            any_changed = any_changed or changed
            if changed:
                stage_stats["call_rewrites"].matched += 1
            else:
                stage_stats["call_rewrites"].unmatched += 1
                _push_unmatched(
                    unmatched_entries,
                    stage="call_rewrites",
                    file=rule.file,
                    reason="no_match",
                    rule=rule.method,
                    plan_file="call_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "method": rule.method,
                    },
                )
        if any_changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1

    drop_first_by_file: dict[Path, list[DropFirstArgCallRewriteRule]] = {}
    for rule in drop_first_arg_call_rules:
        drop_first_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(drop_first_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            stage_stats["call_arg_rewrites"].missing_file += len(rules)
            for rule in rules:
                _push_unmatched(
                    unmatched_entries,
                    stage="call_arg_rewrites",
                    file=rule.file,
                    reason="missing_file",
                    rule=f"{rule.owner_class}.{rule.method} arg[{rule.arg_index}]={rule.arg_text}",
                    plan_file="call_arg_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "owner_class": rule.owner_class,
                        "method": rule.method,
                        "arg_text": rule.arg_text,
                        "arg_index": str(rule.arg_index),
                    },
                )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _rewrite_drop_first_arg_calls(out, rule)
            any_changed = any_changed or changed
            if changed:
                stage_stats["call_arg_rewrites"].matched += 1
            else:
                stage_stats["call_arg_rewrites"].unmatched += 1
                _push_unmatched(
                    unmatched_entries,
                    stage="call_arg_rewrites",
                    file=rule.file,
                    reason="no_match",
                    rule=f"{rule.owner_class}.{rule.method} arg[{rule.arg_index}]={rule.arg_text}",
                    plan_file="call_arg_rewrites.csv",
                    row_key={
                        "file": rule.file,
                        "owner_class": rule.owner_class,
                        "method": rule.method,
                        "arg_text": rule.arg_text,
                        "arg_index": str(rule.arg_index),
                    },
                )
        if any_changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1

    drops_by_file: dict[Path, list[DropMemberRule]] = {}
    for rule in drop_member_rules:
        drops_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(drops_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            stage_stats["drop_members"].missing_file += len(rules)
            for rule in rules:
                _push_unmatched(
                    unmatched_entries,
                    stage="drop_members",
                    file=rule.file,
                    reason="missing_file",
                    rule=f"{rule.kind}:{rule.name}",
                    plan_file="drop_members.csv",
                    row_key={
                        "file": rule.file,
                        "kind": rule.kind,
                        "name": rule.name,
                    },
                )
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            next_text, changed = _drop_members(out, [rule])
            if changed:
                stage_stats["drop_members"].matched += 1
                out = next_text
            else:
                stage_stats["drop_members"].unmatched += 1
                _push_unmatched(
                    unmatched_entries,
                    stage="drop_members",
                    file=rule.file,
                    reason="no_match",
                    rule=f"{rule.kind}:{rule.name}",
                    plan_file="drop_members.csv",
                    row_key={
                        "file": rule.file,
                        "kind": rule.kind,
                        "name": rule.name,
                    },
                )
            any_changed = any_changed or changed
        if any_changed and out != text:
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")
            changed_files += 1

    totals = RuleStageStats()
    for stats in stage_stats.values():
        totals.total += stats.total
        totals.matched += stats.matched
        totals.unmatched += stats.unmatched
        totals.missing_file += stats.missing_file
    unmatched_file_counts = Counter((entry.get("file") or "<all-files>") for entry in unmatched_entries)
    unmatched_top_files = [
        {"file": file, "count": count}
        for file, count in unmatched_file_counts.most_common(25)
    ]
    summary = {
        "dry_run": args.dry_run,
        "changed_files": changed_files,
        "totals": totals.as_dict(),
        "stages": {name: stats.as_dict() for name, stats in stage_stats.items()},
        "unmatched_top_files": unmatched_top_files,
        "unmatched_rows": unmatched_entries,
        "unmatched_examples": unmatched_entries[:200],
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        "Applied refactor cleanup rules. "
        f"dry_run={args.dry_run} changed_files={changed_files} total_rules={totals.total} matched={totals.matched} "
        f"unmatched={totals.unmatched} missing_file={totals.missing_file}"
    )
    for stage_name, stats in stage_stats.items():
        print(
            f"  {stage_name}: total={stats.total} matched={stats.matched} "
            f"unmatched={stats.unmatched} missing_file={stats.missing_file}"
        )
    print(f"  summary_json={args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
