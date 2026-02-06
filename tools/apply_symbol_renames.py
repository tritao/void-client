#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import re
import select
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

"""
Apply Java symbol renames (methods/fields/params/locals) via JDTLS (LSP).

This is the method/variable counterpart to tools/apply_jdtls_renames.py, but it
uses an explicit mapping file (client/refactor/symbol_renames.csv) so we can
rename *specific* symbols without guessing.
"""


# ---------------------------
# CSV parsing / report
# ---------------------------


@dataclasses.dataclass(frozen=True)
class SymbolRename:
    file: str
    kind: str
    old: str
    new: str
    owner: str = ""
    member: str = ""
    signature: str = ""
    param_index: int | None = None  # 0-based
    detail_regex: str = ""
    line: int | None = None  # 1-based
    col: int | None = None  # 1-based
    notes: str = ""


@dataclasses.dataclass
class Report:
    renamed: list[tuple[SymbolRename, str]]
    skipped: list[tuple[SymbolRename, str]]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rename report (symbols via JDTLS / LSP)")
        lines.append("")
        lines.append(f"Renamed: {len(self.renamed)}  ")
        lines.append(f"Skipped: {len(self.skipped)}")
        lines.append("")
        lines.append("## Renamed")
        lines.append("")
        lines.append("| file | kind | old | new | note |")
        lines.append("|---|---|---|---|---|")
        for r, note in self.renamed:
            lines.append(f"| `{r.file}` | `{r.kind}` | `{r.old}` | `{r.new}` | {note} |")
        lines.append("")
        lines.append("## Skipped")
        lines.append("")
        lines.append("| file | kind | old | new | reason |")
        lines.append("|---|---|---|---|---|")
        for r, reason in self.skipped:
            lines.append(f"| `{r.file}` | `{r.kind}` | `{r.old}` | `{r.new}` | {reason} |")
        lines.append("")
        return "\n".join(lines)


