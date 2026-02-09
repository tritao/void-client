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
from tools.refactor.common.constants import REFACTOR_CLEANUP_PLAN_DIR, REFACTOR_SRC_DIR
from tools.refactor.common.io import read_csv_dict_rows
from tools.refactor.common.java_index import JavaIndex
from tools.refactor.common.logging import get_logger, log_event
from tools.refactor.cli.apply_refactor_cleanup import (
    _find_method_spans,
    _load_signature_rules,
    _param_name_from_decl,
    _rewrite_signature_semantic,
)


def _parse_signature_types(sig_text: str) -> tuple[str, ...] | None:
    s = (sig_text or "").strip()
    if not s:
        return None
    if s == "()":
        return ()
    parts = [p.strip() for p in s.split(",")]
    cleaned = tuple(p for p in parts if p)
    return cleaned


def _check_ambiguous_signature_callsite_drops(sig_rules: list) -> list[str]:
    by_key: dict[tuple[str, str, int], set[int]] = defaultdict(set)
    by_method_arity: dict[tuple[str, int], set[int]] = defaultdict(set)
    for rule in sig_rules:
        if rule.op != "drop_param":
            continue
        sig_before = _parse_signature_types(rule.signature_before)
        if sig_before is None:
            continue
        arity = len(sig_before)
        if rule.param_index < 0 or rule.param_index >= arity:
            continue
        owner = (rule.owner or "").strip()
        by_key[(owner, rule.method, arity)].add(rule.param_index)
        by_method_arity[(rule.method, arity)].add(rule.param_index)

    errors: list[str] = []
    for (owner, method, arity), indexes in sorted(by_key.items()):
        if len(indexes) <= 1:
            continue
        owner_display = owner or "<unscoped>"
        idx_csv = ",".join(str(v) for v in sorted(indexes))
        errors.append(
            "signature_rewrites ambiguous drop_param callsite rule set: "
            f"owner={owner_display} method={method} arity={arity} indexes={idx_csv}"
        )
    for (method, arity), indexes in sorted(by_method_arity.items()):
        if len(indexes) <= 1:
            continue
        idx_csv = ",".join(str(v) for v in sorted(indexes))
        errors.append(
            "signature_rewrites ambiguous drop_param fallback set: "
            f"method={method} arity={arity} indexes={idx_csv}"
        )
    return errors


