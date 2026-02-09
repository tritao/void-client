#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.logging import get_logger, log_event
from tools.refactor.common.constants import (
    REFACTOR_CLEANUP_PLAN_DIR,
    REFACTOR_CLEANUP_SUMMARY_JSON,
    REFACTOR_EXTRACT_MANIFEST_DIR,
    REFACTOR_PLAN_DIR,
    REFACTOR_SRC_DIR,
)
from tools.refactor.common.java_index import JavaIndex
from tools.refactor.common.io import read_csv_dict_rows, write_csv_dict_rows
from tools.refactor.extract.manifest import load_manifests

CLEANUP_PLAN_FILES = [
    "reset_methods.csv",
    "delegation_rewrites.csv",
    "signature_rewrites.csv",
    "call_rewrites.csv",
    "call_arg_rewrites.csv",
    "drop_members.csv",
]

DEDUP_KEYS_BY_FILE: dict[str, tuple[str, ...]] = {
    "reset_methods.csv": ("owner_class", "target_file", "reset_method", "field_name", "reset_value"),
    "delegation_rewrites.csv": ("file", "method", "owner_class", "field_name", "reset_value", "rewrite_to"),
    "signature_rewrites.csv": (
        "file",
        "owner",
        "method",
        "signature_before",
        "signature_after",
        "op",
        "param_index",
        "new_name",
        "match_text",
    ),
    "call_rewrites.csv": ("file", "method"),
    "call_arg_rewrites.csv": ("file", "owner_class", "method", "arg_text", "arg_index"),
    "drop_members.csv": ("file", "kind", "name"),
}


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


def _param_name_from_decl(param_decl: str) -> str:
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", (param_decl or "").strip())
    return m.group(1) if m else ""


