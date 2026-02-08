#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tools.refactor.common.constants import (
    CLIENT_SRC_DIR,
    DEFAULT_MAX_MANIFESTS,
    DEFAULT_MAX_RENAMES,
    REFACTOR_CLASSES_CSV,
    REFACTOR_EXTRACT_MANIFEST_DIR,
    REFACTOR_PIPELINE_REPORT_MD,
    REFACTOR_PLAN_DIR,
    REFACTOR_SRC_DIR,
    REFACTOR_STATE_DIR,
    REFACTOR_SYMBOL_DIR,
    REFACTOR_SYMBOLS_CSV,
    REFACTOR_VIEWS_REPORT_MD,
)

@dataclasses.dataclass(frozen=True)
class Step:
    name: str
    cmd: list[str]
    marker: Path
    log_path: Path


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash_cmd(cmd: list[str]) -> str:
    return hashlib.sha256("\0".join(cmd).encode("utf-8")).hexdigest()[:16]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_step(step: Step, *, cwd: Path, env: dict[str, str]) -> tuple[int, float]:
    start = time.time()
    proc = subprocess.run(step.cmd, cwd=str(cwd), env=env, text=True, capture_output=True)
    elapsed = time.time() - start
    step.log_path.parent.mkdir(parents=True, exist_ok=True)
    step.log_path.write_text(
        "\n".join(
            [
                f"$ {' '.join(step.cmd)}",
                "",
                "## stdout",
                proc.stdout or "",
                "",
                "## stderr",
                proc.stderr or "",
            ]
        ),
        encoding="utf-8",
    )
    return proc.returncode, elapsed


