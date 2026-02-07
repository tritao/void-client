#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import re
from collections import defaultdict
from pathlib import Path

from static_extract_common import build_java_parser, iter_nodes


SCHEMA = [
    "id",
    "module",
    "action",
    "kind",
    "file",
    "owner",
    "member",
    "signature",
    "param_index",
    "old",
    "new",
    "target_class",
    "target_file",
    "scope",
    "phase",
    "confidence",
    "status",
    "notes",
]

VALID_ACTIONS = {"rename", "extract", "class_rename", "defer"}
VALID_KINDS = {"type", "method", "field", "param", "local", "class"}
VALID_PHASES = {"core", "module", "cleanup", "split"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_STATUS = {"proposed", "approved", "applied", "blocked"}

SYMBOL_VIEW_HEADER = [
    "file",
    "kind",
    "owner",
    "member",
    "signature",
    "param_index",
    "old",
    "new",
    "detail_regex",
    "line",
    "col",
    "notes",
    # Plan metadata for schedulers/filters (ignored by renamer).
    "module",
    "phase",
    "confidence",
    "status",
    "source",
]
CLASS_VIEW_HEADER = ["src", "dst", "score", "anchors"]


@dataclasses.dataclass(frozen=True)
class PlanRow:
    source_file: Path
    source_line: int
    data: dict[str, str]

    @property
    def module(self) -> str:
        return self.data["module"]

    @property
    def action(self) -> str:
        return self.data["action"]

    @property
    def kind(self) -> str:
        return self.data["kind"]


def _read_plan_rows(plan_dir: Path) -> list[PlanRow]:
    rows: list[PlanRow] = []
    for csv_path in sorted(plan_dir.glob("*.csv")):
        if csv_path.name.startswith("_"):
            continue
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader((line for line in handle if line.strip() and not line.lstrip().startswith("#")))
            if not reader.fieldnames:
                continue
            stem_parts = csv_path.stem.split(".")
            inferred_module = stem_parts[0]
            inferred_action = stem_parts[1] if len(stem_parts) > 1 and stem_parts[1] in VALID_ACTIONS else ""
            if "action" not in reader.fieldnames and not inferred_action:
                # Non-plan CSVs (e.g. layout_rules.csv) can live beside plan files.
                continue
            fieldnames = set(reader.fieldnames)
            for idx, raw in enumerate(reader, start=2):
                data = {k: (raw.get(k, "") or "").strip() if k in fieldnames else "" for k in SCHEMA}
                if not data["action"]:
                    data["action"] = inferred_action
                if not any(data.values()):
                    continue
                if not data["module"]:
                    data["module"] = inferred_module
                rows.append(PlanRow(source_file=csv_path, source_line=idx, data=data))
    return rows


def _validate(rows: list[PlanRow], refactor_src: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rename_conflicts: dict[tuple[str, str], set[str]] = defaultdict(set)
    rename_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    seen_ids: dict[str, PlanRow] = {}
    extract_target_files = {
        row.data["target_file"]
        for row in rows
        if row.data.get("action") == "extract" and row.data.get("target_file")
    }
    java_by_stem: dict[str, list[Path]] = defaultdict(list)
    if refactor_src.exists():
        for p in refactor_src.rglob("*.java"):
            try:
                java_by_stem[p.stem].append(p.relative_to(refactor_src))
            except ValueError:
                continue

    # Best-effort overload detection: if a method rename row omits signature and the
    # source class has multiple overloads for that obfuscated name, force authors to
    # specify a signature to disambiguate.
    parser = None
    method_decl_cache: dict[tuple[str, str], dict[str, int]] = {}

    def _node_text(data: bytes, start: int, end: int) -> str:
        return data[start:end].decode("utf-8", errors="replace")

    def _class_method_counts(rel_path: Path, class_name: str) -> dict[str, int]:
        key = (str(rel_path), class_name)
        cached = method_decl_cache.get(key)
        if cached is not None:
            return cached
        abs_path = refactor_src / rel_path
        try:
            data = abs_path.read_bytes()
        except OSError:
            method_decl_cache[key] = {}
            return {}
        nonlocal parser
        if parser is None:
            parser = build_java_parser(out_so=Path("build/ts-languages-java.so"))
        tree = parser.parse(data)
        class_node = None
        for node in iter_nodes(tree.root_node):
            if node.type != "class_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            if _node_text(data, name_node.start_byte, name_node.end_byte) == class_name:
                class_node = node
                break
        root = class_node or tree.root_node
        counts: dict[str, int] = defaultdict(int)
        for node in iter_nodes(root):
            if node.type != "method_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _node_text(data, name_node.start_byte, name_node.end_byte).strip()
            if name:
                counts[name] += 1
        method_decl_cache[key] = dict(counts)
        return method_decl_cache[key]

    def _resolve_java_file(d: dict[str, str]) -> list[Path]:
        file_value = (d.get("file") or "").strip()
        owner_value = (d.get("owner") or "").strip() or _normalize_owner_from_file(file_value)
        if file_value and ("/" in file_value or file_value.endswith(".java")):
            rel = Path(file_value)
            if rel.suffix != ".java":
                rel = rel.with_suffix(".java")
            if (refactor_src / rel).exists():
                return [rel]
            return []
        if not owner_value:
            return []
        return list(java_by_stem.get(owner_value, []))

    for row in rows:
        d = row.data
        prefix = f"{row.source_file}:{row.source_line}"
        if d["id"]:
            if d["id"] in seen_ids:
                prev = seen_ids[d["id"]]
                errors.append(f"{prefix}: duplicate id '{d['id']}' (also {prev.source_file}:{prev.source_line})")
            else:
                seen_ids[d["id"]] = row
        if d["action"] not in VALID_ACTIONS:
            errors.append(f"{prefix}: invalid action '{d['action']}'")
        if d["kind"] and d["kind"] not in VALID_KINDS:
            errors.append(f"{prefix}: invalid kind '{d['kind']}'")
        if d["phase"] and d["phase"] not in VALID_PHASES:
            errors.append(f"{prefix}: invalid phase '{d['phase']}'")
        if d["confidence"] and d["confidence"] not in VALID_CONFIDENCE:
            errors.append(f"{prefix}: invalid confidence '{d['confidence']}'")
        if d["status"] and d["status"] not in VALID_STATUS:
            errors.append(f"{prefix}: invalid status '{d['status']}'")

        if d["file"] and "/" in d["file"]:
            rel = Path(d["file"])
            if rel.suffix and rel.suffix != ".java":
                errors.append(f"{prefix}: file must be Java path or type stem: '{d['file']}'")
            if rel.suffix == ".java" and not (refactor_src / rel).exists():
                if not (d["action"] == "extract" and d["file"] in extract_target_files):
                    errors.append(f"{prefix}: file path does not exist under refactor: '{d['file']}'")

        if d["action"] == "rename":
            if not d["old"] or not d["new"]:
                errors.append(f"{prefix}: rename requires old,new")
            scope = d["scope"] or d["file"] or "<global>"
            rename_conflicts[(scope, d["old"])].add(d["new"])
            rename_targets[(scope, d["new"])].add(d["old"])

            if d["kind"] == "method" and not d["signature"] and d["old"] and refactor_src.exists():
                candidates = _resolve_java_file(d)
                # Only enforce when we can resolve to a single file; otherwise we'd
                # risk false positives due to multiple classes sharing a stem.
                if len(candidates) == 1:
                    owner = (d.get("owner") or "").strip() or _normalize_owner_from_file(d.get("file") or "")
                    if not owner:
                        continue
                    counts = _class_method_counts(candidates[0], owner)
                    if counts.get(d["old"], 0) > 1:
                        warnings.append(
                            f"overload-signature-required: {owner}.{d['old']} has {counts[d['old']]} overloads "
                            f"but rename row has empty signature ({prefix})"
                        )
        elif d["action"] == "extract":
            if d["kind"] not in ("method", "field"):
                errors.append(f"{prefix}: extract requires kind=method|field")
            if not d["old"] or not d["target_class"] or not d["target_file"]:
                errors.append(f"{prefix}: extract requires old,target_class,target_file")
        elif d["action"] == "class_rename":
            if not d["old"] or not d["new"]:
                errors.append(f"{prefix}: class_rename requires old,new")

        if re.search(r"\bLru\b", d["new"]) and "LRU" not in d["new"]:
            errors.append(f"{prefix}: naming style violation '{d['new']}' (use LRU*)")

    for (scope, old), news in rename_conflicts.items():
        if old and len(news) > 1:
            warnings.append(f"conflict: rename old='{old}' in scope='{scope}' has multiple targets: {sorted(news)} (latest wins)")
    for (scope, new), olds in rename_targets.items():
        if new and len(olds) > 1:
            warnings.append(f"collision: rename new='{new}' in scope='{scope}' comes from multiple olds: {sorted(olds)}")
    return errors, warnings


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _normalize_owner_from_file(file_value: str) -> str:
    if not file_value:
        return ""
    if "/" in file_value or file_value.endswith(".java"):
        return Path(file_value).stem
    return file_value


def _emit_symbol_views(rows: list[PlanRow], out_root_csv: Path, out_dir: Path, warnings: list[str]) -> tuple[dict[str, int], int]:
    move_targets: dict[tuple[str, str, str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        d = row.data
        if d["action"] != "extract":
            continue
        if d["status"] == "blocked":
            continue
        source_owner = d["owner"] or _normalize_owner_from_file(d["file"])
        target_owner = d["target_class"]
        target_file = d["target_file"]
        if not source_owner or not target_owner or not target_file or not d["old"]:
            continue
        key_exact = (d["kind"], source_owner, d["old"], d["signature"])
        key_fallback = (d["kind"], source_owner, d["old"], "")
        move_targets[key_exact].add((target_owner, target_file))
        move_targets[key_fallback].add((target_owner, target_file))

    by_module: dict[str, list[list[str]]] = defaultdict(list)
    root_rows: list[list[str]] = []
    latest_by_scope_old: dict[tuple[str, str], PlanRow] = {}
    for row in rows:
        d = row.data
        if d["action"] != "rename":
            continue
        if d["kind"] not in ("type", "method", "field", "param", "local"):
            continue
        scope = d["scope"] or d["file"] or "<global>"
        latest_by_scope_old[(scope, d["old"])] = row
    rebinding_count = 0
    for row in latest_by_scope_old.values():
        d = row.data
        resolved_owner = d["owner"] or _normalize_owner_from_file(d["file"])
        resolved_file = d["file"]
        if d["kind"] in ("method", "field") and resolved_owner and d["old"]:
            seen_keys: set[tuple[str, str, str, str]] = set()
            while True:
                key = (d["kind"], resolved_owner, d["old"], d["signature"])
                if key in seen_keys:
                    warnings.append(
                        f"rebind-cycle: {d['kind']} {resolved_owner}.{d['old']} in {row.source_file}:{row.source_line}"
                    )
                    break
                seen_keys.add(key)
                targets = move_targets.get(key) or move_targets.get((d["kind"], resolved_owner, d["old"], ""))
                if not targets:
                    break
                if len(targets) > 1:
                    warnings.append(
                        f"rebind-ambiguous: {d['kind']} {resolved_owner}.{d['old']} has multiple extract targets {sorted(targets)} "
                        f"(source {row.source_file}:{row.source_line})"
                    )
                    break
                target_owner, target_file = next(iter(targets))
                if target_owner == resolved_owner and target_file == resolved_file:
                    break
                resolved_owner = target_owner
                resolved_file = target_file
                rebinding_count += 1
        emitted = [
            resolved_file,
            d["kind"],
            resolved_owner,
            d["member"],
            d["signature"],
            d["param_index"],
            d["old"],
            d["new"],
            "",
            "",
            "",
            d["notes"],
            d["module"],
            d["phase"],
            d["confidence"],
            d["status"],
            f"{row.source_file}:{row.source_line}",
        ]
        root_rows.append(emitted)
        by_module[d["module"]].append(emitted)
    _write_csv(out_root_csv, SYMBOL_VIEW_HEADER, root_rows)
    counts: dict[str, int] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.csv"):
        old.unlink()
    for module, module_rows in sorted(by_module.items()):
        path = out_dir / f"000-generated-{module}.csv"
        _write_csv(path, SYMBOL_VIEW_HEADER, module_rows)
        counts[module] = len(module_rows)
    return counts, rebinding_count


def _emit_class_view(rows: list[PlanRow], out_csv: Path) -> int:
    out_rows: list[list[str]] = []
    for row in rows:
        d = row.data
        if d["action"] != "class_rename":
            continue
        out_rows.append([d["old"], d["new"], "1.0", d["notes"]])
    _write_csv(out_csv, CLASS_VIEW_HEADER, out_rows)
    return len(out_rows)


def _emit_extract_views(rows: list[PlanRow], out_dir: Path) -> int:
    grouped: dict[tuple[str, str, str], list[PlanRow]] = defaultdict(list)
    for row in rows:
        d = row.data
        if d["action"] != "extract":
            continue
        grouped[(d["file"], d["target_file"], d["target_class"])].append(row)

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.yaml"):
        old.unlink()

    count = 0
    for (source_file, target_file, target_class), items in sorted(grouped.items()):
        manifest_name = f"{count+1:03d}-{Path(source_file).stem}-to-{Path(target_file).stem}.yaml"
        path = out_dir / manifest_name
        lines = [
            f"source: {source_file}",
            f"target_file: {target_file}",
            f"target_class: {target_class}",
            "moves:",
        ]
        for item in sorted(items, key=lambda r: (r.data["kind"], r.data["old"])):
            lines.append(f"  - kind: {item.data['kind']}")
            lines.append(f"    name: {item.data['old']}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        count += 1
    return count


def _write_report(path: Path, *, plan_count: int, symbol_count: int, class_count: int, extract_count: int, rebind_count: int, module_counts: dict[str, int], warnings: list[str]) -> None:
    lines: list[str] = []
    lines.append("# Refactor view build report")
    lines.append("")
    lines.append(f"- Plan rows: `{plan_count}`")
    lines.append(f"- Symbol rename rows emitted: `{symbol_count}`")
    lines.append(f"- Symbol owner/file rebinds: `{rebind_count}`")
    lines.append(f"- Class rename rows emitted: `{class_count}`")
    lines.append(f"- Extract manifests emitted: `{extract_count}`")
    lines.append("")
    lines.append("## Symbol rows by module")
    lines.append("")
    lines.append("| module | rows |")
    lines.append("|---|---:|")
    for module, count in sorted(module_counts.items(), key=lambda x: (x[0])):
        lines.append(f"| `{module}` | {count} |")
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build generated rename/extract views from canonical .refactor-plan CSVs.")
    ap.add_argument("--plan-dir", type=Path, default=Path("client/refactor/.refactor-plan"))
    ap.add_argument("--refactor-src", type=Path, default=Path("client/refactor"))
    ap.add_argument("--out-symbol-root", type=Path, default=Path("client/refactor/.refactor-plan/generated/symbol_renames.csv"))
    ap.add_argument("--out-symbol-dir", type=Path, default=Path("client/refactor/.refactor-plan/symbol-renames/generated"))
    ap.add_argument("--out-class-csv", type=Path, default=Path("client/refactor/.refactor-plan/generated/classes.csv"))
    ap.add_argument("--out-extract-dir", type=Path, default=Path("client/refactor/.refactor-plan/extract-statics/generated"))
    ap.add_argument("--report", type=Path, default=Path("docs/refactor-views-report.md"))
    ap.add_argument("--write-json-summary", type=Path, default=Path("build/refactor-state/views-summary.json"))
    ap.add_argument(
        "--allow-conflicts",
        action="store_true",
        help="Allow warning-level conflicts/collisions (default: fail when warnings exist).",
    )
    args = ap.parse_args()

    rows = _read_plan_rows(args.plan_dir)
    errors, warnings = _validate(rows, args.refactor_src)
    if errors:
        print("Refactor plan validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1
    if warnings and not args.allow_conflicts:
        print("Refactor plan validation warnings (treated as errors):")
        for warning in warnings:
            print(f"- {warning}")
        print("Use --allow-conflicts to bypass temporarily.")
        return 1

    symbol_module_counts, symbol_rebind_count = _emit_symbol_views(rows, args.out_symbol_root, args.out_symbol_dir, warnings)
    symbol_count = sum(symbol_module_counts.values())
    class_count = _emit_class_view(rows, args.out_class_csv)
    extract_count = _emit_extract_views(rows, args.out_extract_dir)
    _write_report(
        args.report,
        plan_count=len(rows),
        symbol_count=symbol_count,
        rebind_count=symbol_rebind_count,
        class_count=class_count,
        extract_count=extract_count,
        module_counts=symbol_module_counts,
        warnings=warnings,
    )
    summary = {
        "plan_rows": len(rows),
        "symbol_rows": symbol_count,
        "symbol_rebinds": symbol_rebind_count,
        "class_rows": class_count,
        "extract_manifests": extract_count,
        "modules": symbol_module_counts,
    }
    args.write_json_summary.parent.mkdir(parents=True, exist_ok=True)
    args.write_json_summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Built views: symbols={symbol_count}, classes={class_count}, extract_manifests={extract_count}. "
        f"Report: {args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
