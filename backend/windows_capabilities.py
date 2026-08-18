"""Windows capability checks used by the voice-capture runtime."""

from __future__ import annotations

import sys
from dataclasses import dataclass

MIN_PROCESS_LOOPBACK_BUILD = 20348


@dataclass(frozen=True)
class ProcessLoopbackSupport:
    supported: bool
    build: int | None
    minimum_build: int = MIN_PROCESS_LOOPBACK_BUILD

    @property
    def message(self) -> str:
        if self.supported:
            return "Process loopback supported"
        if self.build is None:
            return "Process loopback requires Windows"
        return (
            f"Voice capture requires Windows build {self.minimum_build} or newer; "
            f"current build is {self.build}. Text chat translation remains available."
        )


class UnsupportedWindowsBuildError(FileNotFoundError):
    """Caught by the existing capture-start path and surfaced as a clear error."""


def process_loopback_support() -> ProcessLoopbackSupport:
    """Return whether the current OS can use per-process WASAPI loopback.

    Non-Windows hosts are treated as unsupported for the native capture runtime,
    but unit tests can still instantiate the rest of the Python pipeline.
    """
    if sys.platform != "win32":
        return ProcessLoopbackSupport(False, None)
    build = int(sys.getwindowsversion().build)
    return ProcessLoopbackSupport(build >= MIN_PROCESS_LOOPBACK_BUILD, build)


def require_process_loopback_support() -> None:
    support = process_loopback_support()
    if not support.supported:
        raise UnsupportedWindowsBuildError(support.message)
