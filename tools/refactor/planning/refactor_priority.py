#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path

from tools.refactor.common.paths import resolve_repo_root
from tools.reporting import fan_graph


OBF_IDENT_RE = re.compile(
    r"\b("
    r"anInt\d+|anLong\d+|anIntArray\d+|anIntArrayArray\d+|anIntArrayArrayArray\d+|"
    r"aByte\d+|aByteArray\d+|aByteArrayArray\d+|aByteArrayArrayArray\d+|"
    r"aShort\d+|aShortArray\d+|aFloat\d+|aFloatArray\d+|aBoolean\d+|aString\d+|"
    r"aClass\d+(?:_\d+)?|aClass\d+_Sub\d+|method\d+|"
    r"i_\d+_|bool_\d+_|string_\d+_"
    r")\b"
)


@dataclass(frozen=True)
class PriorityRow:
    class_name: str
    rel_path: str
    module: str
    module_rank: int
    fan_in: int
    fan_out: int
    mut_statics: int
    bytes: int
    lines: int
    obf_hits: int
    score: float


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _obf_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = fan_graph.strip_java(fan_graph.remove_package_and_imports(text))
    return len(OBF_IDENT_RE.findall(stripped))


def _load_status_map(previous: Path) -> dict[str, str]:
    if not previous.exists():
        return {}
    status: dict[str, str] = {}
    for line in previous.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"- \[(?P<mark>[ xX])\] `(?P<path>[^`]+)`", line.strip())
        if not m:
            continue
        mark = m.group("mark").strip().lower()
        status[m.group("path")] = "done" if mark == "x" else "todo"
    return status


def _score(*, fan_in: int, fan_out: int, mut_statics: int, obf_hits: int, size_bytes: int) -> float:
    return (
        fan_in * 10.0
        + fan_out * 4.0
        + mut_statics * 25.0
        + obf_hits * 1.5
        + min(60.0, size_bytes / 2000.0)
    )


def _find_module(rel_path: str, module_prefixes: list[str]) -> tuple[str, int]:
    for idx, prefix in enumerate(module_prefixes):
        if rel_path.startswith(prefix):
            return prefix, idx
    return "other", len(module_prefixes)


def _is_excluded(rel_path: str, exclude_prefixes: list[str]) -> bool:
    for prefix in exclude_prefixes:
        if rel_path.startswith(prefix):
            return True
    return False


def build_priority_rows(
    repo_root: Path,
    src_root: Path,
    *,
    module_prefixes: list[str],
    exclude_prefixes: list[str],
) -> list[PriorityRow]:
    java_files = fan_graph.gather_java_files(src_root)
    class_names = {p.stem for p in java_files}
    infos = fan_graph.build_infos(java_files, all_class_names=class_names)
    info_by_name = {i.class_name: i for i in infos}

    fan_in: dict[str, int] = {}
    for i in infos:
        fan_in.setdefault(i.class_name, 0)
    for info in infos:
        for dst in info.ref_classes:
            if dst in info_by_name:
                fan_in[dst] = fan_in.get(dst, 0) + 1

    rows: list[PriorityRow] = []
    for info in infos:
        rel_path = _rel(info.path, repo_root)
        if _is_excluded(rel_path, exclude_prefixes):
            continue
        module, module_rank = _find_module(rel_path, module_prefixes)
        obf_hits = _obf_count(info.path)
        score = _score(
            fan_in=fan_in.get(info.class_name, 0),
            fan_out=len(info.ref_classes),
            mut_statics=info.static_mutable_fields,
            obf_hits=obf_hits,
            size_bytes=info.bytes,
        )
        rows.append(
            PriorityRow(
                class_name=info.class_name,
                rel_path=rel_path,
                module=module,
                module_rank=module_rank,
                fan_in=fan_in.get(info.class_name, 0),
                fan_out=len(info.ref_classes),
                mut_statics=info.static_mutable_fields,
                bytes=info.bytes,
                lines=info.lines,
                obf_hits=obf_hits,
                score=score,
            )
        )
    rows.sort(
        key=lambda r: (r.module_rank, -r.score, -r.fan_in, -r.fan_out, -r.obf_hits, -r.bytes, r.class_name),
    )
    return rows


