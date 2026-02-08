from __future__ import annotations

import dataclasses
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class Mapping:
    old: str
    new: str
    kind: str = ""
    owner: str = ""
    member: str = ""
    file: str = ""
    signature: str = ""
    notes: str = ""


@dataclasses.dataclass(frozen=True)
class PreparedMapping:
    old: str
    new: str
    kind: str
    owner: str
    member: str
    file: str
    signature: str
    signature_types: tuple[str, ...] | None
    notes: str
    scope_files: frozenset


@dataclasses.dataclass(frozen=True)
class ClassIndex:
    fields: dict[str, dict[str, str]]
    extends_of: dict[str, str]
    methods: dict[str, dict[str, str]]
    implements_of: dict[str, list[str]]


@dataclasses.dataclass(frozen=True)
class TypeEnv:
    names: dict[str, str]
    local_names: frozenset[str]
    current_class: str


@dataclasses.dataclass(frozen=True)
class CachedIndexes:
    field_decls: dict[str, set[Path]]
    class_index: ClassIndex


@dataclasses.dataclass(frozen=True)
class MethodRename:
    signature_types: tuple[str, ...] | None
    new: str


@dataclasses.dataclass
class Report:
    renamed_files: dict[str, int]
    renamed_total: int
    skipped: list[str]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rename report (identifiers via tree-sitter)")
        lines.append("")
        lines.append(f"Edited files: {len(self.renamed_files)}  ")
        lines.append(f"Renamed identifiers: {self.renamed_total}")
        lines.append("")
        lines.append("## Files")
        lines.append("")
        lines.append("| file | edits |")
        lines.append("|---|---:|")
        for f, n in sorted(self.renamed_files.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| `{f}` | {n} |")
        lines.append("")
        if self.skipped:
            lines.append("## Skipped")
            lines.append("")
            for s in self.skipped:
                lines.append(f"- {s}")
            lines.append("")
        return "\n".join(lines)

    def to_summary_json(self) -> dict:
        def _reason(s: str) -> str:
            if ":" in s:
                return s.split(":", 1)[0].strip()
            return s.split("(", 1)[0].strip()

        reasons: dict[str, int] = {}
        for s in self.skipped:
            r = _reason(s)
            reasons[r] = reasons.get(r, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        return {
            "edited_files": len(self.renamed_files),
            "renamed_identifiers": self.renamed_total,
            "skipped": len(self.skipped),
            "skipped_reasons": {k: v for k, v in top},
        }
