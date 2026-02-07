#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path


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
    method: str
    old_param_text: str
    new_param_text: str
    drop_line_contains: str


@dataclass(frozen=True)
class NoArgCallRewriteRule:
    file: str
    method: str


@dataclass(frozen=True)
class DropFirstArgCallRewriteRule:
    file: str
    method: str
    first_arg_text: str


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


def _non_comment_csv_lines(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(raw)
    return out


def _load_reset_rules(path: Path) -> list[ResetMethodRule]:
    if not path.exists():
        return []
    rows = _non_comment_csv_lines(path)
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
    rows = _non_comment_csv_lines(path)
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
    rows = _non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[SignatureRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        method = (row.get("method") or "").strip()
        old_param_text = (row.get("old_param_text") or "").strip()
        new_param_text = (row.get("new_param_text") or "").strip()
        drop_line_contains = (row.get("drop_line_contains") or "").strip()
        if not (file and method):
            continue
        out.append(
            SignatureRewriteRule(
                file=file,
                method=method,
                old_param_text=old_param_text,
                new_param_text=new_param_text,
                drop_line_contains=drop_line_contains,
            )
        )
    return out


def _load_noarg_call_rules(path: Path) -> list[NoArgCallRewriteRule]:
    if not path.exists():
        return []
    rows = _non_comment_csv_lines(path)
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
    rows = _non_comment_csv_lines(path)
    if not rows:
        return []
    reader = csv.DictReader(rows)
    out: list[DropFirstArgCallRewriteRule] = []
    for row in reader:
        file = (row.get("file") or "").strip()
        method = (row.get("method") or "").strip()
        first_arg_text = (row.get("first_arg_text") or "").strip()
        if not (file and method and first_arg_text):
            continue
        out.append(DropFirstArgCallRewriteRule(file=file, method=method, first_arg_text=first_arg_text))
    return out


def _load_method_identifier_rewrite_rules(path: Path) -> list[MethodIdentifierRewriteRule]:
    if not path.exists():
        return []
    rows = _non_comment_csv_lines(path)
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
    rows = _non_comment_csv_lines(path)
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
    rows = _non_comment_csv_lines(path)
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
    rows = _non_comment_csv_lines(path)
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


def _rewrite_signature_and_body(text: str, rule: SignatureRewriteRule) -> tuple[str, bool]:
    span = _find_method_body_span(text, rule.method)
    if span is None:
        old_param = re.escape(rule.old_param_text)
        if rule.old_param_text:
            decl_rx = re.compile(rf"(\b{re.escape(rule.method)}\s*\()\s*{old_param}\s*(\)\s*;)")
        else:
            decl_rx = re.compile(rf"(\b{re.escape(rule.method)}\s*\()\s*(\)\s*;)")
        new_text, count = decl_rx.subn(rf"\1{rule.new_param_text}\2", text, count=1)
        return new_text, count > 0
    start, end = span
    header = text[:start]
    body = text[start:end]
    tail = text[end:]

    old_param = re.escape(rule.old_param_text)
    if rule.old_param_text:
        sig_rx = re.compile(rf"(\b{re.escape(rule.method)}\s*\()\s*{old_param}\s*(\))")
    else:
        sig_rx = re.compile(rf"(\b{re.escape(rule.method)}\s*\()\s*(\))")
    new_header, sig_count = sig_rx.subn(rf"\1{rule.new_param_text}\2", header, count=1)

    new_body = body
    body_changed = False
    if rule.drop_line_contains:
        kept_lines: list[str] = []
        for raw in new_body.splitlines():
            if rule.drop_line_contains in raw:
                body_changed = True
                continue
            kept_lines.append(raw)
        new_body = "\n".join(kept_lines)

    changed = sig_count > 0 or body_changed
    if not changed:
        return text, False
    return new_header + new_body + tail, True


def _rewrite_noarg_calls(text: str, rule: NoArgCallRewriteRule) -> tuple[str, bool]:
    rx = re.compile(rf"(\.{re.escape(rule.method)})\s*\(\s*[^()]*\)")
    new_text, count = rx.subn(r"\1()", text)
    return new_text, count > 0


def _rewrite_drop_first_arg_calls(text: str, rule: DropFirstArgCallRewriteRule) -> tuple[str, bool]:
    rx = re.compile(
        rf"(\.{re.escape(rule.method)}\s*\()\s*{re.escape(rule.first_arg_text)}\s*,\s*([^()]+?)\s*(\))"
    )
    new_text, count = rx.subn(r"\1\2\3", text)
    return new_text, count > 0


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
    lines = text.splitlines(keepends=True)
    depth = 0
    changed = False
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
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--plan-dir", type=Path, default=Path("client/refactor/.refactor-plan"))
    args = ap.parse_args()

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
            out, changed = _rewrite_signature_and_body(out, rule)
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
