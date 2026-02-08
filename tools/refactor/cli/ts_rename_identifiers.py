#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable

from tools.refactor.common.errors import ConflictError, ValidationError
from tools.refactor.common.logging import get_logger, log_event
from tools.refactor.common.ts_java import build_java_language
from tools.refactor.rename.io import load_mappings, load_static_extract_moves
from tools.refactor.rename.cache import load_cached_indexes as _load_cached_indexes
from tools.refactor.rename.cache import java_tree_fingerprint as _java_tree_fingerprint
from tools.refactor.rename.cache import write_cached_indexes as _write_cached_indexes
from tools.refactor.rename.index import build_indexes as _build_indexes
from tools.refactor.rename.index import iter_nodes as _iter_nodes
from tools.refactor.rename.index import iter_owner_closure as _iter_owner_closure
from tools.refactor.rename.index import node_text as _node_text
from tools.refactor.rename.index import normalize_type_name as _normalize_type_name
from tools.refactor.rename.edits import apply_edits_bytes, dedupe_and_prune_overlaps
from tools.refactor.rename.preflight import build_prepared_mappings, validate_prepared_conflicts
from tools.refactor.rename.semantic import (
    collect_method_env,
    enclosing_member_name,
    is_field_declarator_name_node,
    parse_signature_types,
    pick_method_rename,
    rename_member_accesses,
)
from tools.refactor.rename.models import ClassIndex, MethodRename, PreparedMapping, Report


try:
    from tree_sitter import Node, Parser  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "\n".join(
            [
                f"Missing tree-sitter dependency: {e}",
                "",
                "Run:",
                "  make bootstrap-refactor-tools",
                "",
                "Then rerun this command using:",
                "  ./.venv/bin/python -m tools.refactor.cli.ts_rename_identifiers ...",
            ]
        )
    )


# Conservative default: only rename classic JODE-style numbered identifiers.
#
# Notes:
# - JODE commonly emits object-typed fields like `aClass45_4286`, `aClass348Array4374`,
#   `aClass318_Sub1Array4293`, `aClass190ArrayArray3335`, etc.
# - It also emits primitive multi-dimensional arrays like `anIntArrayArray1234`.
OBF_NAME_RX = re.compile(
    r"^(?:"
    r"anInt(?:Array)*|"
    r"aSoftReference(?:Array)*|"
    r"aByte(?:Array)*|aShort(?:Array)*|aLong(?:Array)*|aChar(?:Array)*|"
    r"aBoolean(?:Array)*|aFloat(?:Array)*|aDouble(?:Array)*|aString(?:Array)*|"
    r"anObject(?:Array)*|"
    r"aD_?|"
    r"aPlayer(?:Array)*_?|"
    r"aClass\d+(?:_Sub\d+)*(?:Array)*_?|"
    r"aBigInteger"
    r")\d+$"
)
OBF_METHOD_RX = re.compile(r"^method\d+$")
OBF_PARAM_LOCAL_RX = re.compile(
    r"^(?:"
    r"[ijl]|[ijl]_\d+_|"
    r"is(?:_\d+_)?|"
    r"interface\d+s?(?:_\d+_)?|"
    r"bool(?:_\d+_)?|"
    r"byte(?:_\d+_)?|short(?:_\d+_)?|char(?:_\d+_)?|float(?:_\d+_)?|double(?:_\d+_)?|"
    r"long(?:_\d+_)?|int(?:_\d+_)?|"
    r"string(?:_\d+_)?|text(?:\d+)?|object(?:_\d+_)?|"
    r"flag\d*|"
    r"class\d+(?:_sub\d+)*(?:s)?(?:_\d+_)?"
    r")$",
    re.IGNORECASE,
)
_RUST_TOKEN_PREFILTER_MANIFEST = Path("tools/refactor/rust/rename_token_prefilter/Cargo.toml")
_RUST_TOKEN_PREFILTER_TARGET_DIR = Path("build/refactor-cache/rust-target")
_RUST_TOKEN_PREFILTER_BINARY = _RUST_TOKEN_PREFILTER_TARGET_DIR / "release" / "rename_token_prefilter"