def render_markdown(
    *,
    rows: list[PriorityRow],
    generated: dt.date,
    repo_root: Path,
    src_root: Path,
    top_n: int,
    queue_n: int,
    status_map: dict[str, str],
) -> str:
    out: list[str] = []
    out += [
        "# Refactor Priority Board (void-client)",
        "",
        f"Generated: {generated.isoformat()}",
        "",
        "Deterministic rename priority for `client/refactor`, ordered by impact + remaining obfuscation.",
        "",
        "## Scoring",
        "",
        "- `score = fan_in*10 + fan_out*4 + mut_statics*25 + obf_hits*1.5 + size_bonus`",
        "- Module ordering is applied first; score orders files within each module.",
        "- `obf_hits` counts remaining identifiers matching obfuscated patterns (`anInt###`, `aClass###`, `method###`, etc.).",
        "- Use this order for `fields -> methods -> params -> locals` per file.",
        "",
        "## Regenerate",
        "",
        f"- `python tools/refactor/planning/refactor_priority.py --root {_rel(src_root, repo_root)} --write docs/refactor-priority.md --top {top_n} --queue {queue_n}`",
        "",
        "## Ordered Queue",
        "",
        "rank | module | score | fan_in | fan_out | mut_statics | obf_hits | bytes | lines | class | path",
        "---:|---|---:|---:|---:|---:|---:|---:|---:|---|---",
    ]
    for idx, row in enumerate(rows[:top_n], start=1):
        out.append(
            f"{idx} | `{row.module}` | {row.score:.1f} | {row.fan_in} | {row.fan_out} | {row.mut_statics} | {row.obf_hits} | {row.bytes} | {row.lines} | `{row.class_name}` | `{row.rel_path}`"
        )
    out += ["", "## Active Queue Checklist", ""]
    for row in rows[:queue_n]:
        status = status_map.get(row.rel_path, "todo")
        mark = "x" if status == "done" else " "
        out.append(f"- [{mark}] `{row.rel_path}`")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic ordered refactor queue for client/refactor.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=resolve_repo_root(None, __file__, levels_up=3),
        help="Repository root (default: inferred from this script location).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("client/refactor"),
        help="Java source root relative to repo root.",
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=Path("docs/refactor-priority.md"),
        help="Output markdown path (relative to repo root allowed).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=120,
        help="Rows to render in ordered table.",
    )
    parser.add_argument(
        "--queue",
        type=int,
        default=40,
        help="Rows to render in checklist.",
    )
    parser.add_argument(
        "--module-order",
        action="append",
        default=[],
        help=(
            "Module path prefix priority (repeatable). "
            "If omitted, defaults to core-first modules."
        ),
    )
    parser.add_argument(
        "--exclude-prefix",
        action="append",
        default=[],
        help=(
            "Exclude path prefixes from queue (repeatable). "
            "Defaults to client/refactor/script/."
        ),
    )
    parser.add_argument(
        "--include-script-module",
        action="store_true",
        help="Include client/refactor/script/ in ranking.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    src_root = args.root if args.root.is_absolute() else (repo_root / args.root).resolve()
    out_path = args.write if args.write.is_absolute() else (repo_root / args.write).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    status_map = _load_status_map(out_path)
    default_module_order = [
        "client/refactor/collections/",
        "client/refactor/cache/",
        "client/refactor/io/",
        "client/refactor/net/",
        "client/refactor/config/",
        "client/refactor/game/",
        "client/refactor/world/",
        "client/refactor/graphics/",
        "client/refactor/audio/",
        "client/refactor/ui/",
        "client/refactor/client/",
        "client/refactor/math/",
        "client/refactor/platform/",
        "client/refactor/util/",
        "client/refactor/media/",
        "client/refactor/crypto/",
        "client/refactor/timing/",
    ]
    module_order = args.module_order if args.module_order else default_module_order
    exclude_prefixes = args.exclude_prefix if args.exclude_prefix else []
    if not args.include_script_module and not exclude_prefixes:
        exclude_prefixes = ["client/refactor/script/"]

    rows = build_priority_rows(
        repo_root,
        src_root,
        module_prefixes=module_order,
        exclude_prefixes=exclude_prefixes,
    )
    report = render_markdown(
        rows=rows,
        generated=dt.date.today(),
        repo_root=repo_root,
        src_root=src_root,
        top_n=max(1, args.top),
        queue_n=max(1, args.queue),
        status_map=status_map,
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote {os.path.relpath(out_path, repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