def _find_method_declarations(text: str, method_name: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    decls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
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
        params_text = text[open_paren + 1 : close_paren]
        params = _split_top_level_commas(params_text)
        sig = tuple(_param_type_from_decl(p) for p in params if p.strip())
        param_names = tuple(_param_name_from_decl(p) for p in params if p.strip())
        decls.append((sig, param_names))
        pos = close_paren + 1
    return decls


def _signature_text(sig: tuple[str, ...] | None) -> str:
    if sig is None:
        return ""
    if len(sig) == 0:
        return "()"
    return ",".join(sig)


def _is_safe_single_line_guard(match_text: str) -> bool:
    text = (match_text or "").strip()
    if not text:
        return False
    if "{" in text or "}" in text:
        return False
    if "else" in text.lower():
        return False
    if not text.endswith(";"):
        return False
    return True


_UNPARSED = object()


def _strip_wrapping_parentheses(value: str) -> str:
    out = value.strip()
    while out.startswith("(") and out.endswith(")") and len(out) >= 2:
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
    cast_rx = re.compile(r"^\(\s*[A-Za-z_$][A-Za-z0-9_$\[\]]*\s*\)\s*(.+)$")
    while True:
        m = cast_rx.match(out)
        if not m:
            break
        out = m.group(1).strip()
    return out


def _parse_literal_value(value: str):
    text = _strip_wrapping_parentheses(_strip_casts(value).strip())
    if not text:
        return _UNPARSED
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    if low.endswith("l") and len(low) > 1:
        low = low[:-1]
    if re.match(r"^[+-]?(?:0x[0-9a-f]+|\d+)$", low):
        try:
            return int(low, 0)
        except ValueError:
            return _UNPARSED
    return _UNPARSED


def _flip_operator(op: str) -> str:
    return {
        "<": ">",
        "<=": ">=",
        ">": "<",
        ">=": "<=",
        "==": "==",
        "!=": "!=",
    }.get(op, op)


def _eval_guard(op: str, const_value, arg_value) -> bool | None:
    if op == "==":
        return arg_value == const_value
    if op == "!=":
        return arg_value != const_value
    if (
        not isinstance(const_value, int)
        or isinstance(const_value, bool)
        or not isinstance(arg_value, int)
        or isinstance(arg_value, bool)
    ):
        return None
    if op == "<":
        return arg_value < const_value
    if op == "<=":
        return arg_value <= const_value
    if op == ">":
        return arg_value > const_value
    if op == ">=":
        return arg_value >= const_value
    return None


def _is_identifier(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", (value or "").strip()))


def _is_recursive_guard_trap(
    *,
    match_text: str,
    method: str,
    param_names: tuple[str, ...],
) -> bool:
    text = (match_text or "").strip()
    method_name = (method or "").strip()
    if not text or not method_name:
        return False
    line_rx = re.compile(
        r"^if\s*\((?P<cond>[^)]*)\)\s*(?:(?P<owner>[A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*)?(?P<callee>[A-Za-z_$][A-Za-z0-9_$]*)\s*\((?P<args>[^;]*)\)\s*;\s*$"
    )
    m = line_rx.match(text)
    if not m:
        return False
    callee = (m.group("callee") or "").strip()
    if callee != method_name:
        return False
    cond = (m.group("cond") or "").strip()
    cond_rx = re.compile(r"^(?P<lhs>.+?)\s*(?P<op>==|!=|<=|>=|<|>)\s*(?P<rhs>.+?)$")
    cm = cond_rx.match(cond)
    if not cm:
        return False
    lhs = (cm.group("lhs") or "").strip()
    rhs = (cm.group("rhs") or "").strip()
    op = (cm.group("op") or "").strip()

    param_name = ""
    const_value = _UNPARSED
    effective_op = op
    if _is_identifier(lhs):
        parsed_rhs = _parse_literal_value(rhs)
        if parsed_rhs is not _UNPARSED:
            param_name = lhs
            const_value = parsed_rhs
    if not param_name and _is_identifier(rhs):
        parsed_lhs = _parse_literal_value(lhs)
        if parsed_lhs is not _UNPARSED:
            param_name = rhs
            const_value = parsed_lhs
            effective_op = _flip_operator(op)
    if not param_name or const_value is _UNPARSED:
        return False
    try:
        param_index = param_names.index(param_name)
    except ValueError:
        return False

    args_text = (m.group("args") or "").strip()
    args = _split_top_level_commas(args_text) if args_text else []
    if param_index < 0 or param_index >= len(args):
        return False
    arg_value = _parse_literal_value(args[param_index])
    if arg_value is _UNPARSED:
        return False
    result = _eval_guard(effective_op, const_value, arg_value)
    return result is True


def _infer_unique_method_decl(
    *,
    java_index: JavaIndex,
    file: str,
    method: str,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    text = java_index.text_by_file.get(Path(file))
    if not text:
        return None
    decls = _find_method_declarations(text, method)
    if not decls:
        return None
    unique_sigs = sorted(set(sig for sig, _ in decls))
    if len(unique_sigs) != 1:
        return None
    matches = [(sig, params) for sig, params in decls if sig == unique_sigs[0]]
    if len(matches) != 1:
        return None
    return matches[0]


def _load_guard_promotion_rows(plan_dir: Path, java_index: JavaIndex) -> list[dict[str, str]]:
    path = plan_dir / "generated" / "unified_cleanup_candidates.csv"
    header, rows = read_csv_dict_rows(path)
    if not header:
        return []
    by_id = {(row.get("id") or "").strip(): row for row in rows if (row.get("id") or "").strip()}
    out: list[dict[str, str]] = []
    for row in rows:
        if (row.get("rule_type") or "").strip() != "guard_dead_by_callers":
            continue
        if (row.get("confidence") or "").strip().lower() != "high":
            continue
        action = (row.get("action") or row.get("action_hint") or "").strip()
        if action != "signature_rewrites.drop_statement_contains":
            continue
        gate_status = (row.get("gate_status") or "").strip().lower()
        if gate_status and gate_status != "auto":
            continue
        dep = (row.get("dependency") or "").strip()
        root = by_id.get(dep, {})
        match_text = (root.get("match") or "").strip()
        if not match_text:
            continue
        if not _is_safe_single_line_guard(match_text):
            continue
        file = (row.get("file") or "").strip()
        owner = (row.get("owner") or "").strip()
        method = (row.get("member") or "").strip()
        if not (file and owner and method):
            continue
        decl = _infer_unique_method_decl(java_index=java_index, file=file, method=method)
        sig_before_text = _signature_text(decl[0] if decl else None)
        note_reason = (row.get("gate_reason") or "").strip() or "auto"
        out.append(
            {
                "file": file,
                "owner": owner,
                "method": method,
                "signature_before": sig_before_text,
                "signature_after": sig_before_text,
                "op": "drop_statement_contains",
                "param_index": "",
                "new_name": "",
                "match_text": match_text,
                "phase": "cleanup",
                "status": "approved",
                "notes": f"Auto-promoted from unified cleanup ({note_reason})",
            }
        )
    for row in rows:
        rule_type = (row.get("rule_type") or "").strip()
        if rule_type not in {"guard_param", "guard_call"}:
            continue
        if (row.get("confidence") or "").strip().lower() != "high":
            continue
        action = (row.get("action") or row.get("action_hint") or "").strip()
        if "drop_statement_contains" not in action:
            continue
        match_text = (row.get("match") or "").strip()
        if not match_text or not _is_safe_single_line_guard(match_text):
            continue
        file = (row.get("file") or "").strip()
        owner = (row.get("owner") or "").strip()
        method = (row.get("member") or "").strip()
        if not (file and owner and method):
            continue
        decl = _infer_unique_method_decl(java_index=java_index, file=file, method=method)
        if not decl:
            continue
        sig_before, param_names = decl
        if not _is_recursive_guard_trap(match_text=match_text, method=method, param_names=param_names):
            continue
        sig_before_text = _signature_text(sig_before)
        out.append(
            {
                "file": file,
                "owner": owner,
                "method": method,
                "signature_before": sig_before_text,
                "signature_after": sig_before_text,
                "op": "drop_statement_contains",
                "param_index": "",
                "new_name": "",
                "match_text": match_text,
                "phase": "cleanup",
                "status": "approved",
                "notes": "Auto-promoted from unified cleanup (recursion_trap_proven)",
            }
        )
    return out


def _append_signature_promotions(
    rows: list[dict[str, str]],
    promoted_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    keys = {
        (
            (row.get("file") or "").strip(),
            (row.get("owner") or "").strip(),
            (row.get("method") or "").strip(),
            (row.get("op") or "").strip(),
            (row.get("signature_before") or "").strip(),
            (row.get("signature_after") or "").strip(),
            (row.get("param_index") or "").strip(),
            (row.get("new_name") or "").strip(),
            (row.get("match_text") or "").strip(),
        )
        for row in rows
    }
    out = list(rows)
    added = 0
    for row in promoted_rows:
        key = (
            (row.get("file") or "").strip(),
            (row.get("owner") or "").strip(),
            (row.get("method") or "").strip(),
            (row.get("op") or "").strip(),
            (row.get("signature_before") or "").strip(),
            (row.get("signature_after") or "").strip(),
            (row.get("param_index") or "").strip(),
            (row.get("new_name") or "").strip(),
            (row.get("match_text") or "").strip(),
        )
        if not key[0] or not key[1] or not key[2] or not key[3]:
            continue
        if key in keys:
            continue
        keys.add(key)
        out.append(row)
        added += 1
    return out, added


def _dedupe_rows(rows: list[dict[str, str]], key_cols: tuple[str, ...]) -> tuple[list[dict[str, str]], int]:
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, str]] = []
    removed = 0
    for row in rows:
        key = tuple((row.get(col) or "").strip() for col in key_cols)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        out.append(row)
    return out, removed


def _prune_stale_rows(
    *,
    python: str,
    src_dir: Path,
    plan_dir: Path,
    dryrun_summary_json: Path,
    prune_summary_json: Path,
) -> tuple[int, int]:
    dryrun_summary_json.parent.mkdir(parents=True, exist_ok=True)
    prune_summary_json.parent.mkdir(parents=True, exist_ok=True)
    apply_cmd = [
        python,
        "-m",
        "tools.refactor.cli.apply_refactor_cleanup",
        "--src-dir",
        str(src_dir),
        "--plan-dir",
        str(plan_dir),
        "--summary-json",
        str(dryrun_summary_json),
        "--dry-run",
    ]
    apply_proc = subprocess.run(apply_cmd, check=False)
    if apply_proc.returncode != 0:
        raise RuntimeError(f"stale prune preflight failed ({apply_proc.returncode})")
    prune_cmd = [
        python,
        "-m",
        "tools.refactor.cli.prune_cleanup_plan",
        "--plan-dir",
        str(plan_dir),
        "--summary-json",
        str(dryrun_summary_json),
        "--out-summary-json",
        str(prune_summary_json),
    ]
    prune_proc = subprocess.run(prune_cmd, check=False)
    if prune_proc.returncode != 0:
        raise RuntimeError(f"stale prune failed ({prune_proc.returncode})")
    try:
        summary = json.loads(prune_summary_json.read_text(encoding="utf-8"))
    except Exception:
        summary = {}
    return int(summary.get("rows_removed") or 0), int(summary.get("rows_after") or 0)


def _load_aliases(plan_dir: Path) -> dict[tuple[str, str, str], set[str]]:
    aliases: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for csv_path in sorted(plan_dir.glob("*.rename.csv")):
        header, rows = read_csv_dict_rows(csv_path)
        if not header:
            continue
        needed = {"kind", "owner", "old", "new"}
        if not needed.issubset(set(header)):
            continue
        for row in rows:
            kind = row.get("kind", "").strip().lower()
            owner = row.get("owner", "").strip()
            old = row.get("old", "").strip()
            new = row.get("new", "").strip()
            if kind not in ("method", "field") or not owner or not old or not new:
                continue
            aliases[(owner, kind, old)].add(new)
            aliases[(owner, kind, new)].add(old)
    return aliases


def _load_move_targets(manifest_dir: Path) -> dict[tuple[str, str, str], set[str]]:
    out: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for manifest in load_manifests(manifest_dir):
        source_owner = manifest.source.stem
        target_owner = manifest.target_class
        for move in manifest.moves:
            if move.kind not in ("method", "field"):
                continue
            out[(move.kind, source_owner, move.name)].add(target_owner)
    return out


def _resolve_owner(
    *,
    owner: str,
    kind: str,
    member: str,
    aliases: dict[tuple[str, str, str], set[str]],
    move_targets: dict[tuple[str, str, str], set[str]],
) -> tuple[str, str]:
    if not owner or not kind or not member:
        return owner, "skip"
    cur_owner = owner
    cur_member = member
    for _ in range(16):
        names = {cur_member}
        names.update(aliases.get((cur_owner, kind, cur_member), set()))
        targets: set[str] = set()
        picked_member = ""
        for candidate in sorted(names):
            t = move_targets.get((kind, cur_owner, candidate), set())
            if t:
                targets.update(t)
                picked_member = candidate
        if not targets:
            return cur_owner, "unchanged" if cur_owner == owner else "remapped"
        if len(targets) > 1:
            return cur_owner, f"ambiguous:{cur_owner}.{picked_member}"
        next_owner = next(iter(targets))
        if next_owner == cur_owner:
            return cur_owner, "unchanged" if cur_owner == owner else "remapped"
        cur_owner = next_owner
    return cur_owner, "max-hops"


def _remap_owner_columns(
    *,
    rows: list[dict[str, str]],
    aliases: dict[tuple[str, str, str], set[str]],
    move_targets: dict[tuple[str, str, str], set[str]],
    java_index: JavaIndex,
    allow_file_remap: bool,
) -> tuple[list[dict[str, str]], int, int, list[str]]:
    remapped = 0
    file_remapped = 0
    warnings: list[str] = []
    out: list[dict[str, str]] = []
    for idx, row in enumerate(rows, start=2):
        next_row = dict(row)
        last_resolved_owner = ""
        member = ""
        kind = ""
        for owner_col in ("owner_class", "owner"):
            owner = next_row.get(owner_col, "").strip()
            if not owner:
                continue
            if next_row.get("old_method", "").strip():
                kind = "method"
                member = next_row.get("old_method", "").strip()
            elif next_row.get("method", "").strip():
                kind = "method"
                member = next_row.get("method", "").strip()
            elif next_row.get("field_name", "").strip():
                kind = "field"
                member = next_row.get("field_name", "").strip()
            else:
                continue
            resolved_owner, status = _resolve_owner(owner=owner, kind=kind, member=member, aliases=aliases, move_targets=move_targets)
            if status.startswith("ambiguous") or status == "max-hops":
                warnings.append(f"row {idx} {owner_col}={owner} {kind}={member}: {status}")
            if resolved_owner and resolved_owner != owner:
                next_row[owner_col] = resolved_owner
                remapped += 1
            last_resolved_owner = resolved_owner or owner

        # Keep cleanup plan rows aligned with moved owners by remapping the file path
        # to the resolved owner when a unique destination exists.
        file_value = next_row.get("file", "").strip()
        if allow_file_remap and file_value:
            method_name = next_row.get("method", "").strip() or next_row.get("old_method", "").strip()
            if method_name:
                if (not java_index.has_file(file_value)) or (not java_index.file_has_method(file_value, method_name)):
                    owner_for_file = last_resolved_owner or next_row.get("owner", "").strip() or next_row.get("owner_class", "").strip()
                    if owner_for_file:
                        owner_candidates = java_index.owner_files(owner_for_file)
                        method_candidates = [p for p in owner_candidates if java_index.file_has_method(str(p), method_name)]
                        chosen: Path | None = None
                        if len(method_candidates) == 1:
                            chosen = method_candidates[0]
                        elif len(owner_candidates) == 1:
                            chosen = owner_candidates[0]
                        if chosen is not None:
                            next_file = str(chosen).replace("\\", "/")
                            if next_file != file_value:
                                next_row["file"] = next_file
                                file_remapped += 1
        out.append(next_row)
    return out, remapped, file_remapped, warnings


def main() -> int:
    logger = get_logger(__name__)
    ap = argparse.ArgumentParser(description="Generate cleanup plan with owner remaps after static extracts.")
    ap.add_argument("--python", default=sys.executable, help="Python interpreter for sub-tools.")
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_PLAN_DIR)
    ap.add_argument("--manifest-dir", type=Path, default=REFACTOR_EXTRACT_MANIFEST_DIR)
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--out-dir", type=Path, default=REFACTOR_CLEANUP_PLAN_DIR)
    ap.add_argument("--summary-json", type=Path, default=REFACTOR_CLEANUP_SUMMARY_JSON)
    ap.add_argument(
        "--stale-prune",
        dest="stale_prune",
        action="store_true",
        default=True,
        help="Prune no-op rows from generated cleanup plan before preflight/apply (default: on).",
    )
    ap.add_argument(
        "--no-stale-prune",
        dest="stale_prune",
        action="store_false",
        help="Disable stale pruning of generated cleanup plan.",
    )
    ap.add_argument(
        "--stale-dryrun-summary-json",
        type=Path,
        default=Path("build/refactor-state/cleanup-remap-dryrun-summary.json"),
        help="Dry-run cleanup summary used to compute stale rows.",
    )
    ap.add_argument(
        "--stale-prune-summary-json",
        type=Path,
        default=Path("build/refactor-state/cleanup-remap-prune-summary.json"),
        help="Summary output for stale prune pass.",
    )
    ap.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Fail when owner remap emits ambiguous/max-hop warnings.",
    )
    ap.add_argument(
        "--include-legacy-call-rewrites",
        action="store_true",
        help="Include legacy call_rewrites.csv and call_arg_rewrites.csv in generated cleanup plan.",
    )
    args = ap.parse_args()
    resolve_path_args(
        args,
        (
            "plan_dir",
            "manifest_dir",
            "src_dir",
            "out_dir",
            "summary_json",
            "stale_dryrun_summary_json",
            "stale_prune_summary_json",
        ),
    )

    aliases = _load_aliases(args.plan_dir)
    move_targets = _load_move_targets(args.manifest_dir)
    java_index = JavaIndex(args.src_dir)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for old in args.out_dir.glob("*.csv"):
        old.unlink()

    total_rows = 0
    total_remapped = 0
    total_file_remapped = 0
    total_promoted = 0
    total_deduped = 0
    stale_rows_removed = 0
    stale_rows_after = 0
    skipped_plan_files: list[str] = []
    warnings: list[str] = []
    promoted_rows = _load_guard_promotion_rows(args.plan_dir, java_index)

    for name in CLEANUP_PLAN_FILES:
        if (not args.include_legacy_call_rewrites) and name in {"call_rewrites.csv", "call_arg_rewrites.csv"}:
            skipped_plan_files.append(name)
            continue
        src = args.plan_dir / name
        header, rows = read_csv_dict_rows(src)
        if not header:
            continue
        if name == "signature_rewrites.csv" and promoted_rows:
            rows, added = _append_signature_promotions(rows, promoted_rows)
            total_promoted += added
        total_rows += len(rows)
        remapped_rows, remapped, file_remapped, file_warnings = _remap_owner_columns(
            rows=rows,
            aliases=aliases,
            move_targets=move_targets,
            java_index=java_index,
            allow_file_remap=name not in {"call_rewrites.csv", "call_arg_rewrites.csv"},
        )
        dedup_keys = DEDUP_KEYS_BY_FILE.get(name)
        if dedup_keys:
            remapped_rows, deduped = _dedupe_rows(remapped_rows, dedup_keys)
            total_deduped += deduped
        total_remapped += remapped
        total_file_remapped += file_remapped
        warnings.extend([f"{name}:{w}" for w in file_warnings])
        write_csv_dict_rows(args.out_dir / name, header, remapped_rows)

    if args.stale_prune:
        try:
            stale_rows_removed, stale_rows_after = _prune_stale_rows(
                python=args.python,
                src_dir=args.src_dir,
                plan_dir=args.out_dir,
                dryrun_summary_json=args.stale_dryrun_summary_json,
                prune_summary_json=args.stale_prune_summary_json,
            )
        except RuntimeError as exc:
            warnings.append(str(exc))

    summary = {
        "rows": total_rows,
        "owner_remaps": total_remapped,
        "file_remaps": total_file_remapped,
        "promoted_signature_rows": total_promoted,
        "deduped_rows_removed": total_deduped,
        "stale_rows_removed": stale_rows_removed,
        "stale_rows_after": stale_rows_after,
        "skipped_plan_files": skipped_plan_files,
        "warnings": warnings,
        "out_dir": str(args.out_dir),
    }
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    log_event(
        logger,
        20,
        "cleanup_remap.done",
        rows=total_rows,
        owner_remaps=total_remapped,
        file_remaps=total_file_remapped,
        promoted_signature_rows=total_promoted,
        deduped_rows_removed=total_deduped,
        stale_rows_removed=stale_rows_removed,
        stale_rows_after=stale_rows_after,
        skipped_plan_files=len(skipped_plan_files),
        out=args.out_dir,
    )
    if warnings:
        for warning in warnings:
            log_event(logger, 30, "cleanup_remap.warning", warning=warning)
        if args.fail_on_warnings:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
