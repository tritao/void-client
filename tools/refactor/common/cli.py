from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterable


def resolve_path_args(args: Namespace, names: Iterable[str]) -> None:
    for name in names:
        value = getattr(args, name, None)
        if value is None:
            continue
        setattr(args, name, value.resolve())
