from __future__ import annotations

import re


def apply_non_string_comment_replacements(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    mode = "code"
    segment_start = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if mode == "code":
            if ch == "/" and nxt == "/":
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "block_comment"
                i += 2
                continue
            if ch == '"':
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "string"
                i += 1
                continue
            if ch == "'":
                segment = text[segment_start:i]
                for rx, repl in patterns:
                    segment = rx.sub(repl, segment)
                out.append(segment)
                segment_start = i
                mode = "char"
                i += 1
                continue
            i += 1
            continue
        if mode == "line_comment":
            if ch == "\n":
                out.append(text[segment_start : i + 1])
                segment_start = i + 1
                mode = "code"
            i += 1
            continue
        if mode == "block_comment":
            if ch == "*" and nxt == "/":
                i += 2
                out.append(text[segment_start:i])
                segment_start = i
                mode = "code"
                continue
            i += 1
            continue
        if mode == "string":
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                i += 1
                out.append(text[segment_start:i])
                segment_start = i
                mode = "code"
                continue
            i += 1
            continue
        if mode == "char":
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                i += 1
                out.append(text[segment_start:i])
                segment_start = i
                mode = "code"
                continue
            i += 1
            continue
    tail = text[segment_start:]
    if mode == "code":
        for rx, repl in patterns:
            tail = rx.sub(repl, tail)
    out.append(tail)
    return "".join(out)