def main() -> int:
    logger = get_logger(__name__)
    ap = argparse.ArgumentParser(description="Fail-fast preflight for generated cleanup rules.")
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_CLEANUP_PLAN_DIR)
    ap.add_argument("--drift-warn-ratio", type=float, default=0.90)
    ap.add_argument("--drift-fail-ratio", type=float, default=-1.0)
    ap.add_argument(
        "--drift-summary-json",
        type=Path,
        default=Path("build/refactor-state/cleanup-preflight-summary.json"),
    )
    args = ap.parse_args()
    resolve_path_args(args, ("src_dir", "plan_dir", "drift_summary_json"))

    index = JavaIndex(args.src_dir)
    errors: list[str] = []
    warnings: list[str] = []

    def check_file(path: str, where: str) -> None:
        if path and not index.has_file(path):
            errors.append(f"{where}: file not found: {path}")

    # signature_rewrites.csv -> file + method must exist, op must be valid
    _, sig_rows = read_csv_dict_rows(args.plan_dir / "signature_rewrites.csv")
    valid_signature_ops = {"drop_param", "rename_param", "drop_statement_contains"}
    for i, row in enumerate(sig_rows, start=2):
        where = f"signature_rewrites.csv:{i}"
        file = row.get("file", "")
        method = row.get("method", "")
        op = row.get("op", "")
        param_index = row.get("param_index", "")
        new_name = row.get("new_name", "")
        match_text = row.get("match_text", "")
        signature_before = row.get("signature_before", "")
        signature_after = row.get("signature_after", "")
        check_file(file, where)
        if op not in valid_signature_ops:
            errors.append(f"{where}: invalid op '{op}'")
            continue
        if op in {"drop_param", "rename_param"}:
            if not param_index:
                errors.append(f"{where}: param_index required for op={op}")
            else:
                try:
                    if int(param_index) < 0:
                        errors.append(f"{where}: param_index must be >= 0")
                except ValueError:
                    errors.append(f"{where}: param_index must be integer")
        if op == "rename_param" and not new_name:
            errors.append(f"{where}: new_name required for op=rename_param")
        if op == "drop_statement_contains" and not match_text:
            errors.append(f"{where}: match_text required for op=drop_statement_contains")
        # Syntax-level validation only; semantic matching is done by apply stage.
        _ = _parse_signature_types(signature_before)
        _ = _parse_signature_types(signature_after)
        if file and method and index.has_file(file) and not index.file_has_method(file, method):
            owner = index.unique_owner_for_method(method)
            hint = f" (exists on owner {owner})" if owner else ""
            warnings.append(f"{where}: method not found in file: {file}::{method}{hint}")

    # Simulate signature transforms to catch post-transform unresolved param refs.
    sig_rules = _load_signature_rules(args.plan_dir / "signature_rewrites.csv")
    errors.extend(_check_ambiguous_signature_callsite_drops(sig_rules))
    rules_by_file: dict[str, list] = {}
    for rule in sig_rules:
        rules_by_file.setdefault(rule.file, []).append(rule)

    def _pick_span(spans: list[dict[str, object]], sig_target: tuple[str, ...] | None) -> dict[str, object] | None:
        if not spans:
            return None
        if sig_target is None:
            return spans[0] if len(spans) == 1 else None
        for span in spans:
            if tuple(span.get("sig", ())) == sig_target:
                return span
        return None

    for rel_file, file_rules in sorted(rules_by_file.items()):
        if not index.has_file(rel_file):
            continue
        abs_path = (args.src_dir / rel_file).resolve()
        try:
            current_text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rule in file_rules:
            old_param_name = ""
            if rule.op == "rename_param":
                sig_before = _parse_signature_types(rule.signature_before)
                before_spans = _find_method_spans(current_text, rule.method)
                before_target = _pick_span(before_spans, sig_before)
                if before_target is not None:
                    params = list(before_target.get("params", []))
                    if 0 <= rule.param_index < len(params):
                        old_param_name = _param_name_from_decl(str(params[rule.param_index]))
            next_text, changed = _rewrite_signature_semantic(current_text, rule)
            if changed:
                current_text = next_text
            if rule.op != "rename_param" or not old_param_name:
                continue
            sig_after = _parse_signature_types(rule.signature_after)
            after_spans = _find_method_spans(current_text, rule.method)
            after_target = _pick_span(after_spans, sig_after)
            if after_target is None:
                continue
            body_start = after_target.get("body_start")
            body_end = after_target.get("body_end")
            if body_start is None or body_end is None:
                continue
            body = current_text[int(body_start) : int(body_end)]
            if re.search(rf"\b{re.escape(old_param_name)}\b", body):
                errors.append(
                    "signature_rewrites simulation: "
                    f"{rel_file}::{rule.method} leaves old param token '{old_param_name}' in method body after rename_param"
                )

    # call_arg_rewrites.csv -> owner_class.method must exist when owner_class provided
    _, carg_rows = read_csv_dict_rows(args.plan_dir / "call_arg_rewrites.csv")
    for i, row in enumerate(carg_rows, start=2):
        where = f"call_arg_rewrites.csv:{i}"
        file = row.get("file", "")
        owner = row.get("owner_class", "")
        method = row.get("method", "")
        check_file(file, where)
        if owner and method and not index.owner_has_method(owner, method):
            hint = index.unique_owner_for_method(method)
            suffix = f" (candidate owner: {hint})" if hint else ""
            warnings.append(f"{where}: owner/method not found: {owner}.{method}{suffix}")

    # delegation_rewrites.csv -> file/method should exist; owner-class token may be legacy/no-op.
    _, d_rows = read_csv_dict_rows(args.plan_dir / "delegation_rewrites.csv")
    for i, row in enumerate(d_rows, start=2):
        where = f"delegation_rewrites.csv:{i}"
        file = row.get("file", "")
        method = row.get("method", "")
        check_file(file, where)
        if file and method and index.has_file(file) and not index.file_has_method(file, method):
            warnings.append(f"{where}: method not found in file: {file}::{method}")

    # reset_methods.csv -> target_file + owner_class should exist
    _, r_rows = read_csv_dict_rows(args.plan_dir / "reset_methods.csv")
    for i, row in enumerate(r_rows, start=2):
        where = f"reset_methods.csv:{i}"
        owner = row.get("owner_class", "")
        target_file = row.get("target_file", "")
        check_file(target_file, where)
        if owner and not index.owner_files(owner):
            errors.append(f"{where}: owner class not found: {owner}")

    # Drift gate: compute unmatched ratio from a dry-run cleanup apply pass.
    drift_ratio: float | None = None
    drift_totals: dict[str, int] | None = None
    try:
        cmd = [
            sys.executable,
            "-m",
            "tools.refactor.cli.apply_refactor_cleanup",
            "--src-dir",
            str(args.src_dir),
            "--plan-dir",
            str(args.plan_dir),
            "--summary-json",
            str(args.drift_summary_json),
            "--dry-run",
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append(
                "cleanup drift check failed: "
                f"apply_refactor_cleanup --dry-run exit={proc.returncode}"
            )
        elif not args.drift_summary_json.exists():
            warnings.append(
                "cleanup drift check skipped: dry-run summary missing at "
                f"{args.drift_summary_json}"
            )
        else:
            payload = json.loads(args.drift_summary_json.read_text(encoding="utf-8"))
            totals = payload.get("totals") or {}
            total = int(totals.get("total", 0) or 0)
            unmatched = int(totals.get("unmatched", 0) or 0)
            drift_totals = {"total": total, "unmatched": unmatched}
            drift_ratio = (float(unmatched) / float(total)) if total > 0 else 0.0
            if args.drift_warn_ratio >= 0 and drift_ratio > args.drift_warn_ratio:
                warnings.append(
                    "cleanup drift high: "
                    f"unmatched_ratio={drift_ratio:.4f} exceeds warn threshold={args.drift_warn_ratio:.4f}"
                )
            if args.drift_fail_ratio >= 0 and drift_ratio > args.drift_fail_ratio:
                errors.append(
                    "cleanup drift fail gate: "
                    f"unmatched_ratio={drift_ratio:.4f} exceeds fail threshold={args.drift_fail_ratio:.4f}"
                )
    except Exception as exc:
        errors.append(f"cleanup drift check raised: {exc}")

    if errors:
        for err in errors:
            log_event(logger, 40, "cleanup_preflight.error", error=err)
        return 1

    if warnings:
        for warning in warnings:
            log_event(logger, 30, "cleanup_preflight.warning", warning=warning)

    if drift_ratio is not None and drift_totals is not None:
        log_event(
            logger,
            20,
            "cleanup_preflight.drift",
            unmatched_ratio=f"{drift_ratio:.4f}",
            total=drift_totals["total"],
            unmatched=drift_totals["unmatched"],
            warn_threshold=args.drift_warn_ratio,
            fail_threshold=args.drift_fail_ratio,
            summary=args.drift_summary_json,
        )
    log_event(logger, 20, "cleanup_preflight.ok", warnings=len(warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
