#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from tools.refactor.common.cli import resolve_path_args
from tools.refactor.common.constants import (
    REFACTOR_CLEANUP_PLAN_DIR,
    REFACTOR_EXTRACT_MANIFEST_DIR,
    REFACTOR_PLAN_DIR,
    REFACTOR_SRC_DIR,
)


def _run(cmd: list[str]) -> None:
    print(f"[run] {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")


def _read_apply_stats(summary_json: Path) -> tuple[int, int, int]:
    if not summary_json.exists():
        return 0, 0, 0
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    totals = payload.get("totals") or {}
    changed_files = int(payload.get("changed_files") or 0)
    return int(totals.get("matched") or 0), int(totals.get("effective_rules") or 0), changed_files


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run cleanup detect/promote/preflight/apply/prune until no additional "
            "cleanup matches are applied."
        )
    )
    ap.add_argument("--python", default=sys.executable, help="Python interpreter for sub-tools")
    ap.add_argument("--src-dir", type=Path, default=REFACTOR_SRC_DIR)
    ap.add_argument("--plan-dir", type=Path, default=REFACTOR_PLAN_DIR)
    ap.add_argument("--manifest-dir", type=Path, default=REFACTOR_EXTRACT_MANIFEST_DIR)
    ap.add_argument("--cleanup-plan-dir", type=Path, default=REFACTOR_CLEANUP_PLAN_DIR)
    ap.add_argument("--summary-json", type=Path, default=Path("build/refactor-state/cleanup-apply-summary.json"))
    ap.add_argument("--prune-summary-json", type=Path, default=Path("build/refactor-state/cleanup-prune-summary.json"))
    ap.add_argument("--run-summary-json", type=Path, default=Path("build/refactor-state/cleanup-converge-summary.json"))
    ap.add_argument("--max-passes", type=int, default=8)
    ap.add_argument("--drift-warn-ratio", type=float, default=0.90)
    ap.add_argument("--drift-fail-ratio", type=float, default=-1.0)
    ap.add_argument(
        "--fail-on-remap-warnings",
        action="store_true",
        default=True,
        help="Fail when remap emits warnings (default: true).",
    )
    ap.add_argument(
        "--allow-remap-warnings",
        action="store_true",
        help="Do not fail when remap emits warnings.",
    )
    args = ap.parse_args(argv)
    resolve_path_args(
        args,
        (
            "src_dir",
            "plan_dir",
            "manifest_dir",
            "cleanup_plan_dir",
            "summary_json",
            "prune_summary_json",
            "run_summary_json",
        ),
    )

    if args.max_passes <= 0:
        print(f"max-passes must be > 0, got {args.max_passes}")
        return 2

    py = args.python
    generated_dir = args.plan_dir / "generated"
    unified_csv = generated_dir / "unified_cleanup_candidates.csv"
    unified_graph = generated_dir / "unified_cleanup_graph.json"
    fail_on_warnings = args.fail_on_remap_warnings and not args.allow_remap_warnings

    pass_history: list[dict[str, int]] = []
    converged = False

    for pass_index in range(1, args.max_passes + 1):
        print(f"[cleanup-pass {pass_index}]", flush=True)
        _run(
            [
                py,
                "-m",
                "tools.refactor.cleanup.candidate_detector",
                "--src-dir",
                str(args.src_dir),
                "--out-csv",
                str(unified_csv),
                "--out-graph",
                str(unified_graph),
            ]
        )
        remap_cmd = [
            py,
            "-m",
            "tools.refactor.cli.remap_cleanup_owners",
            "--plan-dir",
            str(args.plan_dir),
            "--manifest-dir",
            str(args.manifest_dir),
            "--out-dir",
            str(args.cleanup_plan_dir),
        ]
        if fail_on_warnings:
            remap_cmd.append("--fail-on-warnings")
        _run(remap_cmd)

        preflight_cmd = [
            py,
            "-m",
            "tools.refactor.cli.preflight_refactor_cleanup",
            "--src-dir",
            str(args.src_dir),
            "--plan-dir",
            str(args.cleanup_plan_dir),
            "--drift-warn-ratio",
            str(args.drift_warn_ratio),
        ]
        if args.drift_fail_ratio >= 0:
            preflight_cmd.extend(["--drift-fail-ratio", str(args.drift_fail_ratio)])
        _run(preflight_cmd)

        _run(
            [
                py,
                "-m",
                "tools.refactor.cli.apply_refactor_cleanup",
                "--src-dir",
                str(args.src_dir),
                "--plan-dir",
                str(args.cleanup_plan_dir),
                "--summary-json",
                str(args.summary_json),
            ]
        )
        matched, effective_rules, changed_files = _read_apply_stats(args.summary_json)

        _run(
            [
                py,
                "-m",
                "tools.refactor.cli.prune_cleanup_plan",
                "--plan-dir",
                str(args.cleanup_plan_dir),
                "--summary-json",
                str(args.summary_json),
                "--out-summary-json",
                str(args.prune_summary_json),
            ]
        )

        pass_history.append(
            {
                "pass": pass_index,
                "matched": matched,
                "effective_rules": effective_rules,
                "changed_files": changed_files,
            }
        )
        print(
            f"[cleanup-pass {pass_index}] applied={matched} "
            f"effective_rules={effective_rules} changed_files={changed_files}"
        )
        if changed_files <= 0 or matched <= 0:
            converged = True
            break

    summary = {
        "converged": converged,
        "passes_run": len(pass_history),
        "max_passes": args.max_passes,
        "history": pass_history,
        "src_dir": str(args.src_dir),
        "plan_dir": str(args.plan_dir),
        "cleanup_plan_dir": str(args.cleanup_plan_dir),
    }
    args.run_summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.run_summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if converged:
        print(
            "Cleanup converged. "
            f"passes={summary['passes_run']} run_summary={args.run_summary_json}"
        )
        return 0
    print(
        "Cleanup did not converge within max-passes. "
        f"passes={summary['passes_run']} run_summary={args.run_summary_json}"
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