def _is_obfuscated_name(old: str, kind: str) -> bool:
    k = (kind or "").strip().lower()
    if k == "method":
        return bool(OBF_METHOD_RX.match(old)) or bool(OBF_NAME_RX.match(old))
    if k in ("param", "local"):
        return bool(OBF_PARAM_LOCAL_RX.match(old)) or bool(OBF_NAME_RX.match(old))
    return bool(OBF_NAME_RX.match(old))


def _load_changed_files(src_dir: Path, list_path: Path) -> set[Path]:
    if not list_path.exists():
        return set()
    out: set[Path] = set()
    for raw in list_path.read_text(encoding="utf-8", errors="replace").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = (src_dir / p).resolve()
        else:
            p = p.resolve()
        if p.suffix != ".java":
            continue
        if p.exists():
            out.add(p)
    return out


def _resolve_rust_token_prefilter_binary(*, rust_mode: str) -> Path | None:
    override = os.environ.get("RUST_RENAME_PREFILTER_BIN", "").strip()
    if override:
        candidate = Path(override).resolve()
        if candidate.exists():
            return candidate
    candidate = _RUST_TOKEN_PREFILTER_BINARY.resolve()
    if candidate.exists():
        return candidate
    if rust_mode != "build":
        return None
    try:
        subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "--manifest-path",
                str(_RUST_TOKEN_PREFILTER_MANIFEST),
                "--target-dir",
                str(_RUST_TOKEN_PREFILTER_TARGET_DIR),
            ],
            check=True,
        )
    except Exception:
        return None
    candidate = _RUST_TOKEN_PREFILTER_BINARY.resolve()
    if candidate.exists():
        return candidate
    return None


