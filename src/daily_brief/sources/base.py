"""Shared types for sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SourceError(Exception):
    """A source failed in a way the user should be told about. Other sources keep running."""


@dataclass
class SourceResult:
    name: str
    posts: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. LinkedIn profile text

    @property
    def ok(self) -> bool:
        return not self.error
