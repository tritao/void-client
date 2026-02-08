#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import dataclasses
import re
from pathlib import Path

from tools.refactor.common.ts_java import build_java_parser
from tools.refactor.extract.static_members import parse_static_members


CLASS_REF_RX = re.compile(r"\b[A-Z][A-Za-z0-9_]*\b")


@dataclasses.dataclass(frozen=True)
class Candidate:
    source_file: Path
    source_class: str
    kind: str
    name: str
    refs_total: int
    best_module: str
    best_hits: int
    score: float
    top_modules: tuple[tuple[str, int], ...]


def build_class_module_index(src_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for java in src_dir.rglob("*.java"):
        rel = java.relative_to(src_dir)
        parts = rel.parts
        module = parts[0] if len(parts) > 1 else "_root"
        out[java.stem] = module
    return out


def infer_candidate(member_text: str, class_index: dict[str, str], *, exclude_module: str) -> tuple[int, str, int, tuple[tuple[str, int], ...], float]:
    counts: collections.Counter[str] = collections.Counter()
    total = 0
    for ref in CLASS_REF_RX.findall(member_text):
        module = class_index.get(ref)
        if not module:
            continue
        total += 1
        if module != exclude_module:
            counts[module] += 1
    if not counts:
        return total, "", 0, tuple(), 0.0
    top = counts.most_common(3)
    best_module, best_hits = top[0]
    score = best_hits / max(1, total)
    return total, best_module, best_hits, tuple(top), score


def write_markdown(path: Path, candidates: list[Candidate]) -> None:
    lines: list[str] = []
    lines.append("# Static split candidates")
    lines.append("")
    lines.append("| source | kind | member | suggested module | score | refs | top modules |")
    lines.append("|---|---|---|---|---:|---:|---|")
    for c in candidates:
        top = ", ".join(f"{m}:{n}" for m, n in c.top_modules) if c.top_modules else "-"
        lines.append(
            f"| `{c.source_file}` | {c.kind} | `{c.name}` | `{c.best_module or '-'}` | {c.score:.2f} | {c.refs_total} | {top} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(path: Path, candidates: list[Candidate]) -> None:
    lines = ["source_file,source_class,kind,name,suggested_module,score,refs_total,target_class_hint"]
    for c in candidates:
        target_hint = f"{c.source_class}{c.best_module.capitalize()}Statics" if c.best_module else f"{c.source_class}Statics"
        lines.append(
            f"{c.source_file},{c.source_class},{c.kind},{c.name},{c.best_module},{c.score:.3f},{c.refs_total},{target_hint}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rank static members in collections by module affinity for extraction.")
    ap.add_argument("--src-dir", type=Path, default=Path("client/refactor"))
    ap.add_argument("--scope-dir", type=Path, default=Path("client/refactor/collections"))
    ap.add_argument("--out-md", type=Path, default=Path("docs/static-split-candidates.md"))
    ap.add_argument("--out-csv", type=Path, default=Path("docs/static-split-candidates.csv"))
    ap.add_argument("--min-score", type=float, default=0.55)
    ap.add_argument("--min-refs", type=int, default=4)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--language-so", type=Path, default=Path("build/ts-languages-java.so"))
    args = ap.parse_args()

    src_dir = args.src_dir.resolve()
    scope_dir = args.scope_dir.resolve()
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")
    if not scope_dir.exists():
        raise SystemExit(f"--scope-dir not found: {scope_dir}")

    parser = build_java_parser(out_so=args.language_so)
    class_index = build_class_module_index(src_dir)

    candidates: list[Candidate] = []
    for java in sorted(scope_dir.rglob("*.java")):
        source_class = java.stem
        for member in parse_static_members(java, parser=parser):
            refs_total, best_module, best_hits, top_modules, score = infer_candidate(
                member.text,
                class_index,
                exclude_module="collections",
            )
            if refs_total < args.min_refs or score < args.min_score:
                continue
            candidates.append(
                Candidate(
                    source_file=java.relative_to(src_dir),
                    source_class=source_class,
                    kind=member.kind,
                    name=member.name,
                    refs_total=refs_total,
                    best_module=best_module,
                    best_hits=best_hits,
                    score=score,
                    top_modules=top_modules,
                )
            )

    candidates.sort(key=lambda c: (-c.score, -c.refs_total, str(c.source_file), c.name))
    if args.limit > 0:
        candidates = candidates[: args.limit]
    write_markdown(args.out_md, candidates)
    write_csv(args.out_csv, candidates)
    print(f"Wrote {len(candidates)} candidates to {args.out_md} and {args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
