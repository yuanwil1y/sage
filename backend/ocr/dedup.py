"""OCR chat-window deduplication.

Deduplication is based on continuity between consecutive visible chat windows,
not a long global text TTL. This suppresses the same on-screen row across OCR
frames while allowing a player to send the same valid callout again later.
"""

from __future__ import annotations

from collections.abc import Sequence

from rapidfuzz import fuzz


class OcrDeduper:
    def __init__(
        self,
        *,
        threshold: float = 90.0,
        ttl: float | None = None,
    ) -> None:
        if not 0 <= threshold <= 100:
            raise ValueError("threshold must be between 0 and 100")
        self.threshold = float(threshold)
        # ``ttl`` remains accepted for source compatibility with older callers,
        # but continuity rather than elapsed wall time now defines duplication.
        self.ttl = ttl
        self._previous_lines: list[str] = []

    def reset(self) -> None:
        self._previous_lines = []

    def filter_new(self, visible_lines: Sequence[str]) -> list[str]:
        """Return rows newly appended to the visible chat window.

        The largest fuzzy-matching overlap between the previous window suffix
        and current window prefix is treated as still-visible history. Any rows
        after that overlap are new, even if their text matches an older callout.
        """
        current = [str(line).strip() for line in visible_lines if str(line).strip()]
        if not current:
            self._previous_lines = []
            return []

        previous = self._previous_lines
        overlap = 0
        for size in range(min(len(previous), len(current)), 0, -1):
            previous_tail = previous[-size:]
            current_head = current[:size]
            if all(
                fuzz.ratio(old, new) >= self.threshold
                for old, new in zip(previous_tail, current_head)
            ):
                overlap = size
                break

        self._previous_lines = current
        return current[overlap:]
