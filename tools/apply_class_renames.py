#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path


JAVA_KEYWORDS = {
    "abstract",
    "assert",
    "boolean",
    "break",
    "byte",
    "case",
    "catch",
    "char",
    "class",
    "const",
    "continue",
    "default",
    "do",
    "double",
    "else",
    "enum",
    "extends",
    "final",
    "finally",
    "float",
    "for",
    "goto",
    "if",
    "implements",
    "import",
    "instanceof",
    "int",
    "interface",
    "long",
    "native",
    "new",
    "package",
    "private",
    "protected",
    "public",
    "return",
    "short",
    "static",
    "strictfp",
    "super",
    "switch",
    "synchronized",
    "this",
    "throw",
    "throws",
    "transient",
    "try",
    "void",
    "volatile",
    "while",
    "true",
    "false",
    "null",
}


@dataclasses.dataclass(frozen=True)
class RenameCandidate:
    src_internal: str
    dst_internal: str
    score: float
    anchors: str

    @property
    def src_simple(self) -> str:
        return self.src_internal.split("/")[-1]

    @property
    def dst_simple(self) -> str:
        return self.dst_internal.split("/")[-1]


def _is_valid_java_ident(name: str) -> bool:
    if not name:
        return False
    if name in JAVA_KEYWORDS:
        return False
    if name[0].isdigit():
        return False
    return re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) is not None


def _load_tsv(tsv: Path) -> list[RenameCandidate]:
    rows: list[RenameCandidate] = []
    with tsv.open("r", encoding="utf-8") as f:
        header = f.readline()
        if not header:
            raise RuntimeError(f"empty TSV: {tsv}")
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            src = parts[0].strip()
            dst = parts[1].strip()
            try:
                score = float(parts[2])
            except ValueError:
                continue
            anchors = parts[3].strip() if len(parts) > 3 else ""
            rows.append(RenameCandidate(src_internal=src, dst_internal=dst, score=score, anchors=anchors))
    return rows


def _load_csv(csv_path: Path) -> list[RenameCandidate]:
    """
    CSV with at least: src,dst
    Optional: src,dst,score,anchors

    - Ignores blank lines and lines starting with '#'
    - Tolerates extra trailing columns
    """
    rows: list[RenameCandidate] = []
    for raw in csv_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        src = parts[0]
        dst = parts[1]
        if not src or not dst:
            continue
        score = 1.0
        if len(parts) >= 3 and parts[2]:
            try:
                score = float(parts[2])
            except ValueError:
                score = 1.0
        anchors = parts[3] if len(parts) >= 4 else ""
        rows.append(RenameCandidate(src_internal=src, dst_internal=dst, score=score, anchors=anchors))
    return rows


def _load_manifest(manifest: Path) -> list[RenameCandidate]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if isinstance(data, list):
        renames = data
    elif isinstance(data, dict) and "renames" in data and isinstance(data["renames"], list):
        renames = data["renames"]
    else:
        raise RuntimeError(f"Unrecognized manifest format: {manifest}")

    out: list[RenameCandidate] = []
    for r in renames:
        if not isinstance(r, dict):
            continue
        src = str(r.get("src") or r.get("src_internal") or "").strip()
        dst = str(r.get("dst") or r.get("dst_internal") or "").strip()
        if not src or not dst:
            continue
        score = r.get("score")
        try:
            score_f = float(score) if score is not None else 1.0
        except Exception:
            score_f = 1.0
        anchors = r.get("anchors") or r.get("anchor_strings") or ""
        if isinstance(anchors, list):
            anchors_s = ",".join(str(x) for x in anchors)
        else:
            anchors_s = str(anchors)
        out.append(RenameCandidate(src_internal=src, dst_internal=dst, score=score_f, anchors=anchors_s))
    return out