def _build_steps(args) -> list[Step]:
    py = args.python
    state_dir = args.state_dir
    logs_dir = state_dir / "logs"

    rename_csv = args.rename_csv or args.symbols_csv
    rename_csv_dir = args.rename_csv_dir or args.symbols_csv_dir
    selected_csv = state_dir / "selected-renames.csv"
    extract_touched_files = state_dir / "02-extract-touched-files.txt"
    extract_state_file = state_dir / "02-extract-state.json"

    steps: list[Step] = []
    steps.append(
        Step(
            name="build_views",
            cmd=[
                py,
                "-m",
                "tools.refactor.cli.build_refactor_views",
                "--plan-dir",
                str(args.plan_dir),
                "--refactor-src",
                str(args.refactor_src_dir),
                "--out-symbol-root",
                str(args.symbols_csv),
                "--out-symbol-dir",
                str(args.symbols_csv_dir),
                "--out-class-csv",
                str(args.classes_csv),
                "--out-extract-dir",
                str(args.extract_manifest_dir),
                "--report",
                str(REFACTOR_VIEWS_REPORT_MD),
            ]
            + (["--allow-conflicts"] if args.allow_conflicts else []),
            marker=state_dir / "00-build-views.json",
            log_path=logs_dir / "00-build-views.log",
        )
    )
    if not args.skip_class_renames:
        steps.append(
            Step(
                name="class_renames",
                cmd=[
                    py,
                    "-m",
                    "tools.refactor.cli.apply_class_renames",
                    "--csv",
                    str(args.classes_csv),
                    "--src-dir",
                    str(args.class_src_dir),
                "--report",
                "docs/rename-report.md",
                    "--max-renames",
                    str(args.max_renames),
                ]
                + (["--dry-run"] if args.dry_run else []),
                marker=state_dir / "01-class-renames.json",
                log_path=logs_dir / "01-class-renames.log",
            )
        )
    # Extraction before symbol renames is important: it changes owning classes,
    # and the renamer can then apply moved-member aliases using the manifests.
    steps.append(
        Step(
            name="extract_statics",
            cmd=[
                py,
                "-m",
                "tools.refactor.cli.extract_statics_ts",
                "--src-dir",
                str(args.refactor_src_dir),
                "--manifest-dir",
                str(args.extract_manifest_dir),
                "--max-manifests",
                str(args.max_manifests),
                "--jobs",
                str(args.extract_jobs),
                "--rust-callsites",
                str(args.extract_rust_callsites),
                "--write-touched-files",
                str(extract_touched_files),
                "--state-file",
                str(extract_state_file),
            ]
            + (["--dry-run"] if args.dry_run else []),
            marker=state_dir / "02-extract-statics.json",
            log_path=logs_dir / "02-extract-statics.log",
        )
    )
    # check_static_extract validates that targets contain moved members. In dry-run
    # mode we intentionally don't mutate the tree, so the check would always fail.
    if not args.dry_run:
        steps.append(
            Step(
                name="check_extract",
                cmd=[
                    py,
                    "-m",
                    "tools.refactor.cli.check_static_extract",
                    "--src-dir",
                    str(args.refactor_src_dir),
                    "--manifest-dir",
                    str(args.extract_manifest_dir),
                ]
                + (["--skip-callsites"] if args.fast_validate else []),
                marker=state_dir / "03-check-extract.json",
                log_path=logs_dir / "03-check-extract.log",
            )
        )
    if args.select_effective and args.max_renames >= 0:
        steps.append(
            Step(
                name="select_renames",
                cmd=[
                    py,
                    "-m",
                    "tools.refactor.cli.select_effective_renames",
                    "--src-dir",
                    str(args.refactor_src_dir),
                    "--in-csv",
                    str(rename_csv),
                    "--out-csv",
                    str(selected_csv),
                    "--max",
                    str(args.max_renames),
                    "--min-confidence",
                    str(args.min_confidence),
                ]
                + sum((["--status", s] for s in args.status), [])
                + sum((["--phase", p] for p in args.phase), []),
                marker=state_dir / "03-select-renames.json",
                log_path=logs_dir / "03-select-renames.log",
            )
        )
    steps.append(
        Step(
            name="symbol_renames",
            cmd=[
                py,
                "-m",
                "tools.refactor.cli.ts_rename_identifiers",
                "--csv",
                str(selected_csv if args.select_effective and args.max_renames >= 0 else rename_csv),
                "--csv-dir",
                str(Path("/tmp/void-empty-dir") if args.select_effective and args.max_renames >= 0 else rename_csv_dir),
                "--src-dir",
                str(args.refactor_src_dir),
                "--extract-manifest-dir",
                str(args.extract_manifest_dir),
                "--report",
                "docs/rename-report-refactor.md",
                "--max-mappings",
                str(-1 if args.select_effective and args.max_renames >= 0 else args.max_renames),
                "--jobs",
                str(args.rename_jobs),
                "--rust-token-prefilter",
                str(args.rename_rust_prefilter),
                "--safe-preflight",
                "--allow-non-obfuscated",
            ]
            + (["--changed-files-list", str(extract_touched_files)] if args.incremental_rename else [])
            + (["--dry-run"] if args.dry_run else []),
            marker=state_dir / "04-symbol-renames.json",
            log_path=logs_dir / "04-symbol-renames.log",
        )
    )
    if not args.dry_run and not args.skip_compile:
        steps.append(
            Step(
                name="compile_refactor",
                cmd=(["make", "compile-refactor-fast"] if args.fast_compile else [
                    "make",
                    "compile-recursive",
                    f"SRC_DIR={args.refactor_src_dir}",
                    "CLASSES_DIR=build/classes-refactor-layout",
                    "SOURCES_FILE=build/sources-refactor-layout.txt",
                ]),
                marker=state_dir / "05-compile-refactor.json",
                log_path=logs_dir / "05-compile-refactor.log",
            )
        )
    return steps


