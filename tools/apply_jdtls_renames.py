#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import select
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


"""
Experimental: apply Java renames via JDTLS (Language Server Protocol).

This is intended for *symbol-aware* renames (types, fields, methods) where the
token-based approach in tools/apply_class_renames.py is too risky.

By default this script reads a simple CSV mapping:
  OldName,NewName

Current implementation focuses on top-level type renames in this repo's flat
default-package layout (client/src/*.java). It will:
  - open the defining file for OldName
  - request an LSP rename at the type declaration position
  - apply the returned WorkspaceEdit (text edits + file renames when provided)

You must have JDTLS installed locally (no network bootstrap here). Pass
--jdtls-home, or rely on auto-detection (VS Code server locations, etc.).
"""


# ---------------------------
# CSV parsing / report
# ---------------------------


@dataclasses.dataclass(frozen=True)
class RenameCandidate:
    src: str
    dst: str
    score: float = 1.0
    anchors: str = ""


@dataclasses.dataclass
class Report:
    renamed: list[tuple[str, str, float, str]]
    skipped: list[tuple[str, str, float, str]]

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Rename report (JDTLS / LSP)")
        lines.append("")
        lines.append(f"Renamed: {len(self.renamed)}  ")
        lines.append(f"Skipped: {len(self.skipped)}")
        lines.append("")
        lines.append("## Renamed")
        lines.append("")
        lines.append("| src | dst | score | anchors |")
        lines.append("|---|---:|---:|---|")
        for src, dst, score, anchors in self.renamed:
            lines.append(f"| `{src}` | `{dst}` | {score:.6f} | {anchors} |")
        lines.append("")
        lines.append("## Skipped")
        lines.append("")
        lines.append("| src | dst | score | reason |")
        lines.append("|---|---:|---:|---|")
        for src, dst, score, reason in self.skipped:
            lines.append(f"| `{src}` | `{dst}` | {score:.6f} | {reason} |")
        lines.append("")
        return "\n".join(lines)


def load_csv(csv_path: Path) -> list[RenameCandidate]:
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
        rows.append(RenameCandidate(src=src, dst=dst, score=score, anchors=anchors))
    return rows


# ---------------------------
# Minimal Java tokenization (for finding declaration positions)
# ---------------------------


@dataclasses.dataclass(frozen=True)
class _Tok:
    kind: str  # ws | ident | sym | comment | string | char | number
    text: str
    start: int
    end: int


def _is_ident_start(ch: str) -> bool:
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ch in ("_", "$")


def _is_ident_part(ch: str) -> bool:
    return _is_ident_start(ch) or ("0" <= ch <= "9")


def _consume_number_literal(text: str, i: int) -> int:
    # Conservative; just enough to avoid splitting 0xa into "0x" + "a".
    n = len(text)
    j = i
    if j >= n or not text[j].isdigit():
        return j
    if text[j] == "0" and j + 1 < n and text[j + 1] in ("x", "X", "b", "B"):
        j += 2
        while j < n and (text[j].isalnum() or text[j] == "_"):
            j += 1
        return j
    while j < n and (text[j].isdigit() or text[j] in ("_", ".", "e", "E", "+", "-")):
        j += 1
    return j


