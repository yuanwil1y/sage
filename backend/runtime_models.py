"""Runtime activation for models changed by the explicit Model Manager."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("runtime.models")


class PassthroughTranslator:
    """Keep the rest of Sage usable while Hy-MT2 is unavailable."""

    def translate(
        self,
        text: str,
        source_lang: str = "日语",
        target_lang: str = "简体中文",
    ) -> str:
        return text


class RuntimeModelController:
    """Apply Model Manager install/delete changes to a running orchestrator."""

    def __init__(
        self,
        orchestrator: Any,
        mt2_manager: Any,
        *,
        translator_factory: Callable[[str], Any] | None = None,
        mt2_ready_timeout: float = 60.0,
    ) -> None:
        if translator_factory is None:
            from translation.hy_mt2_translator import HyMT2LocalTranslator

            translator_factory = HyMT2LocalTranslator
        self.orchestrator = orchestrator
        self.mt2_manager = mt2_manager
        self.translator_factory = translator_factory
        self.mt2_ready_timeout = float(mt2_ready_timeout)

    def handle_change(self, model_key: str, action: str) -> None:
        """React after a successful download/import/delete operation."""
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
