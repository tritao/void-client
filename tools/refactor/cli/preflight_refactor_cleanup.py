#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import REFACTOR_CLEANUP_PLAN_DIR, REFACTOR_SRC_DIR
from tools.refactor.common.io import read_csv_dict_rows
from tools.refactor.common.java_index import JavaIndex
from tools.refactor.common.logging import get_logger, log_event


def _parse_signature_types(sig_text: str) -> tuple[str, ...] | None:
    s = (sig_text or "").strip()
    if not s:
        return None
    if s == "()":
        return ()
    parts = [p.strip() for p in s.split(",")]
    cleaned = tuple(p for p in parts if p)
    return cleaned


def main() -> int:
    logger = get_logger(__name__)
    ap = argparse.ArgumentParser(description="Fail-fast preflight for generated cleanup rules.")
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_CLEANUP_PLAN_DIR)
    args = ap.parse_args()
    resolve_path_args(args, ("src_dir", "plan_dir"))

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

    # method_identifier_rewrites.csv -> file + method must exist
    _, mid_rows = read_csv_dict_rows(args.plan_dir / "method_identifier_rewrites.csv")
    for i, row in enumerate(mid_rows, start=2):
        where = f"method_identifier_rewrites.csv:{i}"
        file = row.get("file", "")
        method = row.get("method", "")
        check_file(file, where)
        if file and method and index.has_file(file) and not index.file_has_method(file, method):
            owner = index.unique_owner_for_method(method)
            hint = f" (exists on owner {owner})" if owner else ""
            warnings.append(f"{where}: method not found in file: {file}::{method}{hint}")

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

    # qualified_call_rewrites.csv -> owner_class.old_method must exist
    _, q_rows = read_csv_dict_rows(args.plan_dir / "qualified_call_rewrites.csv")
    for i, row in enumerate(q_rows, start=2):
        where = f"qualified_call_rewrites.csv:{i}"
        file = row.get("file", "")
        owner = row.get("owner_class", "")
        old_method = row.get("old_method", "")
        check_file(file, where)
        if owner and old_method and not index.owner_has_method(owner, old_method):
            hint = index.unique_owner_for_method(old_method)
            suffix = f" (candidate owner: {hint})" if hint else ""
            warnings.append(f"{where}: owner/method not found: {owner}.{old_method}{suffix}")

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

    if errors:
        for err in errors:
            log_event(logger, 40, "cleanup_preflight.error", error=err)
        return 1

    if warnings:
        for warning in warnings:
            log_event(logger, 30, "cleanup_preflight.warning", warning=warning)

    log_event(logger, 20, "cleanup_preflight.ok", warnings=len(warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