def _tokenize_java(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                j = text.find("\n", i + 2)
                if j == -1:
                    j = n
                else:
                    j += 1
                toks.append(_Tok("comment", text[i:j], i, j))
                i = j
                continue
            if nxt == "*":
                j = text.find("*/", i + 2)
                j = (j + 2) if j != -1 else n
                toks.append(_Tok("comment", text[i:j], i, j))
                i = j
                continue

        if ch == '"':
            j = i + 1
            while j < n:
                c = text[j]
                j += 1
                if c == "\\" and j < n:
                    j += 1
                    continue
                if c == '"':
                    break
            toks.append(_Tok("string", text[i:j], i, j))
            i = j
            continue

        if ch == "'":
            j = i + 1
            while j < n:
                c = text[j]
                j += 1
                if c == "\\" and j < n:
                    j += 1
                    continue
                if c == "'":
                    break
            toks.append(_Tok("char", text[i:j], i, j))
            i = j
            continue

        if ch.isspace():
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            toks.append(_Tok("ws", text[i:j], i, j))
            i = j
            continue

        if ch.isdigit():
            j = _consume_number_literal(text, i)
            toks.append(_Tok("number", text[i:j], i, j))
            i = j
            continue

        if _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_part(text[j]):
                j += 1
            toks.append(_Tok("ident", text[i:j], i, j))
            i = j
            continue

        toks.append(_Tok("sym", ch, i, i + 1))
        i += 1

    return toks


def _find_top_level_type_decl_offset(text: str, type_name: str) -> int | None:
    toks = [t for t in _tokenize_java(text) if t.kind not in ("ws", "comment", "string", "char")]
    n = len(toks)
    for i in range(n - 2):
        # class Foo / interface Foo / enum Foo
        if toks[i].kind == "ident" and toks[i].text in ("class", "interface", "enum"):
            nxt = toks[i + 1]
            if nxt.kind == "ident" and nxt.text == type_name:
                return nxt.start
        # @interface Foo
        if toks[i].kind == "sym" and toks[i].text == "@" and toks[i + 1].kind == "ident" and toks[i + 1].text == "interface":
            name_tok = toks[i + 2]
            if name_tok.kind == "ident" and name_tok.text == type_name:
                return name_tok.start
    return None


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


# ---------------------------
# LSP client
# ---------------------------


class LspClient:
    def __init__(self, proc: subprocess.Popen[bytes], *, timeout_s: float = 60.0) -> None:
        self.proc = proc
        self.timeout_s = timeout_s
        self._next_id = 1
        self._stderr_tail: list[str] = []

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
        try:
            length = int(length_s)
        except ValueError:
            raise RuntimeError(f"invalid Content-Length: {length_s!r}")
        body = self._read_exactly(length, timeout_s=self.timeout_s)
        try:
            return json.loads(body.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"failed to decode LSP message: {e}") from e

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
                # ignore notifications or other responses
                continue
            if "error" in msg and msg["error"]:
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

    # Apply from back to front.
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

    # documentChanges can include file operations; prefer it when present.
    doc_changes = edit.get("documentChanges")
    if isinstance(doc_changes, list):
        for ch in doc_changes:
            if not isinstance(ch, dict):
                continue
            if "kind" in ch and ch.get("kind") == "rename":
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

    # Fallback: plain `changes` map.
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


def _fallback_rename_file_for_type(src_file: Path, dst_type: str, *, dry_run: bool) -> None:
    """
    If JDTLS didn't include a file rename operation, try to rename the file to
    match the new top-level type.
    """
    if not src_file.exists():
        return
    dst_file = src_file.with_name(dst_type + ".java")
    if dst_file.exists():
        return
    text = src_file.read_text(encoding="utf-8", errors="replace")
    if (f"class {dst_type}" not in text) and (f"interface {dst_type}" not in text) and (f"enum {dst_type}" not in text) and (f"@interface {dst_type}" not in text):
        return
    if not dry_run:
        if _git_available():
            _git_mv(src_file, dst_file)
        else:
            src_file.rename(dst_file)


# ---------------------------
# JDTLS startup / project bootstrap
# ---------------------------


def _detect_jdtls_home() -> Path | None:
    """
    Best-effort detection of JDTLS installations.
    """
    candidates: list[Path] = []
    home = Path.home()

    # Common user installs.
    candidates.extend(
        [
            home / ".local" / "share" / "jdtls",
            home / ".local" / "share" / "eclipse.jdt.ls",
            home / ".cache" / "jdtls",
            home / ".cache" / "eclipse.jdt.ls",
        ]
    )

    # VS Code / VS Code server extension layouts.
    for base in [home / ".vscode" / "extensions", home / ".vscode-server" / "extensions"]:
        if not base.exists():
            continue
        for ext in sorted(base.glob("redhat.java-*")):
            server = ext / "server"
            if server.exists():
                candidates.append(server)

    # JDTLS itself.
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
    """
    Write minimal Eclipse project metadata so JDTLS can resolve the build path.

    Files are gitignored by default (see .gitignore updates in this repo).
    """
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
# Main
# ---------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=Path("client/refactor/classes.csv"))
    ap.add_argument("--src-dir", type=Path, default=Path("client/src"))
    ap.add_argument("--report", type=Path, default=Path("docs/rename-report-lsp.md"))
    ap.add_argument("--max-renames", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")

    ap.add_argument("--jdtls-home", type=Path, default=None)
    ap.add_argument("--jdtls-cmd", nargs="+", default=None, help="Run JDTLS using this explicit command (overrides --jdtls-home).")
    ap.add_argument("--java-cmd", default="java", help="Java command used to start JDTLS (usually Java 17+).")
    ap.add_argument("--timeout-s", type=float, default=60.0)
    ap.add_argument("--data-dir", type=Path, default=Path("build/jdtls-data"))

    ap.add_argument(
        "--no-eclipse-project",
        action="store_true",
        help="Do not create .project/.classpath/.settings for JDTLS (may reduce rename accuracy).",
    )
    args = ap.parse_args(argv)

    root = Path.cwd()
    args.src_dir = args.src_dir.resolve()
    if not args.src_dir.exists():
        raise SystemExit(f"--src-dir not found: {args.src_dir}")

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
                        "  python tools/apply_jdtls_renames.py --jdtls-home /path/to/jdtls",
                        "  python tools/apply_jdtls_renames.py --jdtls-cmd <full jdtls command...>",
                        "",
                        "Auto-detection checks common VS Code extension paths under ~/.vscode*/extensions.",
                    ]
                )
            )
        cmd = _jdtls_cmd_from_home(jdtls_home, java_cmd=args.java_cmd, data_dir=args.data_dir)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(root),
    )
    client = LspClient(proc, timeout_s=args.timeout_s)

    root_uri = root.resolve().as_uri()
    init_result = client.request(
        "initialize",
        {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "workspace": {
                    "workspaceEdit": {
                        "documentChanges": True,
                        "resourceOperations": ["rename", "create", "delete"],
                    }
                }
            },
        },
    )
    _ = init_result  # kept for debugging; not used yet
    client.notify("initialized", {})

    report = Report(renamed=[], skipped=[])

    mappings = load_csv(args.csv)
    java_files = {p.stem: p for p in args.src_dir.glob("*.java")}

    chosen: list[RenameCandidate] = []
    for r in mappings:
        if args.max_renames >= 0 and len(chosen) >= args.max_renames:
            break
        src_path = java_files.get(r.src)
        if not src_path:
            continue
        dst_path = src_path.with_name(r.dst + ".java")
        if dst_path.exists():
            continue
        chosen.append(r)

    if not chosen:
        report.skipped.append(("", "", 0.0, "no applicable renames found (already applied?)"))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report.to_markdown(), encoding="utf-8")
        print(f"Renamed 0 symbols. Wrote {args.report}")
        client.request("shutdown", {})
        client.notify("exit", {})
        return 0

    for r in chosen:
        src_path = java_files.get(r.src)
        if not src_path or not src_path.exists():
            report.skipped.append((r.src, r.dst, r.score, "src missing"))
            continue
        if src_path.with_name(r.dst + ".java").exists():
            report.skipped.append((r.src, r.dst, r.score, "dst file already exists"))
            continue

        text = src_path.read_text(encoding="utf-8", errors="replace")
        decl_off = _find_top_level_type_decl_offset(text, r.src)
        if decl_off is None:
            report.skipped.append((r.src, r.dst, r.score, "type declaration not found in file"))
            continue

        uri = src_path.resolve().as_uri()
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
            pos = _offset_to_lsp_position(text, decl_off)
            edit = client.request("textDocument/rename", {"textDocument": {"uri": uri}, "position": pos, "newName": r.dst})
            if edit:
                _apply_workspace_edit(edit, dry_run=args.dry_run)
                _fallback_rename_file_for_type(src_path, r.dst, dry_run=args.dry_run)
                report.renamed.append((r.src, r.dst, r.score, r.anchors))
            else:
                report.skipped.append((r.src, r.dst, r.score, "rename produced no edits"))
        except Exception as e:
            report.skipped.append((r.src, r.dst, r.score, f"rename failed: {e}"))
        finally:
            client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

        # refresh file list after a rename (files may have moved)
        java_files = {p.stem: p for p in args.src_dir.glob("*.java")}

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