def _run_rust_token_prefilter(
    *,
    src_dir: Path,
    target_java_files: list[Path],
    tokens: list[str],
    cache_dir: Path,
    jobs: int,
    rust_mode: str,
) -> set[Path] | None:
    if rust_mode == "off":
        return None
    binary = _resolve_rust_token_prefilter_binary(rust_mode=rust_mode)
    if binary is None:
        return None
    if not target_java_files or not tokens:
        return set()
    work_dir = (cache_dir / "rust-prefilter").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    tokens_file = work_dir / "tokens.txt"
    files_file = work_dir / "files.txt"
    matched_file = work_dir / "matched.txt"
    uniq_tokens = sorted({t for t in tokens if t})
    tokens_file.write_text("\n".join(uniq_tokens) + ("\n" if uniq_tokens else ""), encoding="utf-8")
    files_file.write_text(
        "\n".join(str(p.resolve()) for p in target_java_files) + ("\n" if target_java_files else ""),
        encoding="utf-8",
    )
    cmd = [
        str(binary),
        "--src-dir",
        str(src_dir),
        "--tokens-file",
        str(tokens_file),
        "--files-list",
        str(files_file),
        "--out-file",
        str(matched_file),
        "--jobs",
        str(max(1, jobs)),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except Exception:
        return None
    out: set[Path] = set()
    if not matched_file.exists():
        return out
    for raw in matched_file.read_text(encoding="utf-8", errors="replace").splitlines():
        value = raw.strip()
        if not value:
            continue
        p = Path(value)
        if not p.is_absolute():
            p = (src_dir / p).resolve()
        else:
            p = p.resolve()
        if p.suffix == ".java" and p.exists():
            out.add(p)
    return out


def _build_token_prefilter(tokens: list[str], *, chunk_size: int = 400) -> list[re.Pattern[str]]:
    if not tokens:
        return []
    uniq = sorted({t for t in tokens if t}, key=len, reverse=True)
    patterns: list[re.Pattern[str]] = []
    for i in range(0, len(uniq), chunk_size):
        chunk = uniq[i : i + chunk_size]
        alt = "|".join(re.escape(t) for t in chunk)
        patterns.append(re.compile(rf"\b(?:{alt})\b"))
    return patterns


def _text_has_any_token(text: str, patterns: list[re.Pattern[str]]) -> bool:
    for rx in patterns:
        if rx.search(text):
            return True
    return False


def _partition_paths(paths: list[Path], shards: int) -> list[list[Path]]:
    buckets: list[list[Path]] = [[] for _ in range(shards)]
    for idx, path in enumerate(paths):
        buckets[idx % shards].append(path)
    return [bucket for bucket in buckets if bucket]


def _write_changed_file_list(path: Path, *, src_dir: Path, files: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for p in files:
        try:
            lines.append(str(p.resolve().relative_to(src_dir.resolve())))
        except ValueError:
            lines.append(str(p.resolve()))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _run_parallel_subprocess_renames(
    *,
    args,
    src_dir: Path,
    csv_args: list[str],
    extract_manifest_dir: Path | None,
    target_java_files: list[Path],
) -> tuple[dict[str, int], int, list[str], int]:
    shard_count = max(1, args.jobs)
    shards = _partition_paths(target_java_files, shard_count)
    work_dir = (args.cache_dir / "parallel-shards").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    procs: list[tuple[int, subprocess.Popen[str], Path]] = []
    for shard_idx, shard_files in enumerate(shards):
        changed_list = work_dir / f"shard-{shard_idx:02d}.changed.txt"
        shard_report = work_dir / f"shard-{shard_idx:02d}.report.md"
        shard_report_json = work_dir / f"shard-{shard_idx:02d}.report.json"
        _write_changed_file_list(changed_list, src_dir=src_dir, files=shard_files)

        cmd: list[str] = [
            sys.executable,
            "-m",
            "tools.refactor.cli.ts_rename_identifiers",
            *csv_args,
            "--src-dir",
            str(src_dir),
            "--report",
            str(shard_report),
            "--report-json",
            str(shard_report_json),
            "--max-mappings",
            str(args.max_mappings),
            "--jobs",
            "1",
            "--language-so",
            str(args.language_so),
            "--cache-dir",
            str(args.cache_dir),
            "--changed-files-list",
            str(changed_list),
        ]
        if extract_manifest_dir:
            cmd.extend(["--extract-manifest-dir", str(extract_manifest_dir)])
        if args.dry_run:
            cmd.append("--dry-run")
        if args.allow_non_obfuscated:
            cmd.append("--allow-non-obfuscated")
        if args.safe_preflight:
            cmd.append("--safe-preflight")
        if args.no_cache:
            cmd.append("--no-cache")
        if args.disable_token_prefilter:
            cmd.append("--disable-token-prefilter")
        cmd.extend(["--rust-token-prefilter", "off"])

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append((shard_idx, proc, shard_report_json))

    renamed_files: dict[str, int] = {}
    renamed_total = 0
    skipped_items: list[str] = []
    semantic_files_pruned = 0
    for shard_idx, proc, shard_report_json in procs:
        out, err = proc.communicate()
        if proc.returncode != 0:
            tail = (err or out or "").strip().splitlines()[-20:]
            msg = "\n".join(tail)
            raise ValidationError(f"parallel rename shard {shard_idx} failed:\n{msg}")
        if not shard_report_json.exists():
            continue
        data = json.loads(shard_report_json.read_text(encoding="utf-8"))
        files_map = data.get("renamed_files")
        if isinstance(files_map, dict):
            for key, value in files_map.items():
                try:
                    renamed_files[str(key)] = int(value)
                except Exception:
                    continue
        try:
            renamed_total += int(data.get("renamed_identifiers", 0))
        except Exception:
            pass
        try:
            semantic_files_pruned += int(data.get("semantic_files_pruned", 0))
        except Exception:
            pass
        shard_skips = data.get("skipped_items")
        if isinstance(shard_skips, list):
            skipped_items.extend(str(item) for item in shard_skips)
    deduped_skips = list(dict.fromkeys(skipped_items))
    return renamed_files, renamed_total, deduped_skips, semantic_files_pruned


def main(argv: list[str]) -> int:
    logger = get_logger(__name__)
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, action="append", default=[])
    ap.add_argument("--csv-dir", type=Path, default=Path("client/refactor/.refactor-plan/symbol-renames/generated"))
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"))
    ap.add_argument(
        "--extract-manifest-dir",
        type=Path,
        default=None,
        help="Optional directory of extract-statics manifests; when present, member renames are also applied to moved targets.",
    )
    ap.add_argument("--report", type=Path, default=Path("docs/rename-report-refactor.md"))
    ap.add_argument("--report-json", type=Path, default=Path("build/refactor-state/rename-summary.json"))
    ap.add_argument("--max-mappings", type=int, default=25, help="Apply at most N mapping rows (default 25). Use -1 for all.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-non-obfuscated", action="store_true", help="Allow renaming identifiers not matching the default obfuscated-name regex.")
    ap.add_argument(
        "--safe-preflight",
        action="store_true",
        help="Preflight mappings and skip unsafe rows (e.g. ambiguous field declarations).",
    )
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    ap.add_argument("--cache-dir", type=Path, default=Path("build/refactor-cache/ts_rename_identifiers"))
    ap.add_argument("--no-cache", action="store_true", help="Disable disk cache for indexes.")
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("RENAME_JOBS", "1")), help="Number of worker threads for per-file rename passes (default 1).")
    ap.add_argument(
        "--rust-token-prefilter",
        choices=("off", "auto", "build"),
        default=os.environ.get("RENAME_RUST_PREFILTER", "off"),
        help="Use Rust token prefilter to prune files before AST parse (off|auto|build).",
    )
    ap.add_argument(
        "--changed-files-list",
        type=Path,
        default=None,
        help="Optional newline-delimited Java file list (relative to --src-dir) to limit rewritten files.",
    )
    ap.add_argument(
        "--disable-token-prefilter",
        action="store_true",
        help="Disable fast token prefilter that skips parsing files with no candidate identifiers.",
    )
    ap.add_argument(
        "--semantic-members",
        action="store_true",
        default=True,
        help="Rename method/field uses based on best-effort receiver type (default on).",
    )
    args = ap.parse_args(argv)

    root = Path.cwd()
    src_dir = args.src_dir.resolve()
    if not src_dir.exists():
        raise ValidationError(f"--src-dir not found: {src_dir}")
    extract_manifest_dir = (args.extract_manifest_dir.resolve() if args.extract_manifest_dir else None)
    csv_args: list[str] = []
    for csv_path in args.csv:
        csv_args.extend(["--csv", str(Path(csv_path))])
    csv_args.extend(["--csv-dir", str(args.csv_dir)])

    mappings = load_mappings(csv_files=[Path(p) for p in (args.csv or [])], csv_dir=args.csv_dir)
    if not mappings:
        raise ValidationError("No mappings found (expected --csv files and/or --csv-dir).")
    if args.max_mappings >= 0:
        mappings = mappings[: args.max_mappings]

    all_java_files = sorted(src_dir.rglob("*.java"))
    changed_files: set[Path] = set()
    if args.changed_files_list is not None:
        changed_files = _load_changed_files(src_dir, args.changed_files_list.resolve())
    target_java_files = all_java_files
    if changed_files:
        target_java_files = [p for p in all_java_files if p.resolve() in changed_files]

    log_event(
        logger,
        20,
        "rename.start",
        src_dir=src_dir,
        java_files=len(target_java_files),
        java_files_total=len(all_java_files),
        mappings=len(mappings),
        changed_files=bool(changed_files),
        incremental=(len(target_java_files) < len(all_java_files)),
    )
    java = build_java_language(out_so=args.language_so)
    parser = Parser()
    parser.set_language(java)
    field_decls: dict[str, set[Path]] = {}
    class_index = ClassIndex(fields={}, extends_of={}, methods={}, implements_of={})
    need_indexes = bool(args.safe_preflight or args.semantic_members)
    if need_indexes:
        fingerprint = _java_tree_fingerprint(src_dir=src_dir, java_files=all_java_files)
        cache_path = (args.cache_dir / f"indexes-{fingerprint}.json").resolve()
        cached = None if args.no_cache else _load_cached_indexes(cache_path)
        if cached is not None:
            field_decls = cached.field_decls
            class_index = cached.class_index
        else:
            built = _build_indexes(parser=parser, java_files=all_java_files)
            field_decls = built.field_decls
            class_index = built.class_index
            if not args.no_cache:
                _write_cached_indexes(cache_path, field_decls=field_decls, class_index=class_index)

    moved_members: dict[tuple[str, str, str], str] = {}
    if extract_manifest_dir:
        moved_members = load_static_extract_moves(extract_manifest_dir)

    prepared, skipped = build_prepared_mappings(
        mappings=mappings,
        src_dir=src_dir,
        java_files=all_java_files,
        field_decls=field_decls,
        allow_non_obfuscated=args.allow_non_obfuscated,
        safe_preflight=args.safe_preflight,
        is_obfuscated_name=_is_obfuscated_name,
        parse_signature_types=parse_signature_types,
    )

    if not prepared:
        raise ValidationError("No applicable mappings after filtering.")

    conflict = validate_prepared_conflicts(prepared)
    if conflict:
        raise ConflictError(conflict)

    renamed_files: dict[str, int] = {}
    renamed_total = 0
    overlap_skips: list[str] = []
    rust_prefilter_files_pruned = 0

    # Index mappings by old identifier for fast lookup during traversal.
    by_old_index: dict[str, list[PreparedMapping]] = {}
    for m in prepared:
        by_old_index.setdefault(m.old, []).append(m)
    rust_prefilter_used = False
    rust_mode = (args.rust_token_prefilter or "auto").strip().lower()
    should_try_rust_prefilter = (
        rust_mode == "build"
        or (
            rust_mode == "auto"
            and (
                (args.changed_files_list is None)
                and
                len(by_old_index) <= 512
                and len(target_java_files) >= 200
            )
        )
    )
    rust_prefilter_matches = None
    if should_try_rust_prefilter:
        rust_prefilter_matches = _run_rust_token_prefilter(
            src_dir=src_dir,
            target_java_files=target_java_files,
            tokens=list(by_old_index.keys()),
            cache_dir=args.cache_dir,
            jobs=max(1, args.jobs),
            rust_mode=rust_mode,
        )
    if rust_prefilter_matches is not None:
        rust_prefilter_used = True
        before = len(target_java_files)
        target_java_files = [p for p in target_java_files if p.resolve() in rust_prefilter_matches]
        rust_prefilter_files_pruned = max(0, before - len(target_java_files))
        log_event(
            logger,
            20,
            "rename.rust_prefilter",
            scanned=before,
            matched=len(target_java_files),
            pruned=rust_prefilter_files_pruned,
        )
    if not target_java_files:
        report_obj = Report(
            renamed_files={},
            renamed_total=0,
            skipped=skipped,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_obj.to_markdown() + "\n", encoding="utf-8")
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        summary = report_obj.to_summary_json()
        summary["renamed_files"] = {}
        summary["skipped_items"] = skipped
        summary["semantic_files_pruned"] = 0
        summary["rust_prefilter_files_pruned"] = rust_prefilter_files_pruned
        args.report_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log_event(
            logger,
            20,
            "rename.done",
            renamed=0,
            files=0,
            skipped=len(skipped),
            semantic_files_pruned=0,
            rust_prefilter_files_pruned=rust_prefilter_files_pruned,
            report=args.report,
        )
        return 0
    token_prefilter_patterns = []
    use_token_prefilter = (
        (not args.disable_token_prefilter)
        and len(target_java_files) < len(all_java_files)
        and (not rust_prefilter_used)
    )
    if use_token_prefilter:
        token_prefilter_patterns = _build_token_prefilter(list(by_old_index.keys()))

    field_map: dict[tuple[str, str], str] = {}
    method_map: dict[tuple[str, str], list[MethodRename]] = {}
    if args.semantic_members:
        for m in prepared:
            k = (m.kind or "").strip().lower()
            if k not in ("field", "method"):
                continue
            owner = (m.owner or "").strip()
            if not owner:
                continue
            if k == "field":
                field_map[(owner, m.old)] = m.new
            else:
                method_map.setdefault((owner, m.old), []).append(
                    MethodRename(signature_types=m.signature_types, new=m.new)
                )
            if moved_members:
                cur_owner = owner
                for _ in range(5):
                    target = moved_members.get((cur_owner, k, m.old))
                    if not target or target == cur_owner:
                        break
                    if k == "field":
                        field_map[(target, m.old)] = m.new
                    else:
                        method_map.setdefault((target, m.old), []).append(
                            MethodRename(signature_types=m.signature_types, new=m.new)
                        )
                    cur_owner = target
    semantic_owner_set: set[str] = set()
    for owner, _name in field_map.keys():
        semantic_owner_set.add(owner)
    for owner, _name in method_map.keys():
        semantic_owner_set.add(owner)

    jobs = max(1, args.jobs)
    if jobs > 1 and len(target_java_files) > 1 and not args.no_cache:
        renamed_files, renamed_total, overlap_skips, semantic_files_pruned = _run_parallel_subprocess_renames(
            args=args,
            src_dir=src_dir,
            csv_args=csv_args,
            extract_manifest_dir=extract_manifest_dir,
            target_java_files=target_java_files,
        )
        report_obj = Report(
            renamed_files=renamed_files,
            renamed_total=renamed_total,
            skipped=overlap_skips,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report_obj.to_markdown() + "\n", encoding="utf-8")
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        summary = report_obj.to_summary_json()
        summary["renamed_files"] = renamed_files
        summary["skipped_items"] = overlap_skips
        summary["semantic_files_pruned"] = semantic_files_pruned
        summary["rust_prefilter_files_pruned"] = rust_prefilter_files_pruned
        args.report_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log_event(
            logger,
            20,
            "rename.done",
            renamed=renamed_total,
            files=len(renamed_files),
            skipped=len(overlap_skips),
            semantic_files_pruned=semantic_files_pruned,
            rust_prefilter_files_pruned=rust_prefilter_files_pruned,
            report=args.report,
            parallel_shards=jobs,
        )
        return 0

    thread_local = threading.local()

    def _get_thread_parser() -> Parser:
        parser_obj = getattr(thread_local, "parser", None)
        if parser_obj is None:
            parser_obj = Parser()
            parser_obj.set_language(java)
            thread_local.parser = parser_obj
        return parser_obj

    semantic_candidate_patterns = _build_token_prefilter(
        [m.old for m in prepared if (m.kind or "").strip().lower() in ("field", "method")]
    )

    def _process_path(path: Path) -> tuple[str | None, int, list[str], bool]:
        data = path.read_bytes()
        need_fast_text = bool(token_prefilter_patterns or semantic_candidate_patterns)
        text_fast = ""
        if need_fast_text:
            text_fast = data.decode("utf-8", errors="replace")
        if token_prefilter_patterns:
            if not _text_has_any_token(text_fast, token_prefilter_patterns):
                return None, 0, [], False
        local_parser = _get_thread_parser()
        tree = local_parser.parse(data)
        all_nodes = list(_iter_nodes(tree.root_node))
        resolved_path = path.resolve()
        edits: list[tuple[int, int, bytes]] = []
        local_skips: list[str] = []

        for n in all_nodes:
            if n.type not in ("identifier", "type_identifier"):
                continue
            if n.type == "type_identifier":
                p = n.parent
                is_cast_context = False
                hops = 0
                while p is not None and hops < 6:
                    if p.type == "cast_expression":
                        is_cast_context = True
                        break
                    p = p.parent
                    hops += 1
                if not is_cast_context:
                    continue
            tok = data[n.start_byte : n.end_byte]
            try:
                s = tok.decode("utf-8")
            except UnicodeDecodeError:
                continue
            candidates = by_old_index.get(s)
            if not candidates:
                continue
            scoped = [m for m in candidates if not m.scope_files or resolved_path in m.scope_files]
            if not scoped:
                continue
            member_name: str | None = None
            filtered_by_member: list[PreparedMapping] = []
            for m in scoped:
                mk = (m.kind or "").strip().lower()
                if mk in ("local", "param") and (m.member or "").strip():
                    if member_name is None:
                        member_name = enclosing_member_name(data, n)
                    if member_name != m.member:
                        continue
                filtered_by_member.append(m)
            if filtered_by_member:
                scoped = filtered_by_member
            else:
                continue
            if args.semantic_members:
                filtered: list[PreparedMapping] = []
                for m in scoped:
                    k = (m.kind or "").strip().lower()
                    if k == "method":
                        continue
                    if k == "field" and not is_field_declarator_name_node(n):
                        continue
                    filtered.append(m)
                if filtered:
                    scoped = filtered
                else:
                    continue
            new = scoped[0].new
            if new == s:
                continue
            edits.append((n.start_byte, n.end_byte, new.encode("utf-8")))

        run_semantic_pass = args.semantic_members and (field_map or method_map)
        semantic_skipped = False
        if run_semantic_pass and semantic_candidate_patterns:
            # Fast pruning: skip semantic scope analysis when the file does not
            # contain any candidate old member names.
            if not _text_has_any_token(text_fast, semantic_candidate_patterns):
                run_semantic_pass = False
                semantic_skipped = True

        if run_semantic_pass:
            current_class = ""
            for n in all_nodes:
                if n.type not in ("class_declaration", "interface_declaration", "enum_declaration"):
                    continue
                name_node = n.child_by_field_name("name")
                if name_node is None:
                    continue
                current_class = _node_text(data, name_node).strip()
                if current_class:
                    break
            if current_class:
                owner_closure = list(_iter_owner_closure(current_class, class_index))
                has_owner_scoped_semantic = any(owner in semantic_owner_set for owner in owner_closure)
                owner_closure_cache: dict[str, tuple[str, ...]] = {current_class: tuple(owner_closure)}
                for n in all_nodes:
                    if n.type != "method_declaration":
                        continue
                    if not has_owner_scoped_semantic:
                        continue
                    name_node = n.child_by_field_name("name")
                    if name_node is None:
                        continue
                    old_name = _node_text(data, name_node).strip()
                    if not old_name:
                        continue
                    params_node = n.child_by_field_name("parameters")
                    declared_types: list[str] = []
                    if params_node is not None:
                        for p in getattr(params_node, "named_children", []) or []:
                            if p.type != "formal_parameter":
                                continue
                            tnode = p.child_by_field_name("type")
                            if tnode is None:
                                continue
                            declared_types.append(_normalize_type_name(_node_text(data, tnode)))
                    new_name = ""
                    for owner in _iter_owner_closure(current_class, class_index):
                        cands = method_map.get((owner, old_name), [])
                        if not cands:
                            continue
                        picked = pick_method_rename(candidates=cands, arg_types=declared_types)
                        if picked:
                            new_name = picked
                            break
                    if new_name and new_name != old_name:
                        edits.append((name_node.start_byte, name_node.end_byte, new_name.encode("utf-8")))

                scopes: list[Node] = [
                    n
                    for n in all_nodes
                    if n.type
                    in (
                        "method_declaration",
                        "constructor_declaration",
                        "static_initializer",
                        "instance_initializer",
                        "field_declaration",
                    )
                ]
                for scope in scopes:
                    env = collect_method_env(
                        data=data, class_index=class_index, current_class=current_class, scope_node=scope
                    )
                    edits.extend(
                        rename_member_accesses(
                            data=data,
                            class_index=class_index,
                            env=env,
                            scope_node=scope,
                            field_map=field_map,
                            method_map=method_map,
                            skip_unqualified_identifiers=(not has_owner_scoped_semantic),
                            owner_closure_cache=owner_closure_cache,
                        )
                    )

        if not edits:
            return None, 0, local_skips, semantic_skipped
        edits, skips = dedupe_and_prune_overlaps(edits)
        if skips:
            local_skips.extend([f"{path.name}: {s}" for s in skips[:20]])
            if len(skips) > 20:
                local_skips.append(f"{path.name}: (plus {len(skips) - 20} more overlap skips)")
        out = apply_edits_bytes(data, edits)
        if out == data:
            return None, 0, local_skips, semantic_skipped
        if not args.dry_run:
            path.write_bytes(out)
        try:
            rel = str(path.relative_to(src_dir))
        except ValueError:
            rel = str(path.relative_to(root))
        return rel, len(edits), local_skips, semantic_skipped

    if jobs == 1:
        results = [_process_path(path) for path in target_java_files]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(_process_path, target_java_files))

    semantic_files_pruned = 0
    for rel, edit_count, local_skips, semantic_skipped in results:
        if semantic_skipped:
            semantic_files_pruned += 1
        if local_skips:
            overlap_skips.extend(local_skips)
        if rel is None:
            continue
        renamed_files[rel] = edit_count
        renamed_total += edit_count

    report_obj = Report(
        renamed_files=renamed_files,
        renamed_total=renamed_total,
        skipped=(skipped + overlap_skips),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_obj.to_markdown() + "\n", encoding="utf-8")
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    summary = report_obj.to_summary_json()
    summary["renamed_files"] = renamed_files
    summary["skipped_items"] = skipped + overlap_skips
    summary["semantic_files_pruned"] = semantic_files_pruned
    summary["rust_prefilter_files_pruned"] = rust_prefilter_files_pruned
    args.report_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log_event(
        logger,
        20,
        "rename.done",
        renamed=renamed_total,
        files=len(renamed_files),
        skipped=len(skipped + overlap_skips),
        semantic_files_pruned=semantic_files_pruned,
        rust_prefilter_files_pruned=rust_prefilter_files_pruned,
        report=args.report,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValidationError, ConflictError) as exc:
        print(str(exc))
        raise SystemExit(1)