def _as_int(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def load_symbol_csv(csv_path: Path) -> list[SymbolRename]:
    return _load_symbol_csv_any(csv_path, label=str(csv_path))


def _non_comment_csv_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        lines.append(raw)
    return lines


def _load_symbol_csv_any(path: Path, *, label: str) -> list[SymbolRename]:
    raw_lines = _non_comment_csv_lines(path.read_text(encoding="utf-8", errors="replace"))
    if not raw_lines:
        return []

    reader = csv.DictReader(raw_lines)
    required = {"file", "kind", "old", "new"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise SystemExit(f"{label} must have header with: {sorted(required)} (got: {reader.fieldnames})")

    out: list[SymbolRename] = []
    for row in reader:
        file_ = (row.get("file") or "").strip()
        kind = (row.get("kind") or "").strip()
        old = (row.get("old") or "").strip()
        new = (row.get("new") or "").strip()
        if not file_ or not kind or not old or not new:
            continue

        # Backward compat: earlier drafts used `container` for the owning type.
        owner = (row.get("owner") or row.get("container") or "").strip()
        member = (row.get("member") or "").strip()
        signature = (row.get("signature") or "").strip()
        param_index = _as_int(row.get("param_index") or "")

        out.append(
            SymbolRename(
                file=file_,
                kind=kind,
                old=old,
                new=new,
                owner=owner,
                member=member,
                signature=signature,
                param_index=param_index,
                detail_regex=(row.get("detail_regex") or "").strip(),
                line=_as_int(row.get("line") or ""),
                col=_as_int(row.get("col") or ""),
                notes=(row.get("notes") or "").strip(),
            )
        )
    return out


def load_symbol_csvs(*, csv_files: list[Path], csv_dir: Path | None) -> list[SymbolRename]:
    out: list[SymbolRename] = []
    for p in csv_files:
        if not p.exists():
            continue
        out.extend(_load_symbol_csv_any(p, label=str(p)))

    if csv_dir and csv_dir.exists():
        for p in sorted([x for x in csv_dir.rglob("*.csv") if x.is_file()]):
            out.extend(_load_symbol_csv_any(p, label=str(p)))

    # Drop exact duplicates while preserving order.
    seen: set[tuple[Any, ...]] = set()
    deduped: list[SymbolRename] = []
    for r in out:
        key = (
            r.file,
            r.kind,
            r.owner,
            r.member,
            r.signature,
            r.param_index,
            r.old,
            r.new,
            r.detail_regex,
            r.line,
            r.col,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _resolve_java_file(file_s: str, *, root: Path, src_dir: Path, classes_csv: Path) -> Path | None:
    """
    Resolve a mapping row's `file` value to an on-disk .java file.

    Accept:
    - explicit repo-relative paths (contain '/')
    - src stems under --src-dir (e.g. Class348_Sub49 or Class348_Sub49.java)
    - refactored type names (e.g. JagBuffer or JagBuffer.java), resolved via classes.csv
    """
    s = (file_s or "").strip()
    if not s:
        return None

    if "/" in s:
        p = (root / s).resolve()
        return p if p.exists() else None

    stem = s[:-5] if s.endswith(".java") else s
    p = (src_dir / f"{stem}.java").resolve()
    if p.exists():
        return p

    cc = classes_csv if classes_csv.is_absolute() else (root / classes_csv)
    if not cc.exists():
        return None

    src_matches: list[str] = []
    for raw in cc.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 2:
            continue
        src, dst = parts[0], parts[1]
        if dst == stem:
            src_matches.append(src)

    if len(src_matches) != 1:
        return None
    p2 = (src_dir / f"{src_matches[0]}.java").resolve()
    return p2 if p2.exists() else None


# ---------------------------
# Offsets / positions
# ---------------------------


def _offset_to_lsp_position(text: str, offset: int) -> dict[str, int]:
    if offset < 0:
        offset = 0
    if offset > len(text):
        offset = len(text)
    line = text.count("\n", 0, offset)
    last_nl = text.rfind("\n", 0, offset)
    ch = offset if last_nl == -1 else (offset - last_nl - 1)
    return {"line": line, "character": ch}


def _line_offsets(text: str) -> list[int]:
    offs = [0]
    for idx, ch in enumerate(text):
        if ch == "\n":
            offs.append(idx + 1)
    return offs


def _pos_to_offset(line_offsets: list[int], pos: dict[str, int], *, text_len: int) -> int:
    line = int(pos.get("line", 0))
    ch = int(pos.get("character", 0))
    if line < 0:
        line = 0
    if line >= len(line_offsets):
        return min(line_offsets[-1], text_len)
    line_start = line_offsets[line]
    line_end = line_offsets[line + 1] if line + 1 < len(line_offsets) else text_len
    return max(line_start, min(line_start + ch, line_end))


def _pos_1based_to_lsp(line_1: int, col_1: int) -> dict[str, int]:
    # LSP is 0-based.
    return {"line": max(0, line_1 - 1), "character": max(0, col_1 - 1)}


# ---------------------------
# LSP client
# ---------------------------


class LspClient:
    def __init__(self, proc: subprocess.Popen[bytes], *, timeout_s: float = 60.0) -> None:
        self.proc = proc
        self.timeout_s = timeout_s
        self._next_id = 1

    def _read_exactly(self, n: int, *, timeout_s: float) -> bytes:
        buf = b""
        start = time.monotonic()
        while len(buf) < n:
            if self.proc.stdout is None:
                raise RuntimeError("missing stdout pipe")
            remaining = timeout_s - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("timeout reading LSP body")
            r, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not r:
                continue
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError("LSP stdout closed")
            buf += chunk
        return buf

    def _read_headers(self, *, timeout_s: float) -> dict[str, str]:
        if self.proc.stdout is None:
            raise RuntimeError("missing stdout pipe")
        start = time.monotonic()
        raw = b""
        while b"\r\n\r\n" not in raw:
            remaining = timeout_s - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("timeout reading LSP headers")
            r, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not r:
                continue
            ch = self.proc.stdout.read(1)
            if not ch:
                raise RuntimeError("LSP stdout closed")
            raw += ch
            if len(raw) > 64 * 1024:
                raise RuntimeError("LSP headers too large")
        head, _sep, _rest = raw.partition(b"\r\n\r\n")
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n"):
            if b":" not in line:
                continue
            k, v = line.split(b":", 1)
            headers[k.decode("ascii", errors="replace").strip().lower()] = v.decode("ascii", errors="replace").strip()
        return headers

    def read_message(self) -> dict[str, Any]:
        headers = self._read_headers(timeout_s=self.timeout_s)
        length_s = headers.get("content-length")
        if not length_s:
            raise RuntimeError("missing Content-Length header from LSP server")
        length = int(length_s)
        body = self._read_exactly(length, timeout_s=self.timeout_s)
        return json.loads(body.decode("utf-8"))

    def send(self, payload: dict[str, Any]) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("missing stdin pipe")
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        self.proc.stdin.write(header + data)
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})

        while True:
            msg = self.read_message()
            if msg.get("id") != req_id:
                continue
            if msg.get("error"):
                raise RuntimeError(f"LSP error for {method}: {msg['error']}")
            return msg.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported URI scheme: {uri}")
    return Path(unquote(parsed.path))


# ---------------------------
# WorkspaceEdit application
# ---------------------------


def _git_available() -> bool:
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _git_mv(src: Path, dst: Path) -> None:
    subprocess.run(["git", "mv", str(src), str(dst)], check=True)


def _apply_text_edits(path: Path, edits: list[dict[str, Any]], *, dry_run: bool) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    line_offsets = _line_offsets(text)

    def key(e: dict[str, Any]) -> tuple[int, int, int, int]:
        r = e.get("range") or {}
        s = r.get("start") or {}
        en = r.get("end") or {}
        return (int(s.get("line", 0)), int(s.get("character", 0)), int(en.get("line", 0)), int(en.get("character", 0)))

    for e in sorted(edits, key=key, reverse=True):
        r = e.get("range") or {}
        s = r.get("start") or {}
        en = r.get("end") or {}
        start_off = _pos_to_offset(line_offsets, s, text_len=len(text))
        end_off = _pos_to_offset(line_offsets, en, text_len=len(text))
        new_text = e.get("newText") or ""
        text = text[:start_off] + new_text + text[end_off:]
        line_offsets = _line_offsets(text)

    if not dry_run:
        path.write_text(text, encoding="utf-8")


def _apply_workspace_edit(edit: dict[str, Any], *, dry_run: bool) -> None:
    if not edit:
        return

    doc_changes = edit.get("documentChanges")
    if isinstance(doc_changes, list):
        for ch in doc_changes:
            if not isinstance(ch, dict):
                continue
            if ch.get("kind") == "rename":
                old_uri = ch.get("oldUri")
                new_uri = ch.get("newUri")
                if not old_uri or not new_uri:
                    continue
                src = _uri_to_path(old_uri)
                dst = _uri_to_path(new_uri)
                if not dry_run:
                    if _git_available():
                        _git_mv(src, dst)
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        src.rename(dst)
                continue
            if "textDocument" in ch and "edits" in ch:
                uri = (ch.get("textDocument") or {}).get("uri")
                if not uri:
                    continue
                path = _uri_to_path(uri)
                edits = ch.get("edits") or []
                if isinstance(edits, list) and path.exists():
                    _apply_text_edits(path, edits, dry_run=dry_run)
                continue

    changes = edit.get("changes")
    if isinstance(changes, dict):
        for uri, edits_any in changes.items():
            if not isinstance(uri, str):
                continue
            path = _uri_to_path(uri)
            if not path.exists():
                continue
            edits = edits_any if isinstance(edits_any, list) else []
            _apply_text_edits(path, edits, dry_run=dry_run)


# ---------------------------
# JDTLS startup / project bootstrap (copied from apply_jdtls_renames.py)
# ---------------------------


def _detect_jdtls_home() -> Path | None:
    candidates: list[Path] = []
    home = Path.home()
    repo_root = Path(__file__).resolve().parents[1]

    candidates.extend(
        [
            repo_root / ".jdtls",
            home / ".local" / "share" / "jdtls",
            home / ".local" / "share" / "eclipse.jdt.ls",
            home / ".cache" / "jdtls",
            home / ".cache" / "eclipse.jdt.ls",
        ]
    )

    for base in [home / ".vscode" / "extensions", home / ".vscode-server" / "extensions"]:
        if not base.exists():
            continue
        for ext in sorted(base.glob("redhat.java-*")):
            server = ext / "server"
            if server.exists():
                candidates.append(server)

    for c in candidates:
        launcher = list((c / "plugins").glob("org.eclipse.equinox.launcher_*.jar"))
        if launcher:
            return c
    return None


def _jdtls_cmd_from_home(jdtls_home: Path, *, java_cmd: str, data_dir: Path) -> list[str]:
    plugins = jdtls_home / "plugins"
    launcher = sorted(plugins.glob("org.eclipse.equinox.launcher_*.jar"))
    if not launcher:
        raise RuntimeError(f"no JDTLS launcher jar under: {plugins}")
    launcher_jar = launcher[-1]

    platform = sys.platform
    if platform.startswith("linux"):
        config_dir = jdtls_home / "config_linux"
    elif platform == "darwin":
        config_dir = jdtls_home / "config_mac"
    elif platform.startswith("win"):
        config_dir = jdtls_home / "config_win"
    else:
        config_dir = jdtls_home / "config_linux"

    if not config_dir.exists():
        raise RuntimeError(f"missing JDTLS config dir: {config_dir}")

    data_dir.mkdir(parents=True, exist_ok=True)

    return [
        java_cmd,
        "-Declipse.application=org.eclipse.jdt.ls.core.id1",
        "-Dosgi.bundles.defaultStartLevel=4",
        "-Declipse.product=org.eclipse.jdt.ls.core.product",
        "-Dlog.protocol=true",
        "-Dlog.level=INFO",
        "-Xms256m",
        "-Xmx2g",
        "--add-modules=ALL-SYSTEM",
        "--add-opens",
        "java.base/java.util=ALL-UNNAMED",
        "--add-opens",
        "java.base/java.lang=ALL-UNNAMED",
        "-jar",
        str(launcher_jar),
        "-configuration",
        str(config_dir),
        "-data",
        str(data_dir),
    ]


def _ensure_eclipse_project(root: Path, *, src_dir: Path, lib_jars: list[Path], name: str) -> None:
    project_file = root / ".project"
    classpath_file = root / ".classpath"
    settings_dir = root / ".settings"
    settings_dir.mkdir(exist_ok=True)
    prefs_file = settings_dir / "org.eclipse.jdt.core.prefs"

    if not project_file.exists():
        project_file.write_text(
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    "<projectDescription>",
                    f"  <name>{name}</name>",
                    "  <comment></comment>",
                    "  <projects></projects>",
                    "  <buildSpec>",
                    "    <buildCommand>",
                    "      <name>org.eclipse.jdt.core.javabuilder</name>",
                    "      <arguments></arguments>",
                    "    </buildCommand>",
                    "  </buildSpec>",
                    "  <natures>",
                    "    <nature>org.eclipse.jdt.core.javanature</nature>",
                    "  </natures>",
                    "</projectDescription>",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    if not classpath_file.exists():
        cp_lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<classpath>"]
        cp_lines.append(f'  <classpathentry kind="src" path="{src_dir.as_posix()}"/>')
        for jar in lib_jars:
            if jar.exists():
                cp_lines.append(f'  <classpathentry kind="lib" path="{jar.as_posix()}"/>')
        cp_lines.append(
            '  <classpathentry kind="con" path="org.eclipse.jdt.launching.JRE_CONTAINER/org.eclipse.jdt.internal.debug.ui.launcher.StandardVMType/JavaSE-1.8"/>'
        )
        cp_lines.append('  <classpathentry kind="output" path="build/jdtls-classes"/>')
        cp_lines.append("</classpath>")
        cp_lines.append("")
        classpath_file.write_text("\n".join(cp_lines), encoding="utf-8")

    if not prefs_file.exists():
        prefs_file.write_text(
            "\n".join(
                [
                    "eclipse.preferences.version=1",
                    "org.eclipse.jdt.core.compiler.codegen.targetPlatform=1.8",
                    "org.eclipse.jdt.core.compiler.compliance=1.8",
                    "org.eclipse.jdt.core.compiler.source=1.8",
                    "",
                ]
            ),
            encoding="utf-8",
        )


# ---------------------------
# Symbol location helpers
# ---------------------------


_LSP_KIND = {
    "type": {5},  # Class
    "method": {6},
    "field": {7, 8},  # Property or Field (servers vary)
}


@dataclasses.dataclass(frozen=True)
class _DocSym:
    name: str
    kind: int
    detail: str
    range_start: dict[str, int]
    children: tuple["_DocSym", ...]


def _parse_document_symbols(raw: Any) -> list[_DocSym]:
    if not isinstance(raw, list):
        return []

    def to_sym(obj: dict[str, Any]) -> _DocSym | None:
        if not isinstance(obj, dict):
            return None
        name = str(obj.get("name") or "")
        kind = int(obj.get("kind") or 0)
        detail = str(obj.get("detail") or "")
        rng = obj.get("selectionRange") or obj.get("range") or {}
        start = (rng.get("start") or {}) if isinstance(rng, dict) else {}
        children_raw = obj.get("children") or []
        children: list[_DocSym] = []
        if isinstance(children_raw, list):
            for c in children_raw:
                s = to_sym(c) if isinstance(c, dict) else None
                if s:
                    children.append(s)
        return _DocSym(name=name, kind=kind, detail=detail, range_start=start, children=tuple(children))

    out: list[_DocSym] = []
    for it in raw:
        s = to_sym(it) if isinstance(it, dict) else None
        if s:
            out.append(s)
    return out


def _iter_syms(syms: Iterable[_DocSym]) -> Iterable[_DocSym]:
    for s in syms:
        yield s
        yield from _iter_syms(s.children)


def _find_container_syms(syms: list[_DocSym], container_name: str) -> list[_DocSym]:
    if not container_name:
        return syms
    matches = [s for s in _iter_syms(syms) if s.kind in _LSP_KIND["type"] and s.name == container_name]
    if not matches:
        return syms
    return list(matches[0].children)


def _choose_symbol_start(
    *,
    syms: list[_DocSym],
    kind: str,
    name: str,
    container: str,
    detail_regex: str,
    signature: str,
) -> tuple[dict[str, int] | None, str]:
    k = kind.strip()
    if k not in _LSP_KIND:
        return None, f"kind={kind!r} requires explicit line/col (supported for documentSymbol: {sorted(_LSP_KIND)})"

    space = _find_container_syms(syms, container)
    candidates = [s for s in _iter_syms(space) if s.kind in _LSP_KIND[k] and s.name == name]

    def _norm_type(t: str) -> str:
        t = t.strip()
        if not t:
            return ""
        # Prefer short type names; JDTLS details typically use unqualified names.
        suffix = ""
        while t.endswith("[]"):
            suffix = "[]" + suffix
            t = t[:-2]
        if t.endswith("..."):
            suffix = "..." + suffix
            t = t[:-3]
        base = t.split(".")[-1]
        return (base + suffix).replace(" ", "")

    def _split_sig_types(sig: str) -> list[str] | None:
        s = sig.strip()
        if not s:
            return None
        if "(" in s and ")" in s:
            s = s[s.find("(") + 1 : s.rfind(")")]
        parts: list[str] = []
        cur = ""
        depth = 0
        for ch in s:
            if ch == "<":
                depth += 1
            elif ch == ">" and depth > 0:
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
            else:
                cur += ch
        parts.append(cur.strip())
        return [_norm_type(p) for p in parts if p]

    def _extract_detail_param_types(detail: str) -> list[str] | None:
        d = (detail or "").strip()
        if not d:
            return None
        start = d.find("(")
        if start == -1:
            return None
        depth = 0
        end = None
        for i, ch in enumerate(d[start:], start=start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            return None
        return _split_sig_types(d[start : end + 1])

    if signature and k == "method":
        want = _split_sig_types(signature)
        if want:
            narrowed: list[_DocSym] = []
            for c in candidates:
                got = _extract_detail_param_types(c.detail)
                if got == want:
                    narrowed.append(c)
            if narrowed:
                candidates = narrowed
    if detail_regex:
        try:
            rx = re.compile(detail_regex)
        except re.error as e:
            return None, f"invalid detail_regex: {e}"
        candidates = [c for c in candidates if rx.search(c.detail or "") is not None]

    if not candidates:
        return None, "symbol not found"
    if len(candidates) > 1:
        details = ", ".join(sorted({c.detail for c in candidates if c.detail})[:5])
        return None, f"ambiguous match ({len(candidates)} candidates){': ' + details if details else ''}"
    return candidates[0].range_start, "ok"


def _find_method_param_name_offset(*, text: str, method_name_off: int, param_index: int) -> int | None:
    """
    Given an offset pointing at the method name identifier, find the offset of
    the parameter *name* token at param_index (0-based).
    """
    if param_index < 0:
        return None
    open_paren = text.find("(", method_name_off)
    if open_paren == -1:
        return None

    n = len(text)
    depth = 0
    angle = 0
    parts: list[tuple[int, int]] = []
    cur_start = open_paren + 1

    i = open_paren
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                parts.append((cur_start, i))
                break
        elif ch == "<":
            angle += 1
        elif ch == ">" and angle > 0:
            angle -= 1
        elif ch == "," and depth == 1 and angle == 0:
            parts.append((cur_start, i))
            cur_start = i + 1
        i += 1

    if not parts:
        return None
    if len(parts) == 1 and text[parts[0][0] : parts[0][1]].strip() == "":
        return None
    if param_index >= len(parts):
        return None

    seg_start, seg_end = parts[param_index]
    seg = text[seg_start:seg_end]
    matches = list(re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*", seg))
    if not matches:
        return None
    keywords = {"final"}
    for m in reversed(matches):
        tok = m.group(0)
        if tok in keywords:
            continue
        return seg_start + m.start()
    return None


def _text_in_range(path: Path, rng: dict[str, Any]) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    lo = _line_offsets(text)
    start = (rng.get("start") or {}) if isinstance(rng, dict) else {}
    end = (rng.get("end") or {}) if isinstance(rng, dict) else {}
    s_off = _pos_to_offset(lo, start, text_len=len(text))
    e_off = _pos_to_offset(lo, end, text_len=len(text))
    return text[s_off:e_off]


# ---------------------------
# Main
# ---------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, action="append", default=[Path("client/refactor/symbol_renames.csv")])
    ap.add_argument("--csv-dir", type=Path, default=Path("client/refactor/.symbol-renames"))
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"))
    ap.add_argument("--classes-csv", type=Path, default=Path("client/refactor/classes.csv"))
    ap.add_argument("--report", type=Path, default=Path("docs/rename-report-symbols-lsp.md"))
    ap.add_argument("--max-renames", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")

    ap.add_argument("--jdtls-home", type=Path, default=None)
    ap.add_argument("--jdtls-cmd", nargs="+", default=None)
    ap.add_argument("--java-cmd", default="java")
    ap.add_argument("--timeout-s", type=float, default=60.0)
    ap.add_argument("--data-dir", type=Path, default=Path("build/jdtls-data"))
    ap.add_argument("--no-eclipse-project", action="store_true")
    args = ap.parse_args(argv)

    root = Path.cwd()
    src_dir = args.src_dir.resolve()
    if not src_dir.exists():
        raise SystemExit(f"--src-dir not found: {src_dir}")

    csv_files = [Path(p) for p in (args.csv or [])]
    csv_dir = Path(args.csv_dir) if args.csv_dir else None
    if not any(p.exists() for p in csv_files) and not (csv_dir and csv_dir.exists()):
        raise SystemExit("No symbol rename mappings found (expected --csv files and/or --csv-dir).")

    if not args.no_eclipse_project:
        libs = [Path("libs/clientlibs.jar")]
        _ensure_eclipse_project(root, src_dir=Path("client/src"), lib_jars=libs, name=root.name)

    if args.jdtls_cmd:
        cmd = list(args.jdtls_cmd)
    else:
        jdtls_home = args.jdtls_home or _detect_jdtls_home()
        if not jdtls_home:
            raise SystemExit(
                "\n".join(
                    [
                        "Could not find a JDTLS installation.",
                        "",
                        "Install JDTLS locally (for example via a VS Code Java extension), then run one of:",
                        "  python tools/apply_symbol_renames.py --jdtls-home /path/to/jdtls",
                        "  python tools/apply_symbol_renames.py --jdtls-cmd <full jdtls command...>",
                        "",
                        "Auto-detection checks common VS Code extension paths under ~/.vscode*/extensions.",
                    ]
                )
            )
        cmd = _jdtls_cmd_from_home(jdtls_home, java_cmd=args.java_cmd, data_dir=args.data_dir)

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(root))
    client = LspClient(proc, timeout_s=args.timeout_s)

    root_uri = root.resolve().as_uri()
    client.request(
        "initialize",
        {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {
                    "workspaceEdit": {"documentChanges": True, "resourceOperations": ["rename", "create", "delete"]},
                }
            },
        },
    )
    client.notify("initialized", {})

    report = Report(renamed=[], skipped=[])
    mappings = load_symbol_csvs(csv_files=csv_files, csv_dir=csv_dir)

    applied = 0
    for r in mappings:
        if args.max_renames >= 0 and applied >= args.max_renames:
            break

        # Resolve file path.
        path = _resolve_java_file(r.file, root=root, src_dir=src_dir, classes_csv=args.classes_csv)
        if not path:
            report.skipped.append((r, "file not found / ambiguous"))
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        uri = path.as_uri()

        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "java",
                    "version": 1,
                    "text": text,
                }
            },
        )

        try:
            pos: dict[str, int] | None = None

            if r.line is not None and r.col is not None:
                pos = _pos_1based_to_lsp(r.line, r.col)
            elif r.kind.strip() == "local":
                report.skipped.append((r, "local renames require line+col"))
                continue
            elif r.kind.strip() == "param":
                if not r.member:
                    report.skipped.append((r, "param renames require member=<method name>"))
                    continue
                if r.param_index is None:
                    report.skipped.append((r, "param renames require param_index (0-based) or line+col"))
                    continue

                syms_raw = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
                syms = _parse_document_symbols(syms_raw)
                method_pos, why = _choose_symbol_start(
                    syms=syms,
                    kind="method",
                    name=r.member,
                    container=r.owner,
                    detail_regex=r.detail_regex,
                    signature=r.signature,
                )
                if not method_pos:
                    report.skipped.append((r, f"method not found: {why}"))
                    continue

                method_off = _pos_to_offset(_line_offsets(text), method_pos, text_len=len(text))
                param_name_off = _find_method_param_name_offset(text=text, method_name_off=method_off, param_index=r.param_index)
                if param_name_off is None:
                    report.skipped.append((r, "could not locate param name in signature"))
                    continue
                pos = _offset_to_lsp_position(text, param_name_off)
            else:
                # Use documentSymbol for type/method/field when possible.
                syms_raw = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
                syms = _parse_document_symbols(syms_raw)
                pos, why = _choose_symbol_start(
                    syms=syms,
                    kind=r.kind,
                    name=r.old,
                    container=r.owner,
                    detail_regex=r.detail_regex,
                    signature=r.signature,
                )
                if not pos:
                    report.skipped.append((r, why))
                    continue

            # Validate the rename target.
            prep = client.request("textDocument/prepareRename", {"textDocument": {"uri": uri}, "position": pos})
            if not prep or not isinstance(prep, dict) or "range" not in prep:
                report.skipped.append((r, "prepareRename returned no range"))
                continue

            current = _text_in_range(path, prep.get("range") or {})
            if current == r.new:
                report.skipped.append((r, "already renamed"))
                continue
            if current != r.old:
                report.skipped.append((r, f"target text mismatch (found {current!r})"))
                continue

            edit = client.request("textDocument/rename", {"textDocument": {"uri": uri}, "position": pos, "newName": r.new})
            if not edit:
                report.skipped.append((r, "rename produced no edits"))
                continue

            _apply_workspace_edit(edit, dry_run=args.dry_run)
            report.renamed.append((r, "ok"))
            applied += 1
        except Exception as e:
            report.skipped.append((r, f"rename failed: {e}"))
        finally:
            client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.to_markdown(), encoding="utf-8")
    print(f"Renamed {len(report.renamed)} symbols. Wrote {args.report}")

    try:
        client.request("shutdown", {})
    finally:
        client.notify("exit", {})
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
