from __future__ import annotations

SCHEMA = [
    "id",
    "module",
    "action",
    "kind",
    "file",
    "owner",
    "member",
    "signature",
    "param_index",
    "old",
    "new",
    "target_class",
    "target_file",
    "scope",
    "phase",
    "confidence",
    "status",
    "notes",
]

VALID_ACTIONS = {"rename", "extract", "class_rename", "defer"}
VALID_KINDS = {"type", "method", "field", "param", "local", "class", "static_init"}
VALID_PHASES = {"core", "module", "cleanup", "split"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_STATUS = {"proposed", "approved", "applied", "blocked"}

SYMBOL_VIEW_HEADER = [
    "file",
    "kind",
    "owner",
    "member",
    "signature",
    "param_index",
    "old",
    "new",
    "detail_regex",
    "line",
    "col",
    "notes",
    "module",
    "phase",
    "confidence",
    "status",
    "source",
]

CLASS_VIEW_HEADER = ["src", "dst", "score", "anchors"]
