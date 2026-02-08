from __future__ import annotations

import logging
import sys
from collections.abc import Mapping


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, event: str, **fields: object) -> None:
    if fields:
        parts = [event] + [f"{k}={_fmt(v)}" for k, v in sorted(fields.items())]
        logger.log(level, " ".join(parts))
        return
    logger.log(level, event)


def _fmt(value: object) -> str:
    if isinstance(value, Mapping):
        return "{" + ",".join(f"{k}:{_fmt(v)}" for k, v in sorted(value.items())) + "}"
    if isinstance(value, (list, tuple, set)):
        return "[" + ",".join(_fmt(v) for v in value) + "]"
    return str(value)

