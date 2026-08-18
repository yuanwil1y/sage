"""Keep runtime model services in sync with the local model store."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from model_store import get_model_spec, model_file_path, model_status

log = logging.getLogger("runtime.models")

_TRACKED_MODELS = ("hy-mt2", "whisper-medium")


class PassthroughTranslator:
    """Keep the rest of Sage usable while Hy-MT2 is unavailable."""

    def translate(
        self,
        text: str,
        source_lang: str = "日语",
        target_lang: str = "简体中文",
    ) -> str:
        return text


def _read_model_state(model_key: str) -> tuple[str, tuple[tuple[str, int, int], ...]]:
    """Return install status plus a file fingerprint for replacement detection."""
    spec = get_model_spec(model_key)
    status = model_status(spec)
    if status != "installed":
        return status, ()

    fingerprint: list[tuple[str, int, int]] = []
    try:
        for item in spec.files:
            stat = model_file_path(spec, item).stat()
            fingerprint.append((item.name, stat.st_size, stat.st_mtime_ns))
    except OSError:
        # A model directory can be replaced between status() and stat(). Treat
        # that short race as an in-progress change and retry on the next poll.
        return "changing", ()
    return status, tuple(fingerprint)


class RuntimeModelController:
    """Hot-activate model installs/replacements and degrade safely on deletion."""

    def __init__(
        self,
        orchestrator: Any,
        mt2_manager: Any,
        *,
        translator_factory: Callable[[str], Any] | None = None,
        mt2_ready_timeout: float = 60.0,
        watch_interval: float = 1.0,
        state_reader: Callable[[str], object] | None = None,
    ) -> None:
        if translator_factory is None:
            from translation.hy_mt2_translator import HyMT2LocalTranslator

            translator_factory = HyMT2LocalTranslator
        self.orchestrator = orchestrator
        self.mt2_manager = mt2_manager
        self.translator_factory = translator_factory
        self.mt2_ready_timeout = float(mt2_ready_timeout)
        self.watch_interval = max(0.1, float(watch_interval))
        self._state_reader = state_reader or _read_model_state
        self._last_states: dict[str, object] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start a lightweight watcher without re-activating the initial state."""
        if self.running:
            return
        self._last_states = {
            key: self._safe_read_state(key) for key in _TRACKED_MODELS
        }
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="RuntimeModelWatcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def _safe_read_state(self, model_key: str) -> object:
        try:
            return self._state_reader(model_key)
        except Exception:
            log.exception("读取模型状态失败: %s", model_key)
            return ("error", ())

    @staticmethod
    def _status_of(state: object) -> str:
        if isinstance(state, tuple) and state:
            return str(state[0])
        return str(state)

    def refresh_once(self) -> None:
        """Poll tracked model fingerprints once and apply stable state changes."""
        for model_key in _TRACKED_MODELS:
            current = self._safe_read_state(model_key)
            previous = self._last_states.get(model_key, current)
            if current == previous:
                self._last_states[model_key] = current
                continue

            self._last_states[model_key] = current
            old_status = self._status_of(previous)
            new_status = self._status_of(current)

            if new_status == "installed":
                # Covers first install and atomic replacement while status stays
                # installed but file size/mtime fingerprint changes.
                self.handle_change(model_key, "下载")
            elif old_status == "installed" and new_status != "installed":
                self.handle_change(model_key, "删除")

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self.watch_interval):
            try:
                self.refresh_once()
            except Exception:
                # A watcher bug must not terminate the rest of Sage.
                log.exception("运行时模型监控失败")

    def handle_change(self, model_key: str, action: str) -> None:
        """React to a stable install/import/replacement/delete transition."""
        if model_key == "hy-mt2":
            self._refresh_hy_mt2(action)
        elif model_key == "whisper-medium":
            self._refresh_whisper(action)

    def _report(self, section: str, text: str) -> None:
        report = getattr(self.orchestrator, "_report_status", None)
        if callable(report):
            report(section, text)

    def _refresh_hy_mt2(self, action: str) -> None:
        # A replacement model should never be loaded while the old server still
        # has the GGUF file open. Delete likewise needs to stop the process.
        self.mt2_manager.stop()

        if action == "删除":
            self.orchestrator.replace_translator(PassthroughTranslator())
            self._report("Local Translation", "Hy-MT2: unavailable")
            log.info("Hy-MT2 已删除，运行时切换为原文透传")
            return
        if action not in {"下载", "导入"}:
            return

        try:
            self.mt2_manager.start()
            self.mt2_manager.wait_ready(timeout=self.mt2_ready_timeout)
            translator = self.translator_factory(self.mt2_manager.base_url)
        except Exception as exc:
            self.mt2_manager.stop()
            self.orchestrator.replace_translator(PassthroughTranslator())
            self._report("Local Translation", f"Hy-MT2: unavailable ({exc})")
            log.exception("Hy-MT2 模型已安装，但运行时激活失败")
            return

        self.orchestrator.replace_translator(translator)
        self._report("Local Translation", "Hy-MT2: loaded")
        log.info("Hy-MT2 模型变更已热激活")

    def _refresh_whisper(self, action: str) -> None:
        transcriber = getattr(self.orchestrator, "transcriber", None)
        if transcriber is None:
            return

        reset_model = getattr(transcriber, "reset_model", None)
        if callable(reset_model):
            reset_model()
        # Allow a future missing-model error to be surfaced again after model
        # replacement/deletion instead of being permanently suppressed.
        if hasattr(self.orchestrator, "_voice_model_error_reported"):
            self.orchestrator._voice_model_error_reported = False

        available = bool(getattr(transcriber, "model_available", False))
        if action == "删除" or not available:
            self._report("Voice", "ASR model: not installed")
        else:
            self._report("Voice", "ASR: ready")
        log.info("Whisper 运行时状态已刷新：%s", "ready" if available else "missing")
