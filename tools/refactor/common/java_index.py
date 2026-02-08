from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path


METHOD_NAME_RX = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
FIELD_RX_TEMPLATE = r"\b{field}\b"


def extract_declared_methods(text: str) -> set[str]:
    out: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if "(" not in line or ")" not in line:
            continue
        if line.startswith("//"):
            continue
        has_block = "{" in line
        has_decl_terminator = line.endswith(";")
        if not has_block and not has_decl_terminator:
            continue
        if has_block and ";" in line and line.index(";") < line.index("{"):
            continue
        if " class " in f" {line} " or line.startswith("class "):
            continue
        if line.startswith(("if ", "for ", "while ", "switch ", "catch ", "return ", "new ")):
            continue
        m = METHOD_NAME_RX.search(line)
        if not m:
            continue
        name = m.group(1)
        if name in {"if", "for", "while", "switch", "catch", "return", "new"}:
            continue
        out.add(name)
    return out


class JavaIndex:
    def __init__(self, src_dir: Path, *, load_text: bool = True) -> None:
        self.src_dir = src_dir
        self.by_stem: dict[str, list[Path]] = defaultdict(list)
        self.methods_by_file: dict[Path, set[str]] = {}
        self.text_by_file: dict[Path, str] = {}
        self.method_to_owners: dict[str, set[str]] = defaultdict(set)
        for path in sorted(src_dir.rglob("*.java")):
            rel = path.relative_to(src_dir)
            self.by_stem[path.stem].append(rel)
            text = path.read_text(encoding="utf-8", errors="replace")
            if load_text:
                self.text_by_file[rel] = text
            methods = extract_declared_methods(text)
            self.methods_by_file[rel] = methods
            for method in methods:
                self.method_to_owners[method].add(rel.stem)

    def has_file(self, rel: str) -> bool:
        return Path(rel) in self.methods_by_file

    def owner_files(self, owner: str) -> list[Path]:
        return list(self.by_stem.get(owner, []))

    def owner_has_method(self, owner: str, method: str) -> bool:
        files = self.owner_files(owner)
        if not files:
            return False
        for rel in files:
            if method in self.methods_by_file.get(rel, set()):
                return True
        return False

    def owner_has_field_token(self, owner: str, field_name: str) -> bool:
        files = self.owner_files(owner)
        if not files:
            return False
        rx = re.compile(FIELD_RX_TEMPLATE.format(field=re.escape(field_name)))
        for rel in files:
            text = self.text_by_file.get(rel, "")
            if rx.search(text):
                return True
        return False

    def file_has_method(self, rel: str, method: str) -> bool:
        return method in self.methods_by_file.get(Path(rel), set())

    def unique_owner_for_method(self, method: str) -> str:
        owners = self.method_to_owners.get(method, set())
        if len(owners) == 1:
            return next(iter(owners))
        return ""
