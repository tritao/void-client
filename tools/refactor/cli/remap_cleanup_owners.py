#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def _load_guard_promotion_rows(plan_dir: Path) -> list[dict[str, str]]:
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
        gate_status = (row.get("gate_status") or "").strip().lower()
        if gate_status and gate_status != "auto":
            continue
        dep = (row.get("dependency") or "").strip()
        root = by_id.get(dep, {})
        match_text = (root.get("match") or "").strip()
        if not match_text:
            continue
        file = (row.get("file") or "").strip()
        owner = (row.get("owner") or "").strip()
        method = (row.get("member") or "").strip()
        if not (file and owner and method):
            continue
        note_reason = (row.get("gate_reason") or "").strip() or "auto"
        out.append(
            {
                "file": file,
                "owner": owner,
                "method": method,
                "signature_before": "()",
                "signature_after": "()",
                "op": "drop_statement_contains",
                "param_index": "",
                "new_name": "",
                "match_text": match_text,
                "phase": "cleanup",
                "status": "approved",
                "notes": f"Auto-promoted from unified cleanup ({note_reason})",
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
            (row.get("match_text") or "").strip(),
        )
        if not all(key):
            continue
        if key in keys:
            continue
        keys.add(key)
        out.append(row)
        added += 1
    return out, added


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
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_PLAN_DIR)
    ap.add_argument("--manifest-dir", type=Path, default=REFACTOR_EXTRACT_MANIFEST_DIR)
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--out-dir", type=Path, default=REFACTOR_CLEANUP_PLAN_DIR)
    ap.add_argument("--summary-json", type=Path, default=REFACTOR_CLEANUP_SUMMARY_JSON)
    ap.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Fail when owner remap emits ambiguous/max-hop warnings.",
    )
    args = ap.parse_args()
    resolve_path_args(args, ("plan_dir", "manifest_dir", "src_dir", "out_dir", "summary_json"))

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
    warnings: list[str] = []
    promoted_rows = _load_guard_promotion_rows(args.plan_dir)

    for name in CLEANUP_PLAN_FILES:
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
        total_remapped += remapped
        total_file_remapped += file_remapped
        warnings.extend([f"{name}:{w}" for w in file_warnings])
        write_csv_dict_rows(args.out_dir / name, header, remapped_rows)

    summary = {
        "rows": total_rows,
        "owner_remaps": total_remapped,
        "file_remaps": total_file_remapped,
        "promoted_signature_rows": total_promoted,
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
