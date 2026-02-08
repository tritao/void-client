#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import REFACTOR_PLAN_DIR, REFACTOR_SRC_DIR
from tools.refactor.common.io import non_comment_csv_lines


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
class QualifiedCallRewriteRule:
    file: str
    owner_class: str
    old_method: str
    new_method: str


@dataclass(frozen=True)
class MethodIdentifierRewriteRule:
    file: str
    method: str
    old: str
    new: str


@dataclass(frozen=True)
class FileIdentifierRewriteRule:
    file: str
    old: str
    new: str


@dataclass(frozen=True)
class DropMemberRule:
    file: str
    kind: str
    name: str


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
        out.append(DropFirstArgCallRewriteRule(file=file, owner_class=owner_class, method=method, arg_text=arg_text, arg_index=arg_index))
    return out


def _load_method_identifier_rewrite_rules(path: Path) -> list[MethodIdentifierRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[MethodIdentifierRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        method = (row.get("method") or "").strip()
        old = (row.get("old") or "").strip()
        new = (row.get("new") or "").strip()
        if not (file and method and old and new):
            continue
        out.append(MethodIdentifierRewriteRule(file=file, method=method, old=old, new=new))
    return out


def _load_file_identifier_rewrite_rules(path: Path) -> list[FileIdentifierRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[FileIdentifierRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        old = (row.get("old") or "").strip()
        new = (row.get("new") or "").strip()
        if not (file and old and new):
            continue
        out.append(FileIdentifierRewriteRule(file=file, old=old, new=new))
    return out


def _load_qualified_call_rewrite_rules(path: Path) -> list[QualifiedCallRewriteRule]:
    if not path.exists():
        return []
    rows = non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[QualifiedCallRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        owner_class = (row.get("owner_class") or "").strip()
        old_method = (row.get("old_method") or "").strip()
        new_method = (row.get("new_method") or "").strip()
        if not (file and owner_class and old_method and new_method):
            continue
        out.append(
            QualifiedCallRewriteRule(
                file=file,
                owner_class=owner_class,
                old_method=old_method,
                new_method=new_method,
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
        else:
            renamed = _rename_param_decl(params[idx], rule.new_name)
            if renamed == params[idx]:
                return text, False
            params[idx] = renamed
        new_params = ", ".join(params)
        start = int(target["params_start"])
        end = int(target["params_end"])
        if text[start:end] == new_params:
            return text, False
        return text[:start] + new_params + text[end:], True

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


def _rewrite_method_identifiers(text: str, rules: list[MethodIdentifierRewriteRule]) -> tuple[str, bool]:
    out = text
    any_changed = False
    for rule in rules:
        span = _find_method_block_span(out, rule.method)
        if span is None:
            continue
        start, end = span
        block = out[start:end]
        rx = re.compile(rf"\b{re.escape(rule.old)}\b")
        new_block, count = rx.subn(rule.new, block)
        if count > 0:
            out = out[:start] + new_block + out[end:]
            any_changed = True
    return out, any_changed


def _rewrite_qualified_calls(text: str, rules: list[QualifiedCallRewriteRule]) -> tuple[str, bool]:
    out = text
    any_changed = False
    for rule in rules:
        rx = re.compile(rf"\b{re.escape(rule.owner_class)}\.{re.escape(rule.old_method)}\s*\(")
        out, count = rx.subn(f"{rule.owner_class}.{rule.new_method}(", out)
        if count > 0:
            any_changed = True
    return out, any_changed


def _rewrite_file_identifiers(text: str, rules: list[FileIdentifierRewriteRule]) -> tuple[str, bool]:
    out = text
    any_changed = False
    for rule in rules:
        rx = re.compile(rf"\b{re.escape(rule.old)}\b")
        out, count = rx.subn(rule.new, out)
        if count > 0:
            any_changed = True
    return out, any_changed


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
    args = ap.parse_args()
    resolve_path_args(args, ("src_dir", "plan_dir"))

    src_dir = args.src_dir.resolve()
    plan_dir = args.plan_dir.resolve()
    reset_rules = _load_reset_rules(plan_dir / "reset_methods.csv")
    rewrite_rules = _load_rewrite_rules(plan_dir / "delegation_rewrites.csv")
    signature_rules = _load_signature_rules(plan_dir / "signature_rewrites.csv")
    noarg_call_rules = _load_noarg_call_rules(plan_dir / "call_rewrites.csv")
    drop_first_arg_call_rules = _load_drop_first_arg_call_rules(plan_dir / "call_arg_rewrites.csv")
    method_identifier_rules = _load_method_identifier_rewrite_rules(plan_dir / "method_identifier_rewrites.csv")
    file_identifier_rules = _load_file_identifier_rewrite_rules(plan_dir / "file_identifier_rewrites.csv")
    qualified_call_rules = _load_qualified_call_rewrite_rules(plan_dir / "qualified_call_rewrites.csv")
    drop_member_rules = _load_drop_member_rules(plan_dir / "drop_members.csv")

    changed_files = 0

    for rule in reset_rules:
        path = (src_dir / rule.target_file).resolve()
        if not path.exists():
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
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    by_file: dict[Path, list[DelegationRewriteRule]] = {}
    for rule in rewrite_rules:
        by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _apply_delegation_rewrite(out, rule)
            any_changed = any_changed or changed
        if any_changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    sig_by_file: dict[Path, list[SignatureRewriteRule]] = {}
    for rule in signature_rules:
        sig_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(sig_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _rewrite_signature_semantic(out, rule)
            any_changed = any_changed or changed
        if any_changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    calls_by_file: dict[Path, list[NoArgCallRewriteRule]] = {}
    for rule in noarg_call_rules:
        calls_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(calls_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _rewrite_noarg_calls(out, rule)
            any_changed = any_changed or changed
        if any_changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    drop_first_by_file: dict[Path, list[DropFirstArgCallRewriteRule]] = {}
    for rule in drop_first_arg_call_rules:
        drop_first_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(drop_first_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out = text
        any_changed = False
        for rule in rules:
            out, changed = _rewrite_drop_first_arg_calls(out, rule)
            any_changed = any_changed or changed
        if any_changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    method_id_by_file: dict[Path, list[MethodIdentifierRewriteRule]] = {}
    for rule in method_identifier_rules:
        method_id_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(method_id_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out, changed = _rewrite_method_identifiers(text, rules)
        if changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    file_id_by_file: dict[Path, list[FileIdentifierRewriteRule]] = {}
    for rule in file_identifier_rules:
        file_id_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(file_id_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out, changed = _rewrite_file_identifiers(text, rules)
        if changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    qualified_calls_by_file: dict[Path, list[QualifiedCallRewriteRule]] = {}
    for rule in qualified_call_rules:
        qualified_calls_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(qualified_calls_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out, changed = _rewrite_qualified_calls(text, rules)
        if changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    drops_by_file: dict[Path, list[DropMemberRule]] = {}
    for rule in drop_member_rules:
        drops_by_file.setdefault((src_dir / rule.file).resolve(), []).append(rule)
    for path, rules in sorted(drops_by_file.items(), key=lambda item: str(item[0])):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        out, changed = _drop_members(text, rules)
        if changed and out != text:
            path.write_text(out, encoding="utf-8")
            changed_files += 1

    print(f"Applied refactor cleanup rules. changed_files={changed_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
