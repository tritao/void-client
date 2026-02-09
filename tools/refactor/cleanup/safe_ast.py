from __future__ import annotations

from pathlib import Path

from tools.refactor.common.ts_java import build_java_parser, iter_nodes


def _char_to_byte_offset(text: str, char_offset: int) -> int:
    if char_offset <= 0:
        return 0
    return len(text[:char_offset].encode("utf-8"))


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())


def _is_statement_like(node_type: str) -> bool:
    if node_type.endswith("_statement"):
        return True
    return node_type in {
        "local_variable_declaration",
        "explicit_constructor_invocation",
    }


def _line_aligned_cut(data: bytes, start: int, end: int) -> tuple[int, int]:
    line_start = data.rfind(b"\n", 0, start)
    line_start = 0 if line_start < 0 else line_start + 1
    line_end = data.find(b"\n", end)
    line_end = len(data) if line_end < 0 else line_end
    leading = data[line_start:start].strip()
    trailing = data[end:line_end].strip()
    if not leading and not trailing:
        cut_start = line_start
        cut_end = line_end + 1 if line_end < len(data) else line_end
        return cut_start, cut_end
    return start, end


def drop_statement_contains_in_method_body(
    text: str,
    *,
    body_start: int,
    body_end: int,
    match_text: str,
) -> tuple[str, bool]:
    if body_start < 0 or body_end <= body_start:
        return text, False
    needle = _normalize_ws(match_text)
    if not needle:
        return text, False
    try:
        data = text.encode("utf-8")
        parser = build_java_parser(out_so=Path("build/ts-languages-java.so"))
        tree = parser.parse(data)
    except Exception:
        return text, False

    start_b = _char_to_byte_offset(text, body_start)
    end_b = _char_to_byte_offset(text, body_end)
    best_span: tuple[int, int] | None = None

    for node in iter_nodes(tree.root_node):
        if node.start_byte < start_b or node.end_byte > end_b:
            continue
        if not _is_statement_like(node.type):
            continue
        snippet = data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        if needle not in _normalize_ws(snippet):
            continue
        span = (node.start_byte, node.end_byte)
        if best_span is None:
            best_span = span
            continue
        prev_len = best_span[1] - best_span[0]
        cur_len = span[1] - span[0]
        if cur_len < prev_len or (cur_len == prev_len and span[0] < best_span[0]):
            best_span = span

    if best_span is None:
        return text, False

    cut_start, cut_end = _line_aligned_cut(data, best_span[0], best_span[1])
    out = data[:cut_start] + data[cut_end:]
    return out.decode("utf-8", errors="replace"), True