def _collect_java_files(src_dir: Path) -> dict[str, Path]:
    return {p.stem: p for p in src_dir.glob("*.java")}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _is_ident_start(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ch in ("_", "$")


def _is_ident_part(ch: str) -> bool:
    return _is_ident_start(ch) or ("0" <= ch <= "9")


def _parse_anchors(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if p.lower() == "null":
            continue
        out.append(p)
    return out


def _apply_rename_map_java(text: str, rename_map: dict[str, str]) -> str:
    """
    Apply identifier renames, but only in Java code tokens.

    Skips:
    - // line comments
    - /* block comments */
    - "string literals"
    - 'char literals'
    """
    if not rename_map:
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    state = "NORMAL"

    while i < n:
        ch = text[i]

        if state == "NORMAL":
            if ch == "/" and i + 1 < n:
                nxt = text[i + 1]
                if nxt == "/":
                    out.append("//")
                    i += 2
                    state = "SL_COMMENT"
                    continue
                if nxt == "*":
                    out.append("/*")
                    i += 2
                    state = "ML_COMMENT"
                    continue
            if ch == '"':
                out.append(ch)
                i += 1
                state = "STRING"
                continue
            if ch == "'":
                out.append(ch)
                i += 1
                state = "CHAR"
                continue
            if _is_ident_start(ch):
                j = i + 1
                while j < n and _is_ident_part(text[j]):
                    j += 1
                ident = text[i:j]
                out.append(rename_map.get(ident, ident))
                i = j
                continue

            out.append(ch)
            i += 1
            continue

        if state == "SL_COMMENT":
            out.append(ch)
            i += 1
            if ch == "\n":
                state = "NORMAL"
            continue

        if state == "ML_COMMENT":
            if ch == "*" and i + 1 < n and text[i + 1] == "/":
                out.append("*/")
                i += 2
                state = "NORMAL"
                continue
            out.append(ch)
            i += 1
            continue

        if state == "STRING":
            out.append(ch)
            i += 1
            if ch == "\\" and i < n:
                out.append(text[i])
                i += 1
                continue
            if ch == '"':
                state = "NORMAL"
            continue

        if state == "CHAR":
            out.append(ch)
            i += 1
            if ch == "\\" and i < n:
                out.append(text[i])
                i += 1
                continue
            if ch == "'":
                state = "NORMAL"
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _self_test() -> None:
    sample = (
        "/* Class170 in block comment */\n"
        "// Class170 in line comment\n"
        "final class Class170 {\n"
        "  void f() {\n"
        "    String s = \"Class170 in string\";\n"
        "    char c = '\\'';\n"
        "    Class170 x = new Class170();\n"
        "  }\n"
        "}\n"
    )
    out = _apply_rename_map_java(sample, {"Class170": "VarpDomain"})
    if "/* VarpDomain in block comment */" in out or "// VarpDomain in line comment" in out:
        raise RuntimeError("self-test failed: renamed inside comment")
    if "\"VarpDomain in string\"" in out:
        raise RuntimeError("self-test failed: renamed inside string literal")
    if "final class VarpDomain" not in out or "VarpDomain x = new VarpDomain()" not in out:
        raise RuntimeError("self-test failed: did not rename code identifiers")


@dataclasses.dataclass
class ApplyReport:
    renamed: list[tuple[str, str, float, str]]
    skipped: list[tuple[str, str, float, str]]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rename report\n")
        lines.append(f"Renamed: {len(self.renamed)}  \nSkipped: {len(self.skipped)}\n")
        if self.renamed:
            lines.append("## Renamed\n")
            lines.append("| src | dst | score | anchors |")
            lines.append("|---|---:|---:|---|")
            for src, dst, score, anchors in self.renamed:
                lines.append(f"| `{src}` | `{dst}` | {score:.6f} | {anchors.replace('|', '\\\\|')} |")
            lines.append("")
        if self.skipped:
            lines.append("## Skipped\n")
            lines.append("| src | dst | score | reason |")
            lines.append("|---|---:|---:|---|")
            for src, dst, score, reason in self.skipped:
                lines.append(f"| `{src}` | `{dst}` | {score:.6f} | {reason.replace('|', '\\\\|')} |")
            lines.append("")
        return "\n".join(lines)


def _git_available() -> bool:
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True, text=True)
        return True
    except Exception:
        return False


def _git_mv(src: Path, dst: Path) -> None:
    subprocess.run(["git", "mv", str(src), str(dst)], check=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Apply class renames to flat client/src/*.java")
    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--csv", type=Path, help="CSV mapping (src,dst[,score,anchors])")
    src_group.add_argument("--tsv", type=Path, help="TSV from a mapper (best matches)")
    src_group.add_argument("--manifest", type=Path, help="JSON manifest of class renames to apply")
    ap.add_argument("--src-dir", required=True, type=Path, help="Directory containing *.java (flat/default package)")
    ap.add_argument("--min-score", type=float, default=0.90, help="Minimum score for TSV/manifest rows")
    ap.add_argument(
        "--require-anchors",
        action="store_true",
        help="Only accept TSV/manifest rows that have at least one useful anchor string (skips empty/'null')",
    )
    ap.add_argument("--max-renames", type=int, default=0, help="If >0, cap how many renames to apply this run")
    ap.add_argument("--self-test", action="store_true", help="Run a quick internal sanity test and exit")
    ap.add_argument("--dry-run", action="store_true", help="Compute mapping and report, but don't modify files")
    ap.add_argument("--report", required=True, type=Path, help="Markdown report output path")
    ap.add_argument("--write-manifest", type=Path, help="Write selected renames to a JSON manifest")
    ap.add_argument(
        "--no-git-mv",
        action="store_true",
        help="Use filesystem renames instead of `git mv` (default: use `git mv` when available).",
    )
    args = ap.parse_args(argv)

    if args.self_test:
        _self_test()
        print("OK")
        return 0

    src_dir = args.src_dir
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")

    java_files = _collect_java_files(src_dir)
    existing_names = set(java_files.keys())

    if args.csv:
        renames = _load_csv(args.csv)
    elif args.tsv:
        renames = _load_tsv(args.tsv)
    else:
        renames = _load_manifest(args.manifest)

    report = ApplyReport(renamed=[], skipped=[])

    # Filter / validate
    chosen: list[RenameCandidate] = []
    used_dst: set[str] = set()
    for r in renames:
        if r.score < args.min_score:
            report.skipped.append((r.src_simple, r.dst_simple, r.score, "score below --min-score"))
            continue

        anchors = _parse_anchors(r.anchors)
        if args.require_anchors and not anchors:
            report.skipped.append((r.src_simple, r.dst_simple, r.score, "missing anchors (--require-anchors)"))
            continue

        src = r.src_simple
        dst = r.dst_simple

        if src == dst:
            report.skipped.append((src, dst, r.score, "dst equals src (no-op)"))
            continue
        if src not in existing_names:
            # Treat this as "already applied" if dst exists; otherwise skip.
            if dst in existing_names:
                report.skipped.append((src, dst, r.score, "src missing (already renamed?)"))
            else:
                report.skipped.append((src, dst, r.score, "src .java not found"))
            continue
        if not _is_valid_java_ident(dst):
            report.skipped.append((src, dst, r.score, "dst is not a valid Java identifier"))
            continue
        if dst in used_dst:
            report.skipped.append((src, dst, r.score, "dst already used by another mapping"))
            continue
        if dst in existing_names:
            report.skipped.append((src, dst, r.score, "dst name already exists in source tree"))
            continue

        used_dst.add(dst)
        chosen.append(r)
        if args.max_renames and len(chosen) >= args.max_renames:
            break

    if args.write_manifest:
        payload = {
            "version": 1,
            "src_dir": str(src_dir),
            "min_score": args.min_score,
            "source_csv": str(args.csv) if args.csv else None,
            "source_tsv": str(args.tsv) if args.tsv else None,
            "source_manifest": str(args.manifest) if args.manifest else None,
            "renames": [
                {
                    "src": r.src_simple,
                    "dst": r.dst_simple,
                    "score": r.score,
                    "anchors": _parse_anchors(r.anchors),
                }
                for r in chosen
            ],
        }
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    if args.dry_run:
        for r in chosen:
            report.renamed.append((r.src_simple, r.dst_simple, r.score, r.anchors))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report.to_markdown(), encoding="utf-8")
        print(f"Dry-run: would rename {len(chosen)} classes. Wrote {args.report}")
        return 0

    # Load all texts once
    texts: dict[Path, str] = {p: _read_text(p) for p in java_files.values()}
    rename_map = {r.src_simple: r.dst_simple for r in chosen}

    # Replace identifiers token-aware (skip comments/strings)
    for p in list(texts.keys()):
        texts[p] = _apply_rename_map_java(texts[p], rename_map)

    # Write updates to existing paths
    for p, txt in texts.items():
        _write_text(p, txt)

    # File renames (after content updates)
    renames_fs: list[tuple[Path, Path, RenameCandidate]] = []
    for r in chosen:
        src_name = r.src_simple
        dst_name = r.dst_simple
        src_path = java_files[src_name]
        dst_path = src_path.with_name(dst_name + ".java")
        renames_fs.append((src_path, dst_path, r))

    use_git_mv = (not args.no_git_mv) and _git_available()

    # Two-phase rename to avoid collisions.
    tmp_suffix = ".__renametmp__"
    tmp_moves: list[tuple[Path, Path, Path, RenameCandidate]] = []
    for src_path, dst_path, r in renames_fs:
        if dst_path.exists():
            report.skipped.append((src_path.stem, dst_path.stem, r.score, "dst file already exists"))
            continue
        tmp_path = src_path.with_suffix(src_path.suffix + tmp_suffix)
        if use_git_mv:
            _git_mv(src_path, tmp_path)
        else:
            src_path.rename(tmp_path)
        tmp_moves.append((src_path, tmp_path, dst_path, r))

    for _src_path, tmp_path, dst_path, r in tmp_moves:
        if use_git_mv:
            _git_mv(tmp_path, dst_path)
        else:
            tmp_path.rename(dst_path)
        report.renamed.append((r.src_simple, r.dst_simple, r.score, r.anchors))

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.to_markdown(), encoding="utf-8")
    print(f"Renamed {len(report.renamed)} classes. Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
