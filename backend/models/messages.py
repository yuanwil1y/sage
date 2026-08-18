"""Canonical messages flowing through the translation pipeline.

These objects are deliberately small and serialisation-free.  The Python
backend uses them internally; :mod:`ipc.protocol` is the single place that
turns a ``TranslationResult`` into the Widget's NDJSON wire format.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal

SourceType = Literal["voice", "chat"]


def _new_id(source_type: SourceType) -> str:
    prefix = "v" if source_type == "voice" else "c"
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _validate_source_type(source_type: str) -> None:
    if source_type not in ("voice", "chat"):
        raise ValueError(f"unsupported source_type: {source_type!r}")


@dataclass(slots=True)
class SourceMessage:
    """One recognised Japanese source sentence.

    ``id`` is stable across translation and IPC delivery.  Voice messages
    use a ``v-`` prefix and chat messages use ``c-`` so logs and Widget state
    remain easy to inspect.
    """

    source_type: SourceType
    original: str
    id: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        _validate_source_type(self.source_type)
        if not self.id:
            self.id = _new_id(self.source_type)
        if self.created_at <= 0:
            self.created_at = time.time()

@dataclass(slots=True)
class TranslationResult:
    """A translated source sentence ready for IPC broadcast."""

    source_type: SourceType
    original: str
    translated: str
    id: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        _validate_source_type(self.source_type)
        if not self.id:
            self.id = _new_id(self.source_type)
        if self.created_at <= 0:
            self.created_at = time.time()
