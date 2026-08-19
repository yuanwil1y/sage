"""Small, platform-independent helpers for selecting Sage startup mode."""

from __future__ import annotations

import sys
from collections.abc import Sequence


def packaged_ui_requested(
    argv: Sequence[str] | None = None,
    *,
    frozen: bool | None = None,
) -> bool:
    """Return whether the desktop UI is the default for this invocation.

    Source runs remain headless by default. Frozen builds default to the GUI,
    while ``--headless`` is an explicit override for troubleshooting and
    automation.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    return "--headless" not in args and ("--ui" in args or is_frozen)


def resolve_use_ui(
    *,
    ui_requested: bool,
    headless_requested: bool,
    argv: Sequence[str] | None = None,
    frozen: bool | None = None,
) -> bool:
    """Resolve explicit CLI flags before applying the packaged default."""
    if headless_requested:
        return False
    if ui_requested:
        return True
    return packaged_ui_requested(argv=argv, frozen=frozen)
