"""Route Python logging into the desktop debug tab."""

from __future__ import annotations

import logging
import threading
from collections import deque

from PySide6 import QtCore


class GuiLogBridge(QtCore.QObject):
    """Thread-safe log buffer with a Qt signal for queued UI updates."""

    message_ready = QtCore.Signal(str)

    def __init__(self, max_history: int = 2000) -> None:
        super().__init__()
        self._history: deque[str] = deque(maxlen=max_history)
        self._lock = threading.Lock()

    def publish(self, message: str) -> None:
        with self._lock:
            self._history.append(message)
        self.message_ready.emit(message)

    def history(self) -> list[str]:
        with self._lock:
            return list(self._history)


class GuiLogHandler(logging.Handler):
    """A logging handler that is safe to call from worker threads."""

    def __init__(self, bridge: GuiLogBridge) -> None:
        super().__init__()
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.publish(self.format(record))
        except Exception:
            self.handleError(record)


def install_gui_logging() -> tuple[GuiLogBridge, GuiLogHandler]:
    """Replace the console stream handler with a GUI-backed handler."""

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in root.handlers[:]:
        if type(handler) is logging.StreamHandler:
            root.removeHandler(handler)

    bridge = GuiLogBridge()
    handler = GuiLogHandler(bridge)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    root.addHandler(handler)
    return bridge, handler


def uninstall_gui_logging(handler: GuiLogHandler | None) -> None:
    if handler is None:
        return
    root = logging.getLogger()
    if handler in root.handlers:
        root.removeHandler(handler)
    handler.close()
