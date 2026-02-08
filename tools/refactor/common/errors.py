from __future__ import annotations


class RefactorError(Exception):
    """Base error for refactor tooling failures."""


class ValidationError(RefactorError):
    """Input validation failed."""


class ConflictError(RefactorError):
    """Conflicting mappings or actions detected."""