def _write_report(path: Path, rows: list[dict], *, dry_run: bool, state_dir: Path) -> None:
    lines: list[str] = []
    lines.append("# Refactor pipeline report")
    lines.append("")
    lines.append(f"- Ran at: `{_now_iso()}`")
    lines.append(f"- Dry-run: `{str(dry_run).lower()}`")
    lines.append(f"- State dir: `{state_dir}`")
    lines.append("")
    lines.append("| step | status | seconds | marker | log |")
    lines.append("|---|---|---:|---|---|")
    for row in rows:
        lines.append(
            f"| `{row['step']}` | {row['status']} | {row['seconds']:.2f} | `{row['marker']}` | `{row['log']}` |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Unified pipeline for class/symbol renames + static extraction.")
    ap.add_argument("--python", default=sys.executable, help="Python interpreter for sub-tools")
    ap.add_argument("--classes-csv", type=Path, default=REFACTOR_CLASSES_CSV)
    ap.add_argument("--symbols-csv", type=Path, default=REFACTOR_SYMBOLS_CSV)
    ap.add_argument("--symbols-csv-dir", type=Path, default=REFACTOR_SYMBOL_DIR)
    ap.add_argument(
        "--rename-csv",
        type=Path,
        default=None,
        help="Optional override for symbol renames input CSV (build_views outputs still go to --symbols-csv).",
    )
    ap.add_argument(
        "--rename-csv-dir",
        type=Path,
        default=None,
        help="Optional override for symbol renames input directory (build_views outputs still go to --symbols-csv-dir).",
    )
    ap.add_argument("--extract-manifest-dir", type=Path, default=REFACTOR_EXTRACT_MANIFEST_DIR)
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_PLAN_DIR)
    ap.add_argument("--class-src-dir", type=Path, default=CLIENT_SRC_DIR)
    ap.add_argument("--refactor-src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--state-dir", type=Path, default=REFACTOR_STATE_DIR)
    ap.add_argument("--report", type=Path, default=REFACTOR_PIPELINE_REPORT_MD)
    ap.add_argument("--max-renames", type=int, default=int(os.environ.get("MAX_RENAMES", str(DEFAULT_MAX_RENAMES))))
    ap.add_argument("--max-manifests", type=int, default=int(os.environ.get("MAX_MANIFESTS", str(DEFAULT_MAX_MANIFESTS))))
    ap.add_argument("--extract-jobs", type=int, default=int(os.environ.get("EXTRACT_JOBS", "1")))
    ap.add_argument(
        "--extract-rust-callsites",
        default=os.environ.get("EXTRACT_RUST_CALLSITES", "auto"),
        choices=["off", "auto", "build"],
    )
    ap.add_argument("--rename-jobs", type=int, default=int(os.environ.get("RENAME_JOBS", "1")))
    ap.add_argument(
        "--rename-rust-prefilter",
        default=os.environ.get("RENAME_RUST_PREFILTER", "off"),
        choices=["off", "auto", "build"],
    )
    ap.add_argument("--no-select-effective", action="store_true", help="Disable selecting effective renames (use raw first-N rows).")
    ap.add_argument("--min-confidence", default="high", choices=["low", "medium", "high"])
    ap.add_argument("--status", action="append", default=["approved"], help="Allowed status for rename rows (repeatable).")
    ap.add_argument("--phase", action="append", default=[], help="Allowed phase for rename rows (repeatable).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true", help="Skip steps with successful marker in state-dir.")
    ap.add_argument(
        "--fast-validate",
        action="store_true",
        help="Use faster/less-strict extract validation (skips global callsite checks).",
    )
    ap.add_argument(
        "--incremental-rename",
        action="store_true",
        help="Limit symbol rename parsing to extract-touched files for faster loop iterations.",
    )
    ap.add_argument(
        "--fast-compile",
        action="store_true",
        help="Use compile-refactor-fast (ECJ) instead of full javac recursive compile.",
    )
    ap.add_argument("--skip-compile", action="store_true")
    ap.add_argument("--skip-class-renames", action="store_true")
    ap.add_argument("--allow-conflicts", action="store_true")
    args = ap.parse_args(argv)
    args.select_effective = not args.no_select_effective

    cwd = Path.cwd()
    state_dir = args.state_dir.resolve()
    rows: list[dict] = []
    env = dict(os.environ)

    steps = _build_steps(args)
    for step in steps:
        cmd_hash = _hash_cmd(step.cmd)
        if args.resume and step.marker.exists():
            try:
                marker_data = json.loads(step.marker.read_text(encoding="utf-8"))
            except Exception:
                marker_data = {}
            if marker_data.get("status") == "ok" and marker_data.get("cmd_hash") == cmd_hash:
                rows.append(
                    {
                        "step": step.name,
                        "status": "skipped(resume)",
                        "seconds": 0.0,
                        "marker": str(step.marker),
                        "log": str(step.log_path),
                    }
                )
                print(f"[resume] {step.name} (marker matched)")
                continue

        print(f"[run] {step.name}: {' '.join(step.cmd)}")
        code, seconds = _run_step(step, cwd=cwd, env=env)
        marker = {
            "step": step.name,
            "status": "ok" if code == 0 else "error",
            "returncode": code,
            "seconds": seconds,
            "cmd": step.cmd,
            "cmd_hash": cmd_hash,
            "ran_at": _now_iso(),
            "log": str(step.log_path),
        }
        _write_json(step.marker, marker)
        rows.append(
            {
                "step": step.name,
                "status": "ok" if code == 0 else "error",
                "seconds": seconds,
                "marker": str(step.marker),
                "log": str(step.log_path),
            }
        )
        if code != 0:
            _write_report(args.report, rows, dry_run=args.dry_run, state_dir=state_dir)
            print(f"[fail] {step.name} (see {step.log_path})")
            return code

    _write_report(args.report, rows, dry_run=args.dry_run, state_dir=state_dir)
    print(f"Pipeline complete. Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
