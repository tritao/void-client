from __future__ import annotations


def apply_edits_bytes(data: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    for start, end, repl in sorted(edits, key=lambda t: (t[0], t[1]), reverse=True):
        data = data[:start] + repl + data[end:]
    return data


def dedupe_and_prune_overlaps(
    edits: list[tuple[int, int, bytes]],
) -> tuple[list[tuple[int, int, bytes]], list[str]]:
    if not edits:
        return [], []
    skipped: list[str] = []

    by_span: dict[tuple[int, int], bytes] = {}
    for start, end, repl in edits:
        key = (start, end)
        if key not in by_span:
            by_span[key] = repl
            continue
        if by_span[key] == repl:
            continue
        old = by_span[key]
        keep = repl if len(repl) < len(old) else old
        drop = old if keep is repl else repl
        by_span[key] = keep
        skipped.append(f"Conflicting edit span {start}:{end} (kept {keep!r}, dropped {drop!r})")

    spans = [(s, e, r) for (s, e), r in by_span.items()]
    spans.sort(key=lambda t: (t[0], t[1]))

    pruned: list[tuple[int, int, bytes]] = []
    for start, end, repl in spans:
        if not pruned:
            pruned.append((start, end, repl))
            continue
        p_start, p_end, _p_repl = pruned[-1]
        if start >= p_end:
            pruned.append((start, end, repl))
            continue
        cur_len = end - start
        prev_len = p_end - p_start
        if cur_len < prev_len:
            skipped.append(f"Overlapping edit dropped previous span {p_start}:{p_end} for {start}:{end}")
            pruned[-1] = (start, end, repl)
        else:
            skipped.append(f"Overlapping edit skipped span {start}:{end} (kept {p_start}:{p_end})")
    return pruned, skipped

