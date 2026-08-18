"""后端编排器（规格文档第 18、33、35 节）。

把整条流水线串起来：
  ProcessFinder → AudioPipeline(VAD) → Transcriber(whisper) → Translator(Hy-MT2) → PipeServer

并串联 OCR 聊天链路（DXcam → PaddleOCR → LineAssembler → Dedup → Translator → PipeServer）。

本模块是 main.py 的核心，负责各 worker 的启动、数据流转、优雅退出。
真实音频/OCR 采集需运行 VALORANT 时验证；本模块保证接线正确。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

import numpy as np

from config.roi import RoiConfig
from ipc import protocol
from ipc.pipe_server import PipeServer
from models.messages import SourceMessage, TranslationResult
from translation.base import Translator

if TYPE_CHECKING:
    from audio.capture_reader import AudioCaptureReader
    from audio.pipeline import AudioPipeline
    from audio.transcriber import Transcriber
    from ocr.chat_worker import ChatOcrWorker
    from process.process_finder import ProcessInfo

log = logging.getLogger("orchestrator")


class TranslatorOrchestrator:
    """编排语音与聊天两条翻译主链，统一广播到 Game Bar Widget。

    语音链：ProcessFinder 发现 VALORANT → AudioCaptureReader 采 PCM → AudioPipeline(VAD)
           → Transcriber(whisper) → Translator → PipeServer
    聊天链：OCR（外部）→ handle_chat_line → Translator → PipeServer
    """

    def __init__(
        self,
        pipe: PipeServer,
        translator: Translator,
        transcriber: "Transcriber | None" = None,
        audio_pipeline: "AudioPipeline | None" = None,
        capture_reader: "AudioCaptureReader | None" = None,
        chat_worker: "ChatOcrWorker | None" = None,
        mode: Literal["text", "voicechat", "full"] = "full",
    ) -> None:
        if mode not in {"text", "voicechat", "full"}:
            raise ValueError(f"未知构建模式: {mode}")

        self.pipe = pipe
        self.translator = translator
        self.mode = mode
        self.voice_enabled = mode in {"voicechat", "full"}
        self.chat_enabled = mode in {"text", "full"}

        # These imports are deliberately local.  A text build must not pull in
        # faster-whisper/ctranslate2/av, and a voicechat build must not pull in
        # PaddleOCR/OpenCV/DXcam merely because the orchestrator is imported.
        if self.voice_enabled:
            if transcriber is None:
                from audio.transcriber import Transcriber

                transcriber = Transcriber()
            if audio_pipeline is None:
                from audio.pipeline import AudioPipeline

                audio_pipeline = AudioPipeline()
        else:
            transcriber = None
            audio_pipeline = None

        self.transcriber = transcriber
        self.audio_pipeline = audio_pipeline
        self._finder: Any = None
        self._capture: Optional["AudioCaptureReader"] = None
        self._capture_factory = capture_reader if self.voice_enabled else None
        self._chat_worker: "ChatOcrWorker | None" = chat_worker if self.chat_enabled else None
        self._status_callback: Callable[[str, str], None] | None = None
        self._voice_model_error_reported = False
        self._voice_settings = {"vad_threshold": 0.50, "min_silence_ms": 800}
        self._text_settings = {"poll_hz": 4.0, "min_score": 0.50, "change_threshold": 2.0}

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        """Subscribe a UI/status consumer to backend lifecycle updates."""
        self._status_callback = callback

    def configure_voice_settings(self, *, vad_threshold: float, min_silence_ms: int) -> None:
        """Apply voice settings to the current audio pipeline."""

        self._voice_settings = {
            "vad_threshold": float(vad_threshold),
            "min_silence_ms": int(min_silence_ms),
        }
        if self.audio_pipeline is not None:
            self.audio_pipeline.configure(**self._voice_settings)
        log.info(
            "语音设置已应用：敏感度 %.2f，静音结束等待 %d ms",
            self._voice_settings["vad_threshold"],
            self._voice_settings["min_silence_ms"],
        )

    def configure_text_settings(
        self,
        *,
        poll_hz: float,
        min_score: float,
        change_threshold: float,
    ) -> None:
        """Apply OCR tuning values to the current/future chat worker."""

        self._text_settings = {
            "poll_hz": float(poll_hz),
            "min_score": float(min_score),
            "change_threshold": float(change_threshold),
        }
        if self._chat_worker is not None:
            self._chat_worker.configure(**self._text_settings)
        log.info(
            "文字识别设置已应用：扫描 %.1f 次/秒，置信度 %.2f，变化阈值 %.1f",
            self._text_settings["poll_hz"],
            self._text_settings["min_score"],
            self._text_settings["change_threshold"],
        )

    def _report_status(self, section: str, text: str) -> None:
        if self._status_callback is None:
            return
        try:
            self._status_callback(section, text)
        except Exception:
            log.exception("状态回调失败: %s=%s", section, text)

    # ---- 语音主链 ----

    def handle_utterance(self, utterance_16k: np.ndarray) -> None:
        """一段完整语音识别 + 翻译 + 广播。"""
        if not self.voice_enabled or self.transcriber is None:
            return
        if utterance_16k.size == 0:
            return
        try:
            text = self.transcriber.transcribe(utterance_16k)
        except FileNotFoundError as exc:
            if not self._voice_model_error_reported:
                self._report_status("Voice", "ASR model: not installed")
                log.warning("语音模型未安装，请在模型中心下载: %s", exc)
                self._voice_model_error_reported = True
            return
        except Exception as exc:
            self._report_status("Voice", f"ASR error: {exc}")
            log.exception("语音转写失败")
            return
        if not text:
            return
        source = SourceMessage(source_type="voice", original=text)
        self.translate_and_broadcast(source)

    def handle_pcm(self, pcm_bytes: bytes) -> None:
        """输入 PCM 字节流，内部 VAD 切句后逐句处理。"""
        if not self.voice_enabled or self.audio_pipeline is None:
            return
        utterances = self.audio_pipeline.feed_pcm(pcm_bytes)
        for u in utterances:
            self.handle_utterance(u)

    # ---- 聊天主链 ----

    def handle_chat_line(self, original: str) -> None:
        """一条已去重的聊天行，翻译 + 广播。"""
        if not original or not original.strip():
            return
        source = SourceMessage(source_type="chat", original=original.strip())
        self.translate_and_broadcast(source)

    # ---- 翻译 + 广播 ----

    def translate_and_broadcast(self, source: SourceMessage) -> None:
        translated = self.translator.translate(source.original)
        result = TranslationResult(
            id=source.id,
            source_type=source.source_type,
            original=source.original,
            translated=translated,
            created_at=source.created_at,
        )
        self.pipe.broadcast(protocol.subtitle_message(result))
        log.info("[%s] %s → %s", source.source_type, source.original, translated)

    # ---- 进程发现 + 音频捕获 ----

    def _start_capture(self, target_pid: int) -> None:
        """针对 VALORANT pid 启动/重建音频捕获。"""
        if not self.voice_enabled:
            return
        if self._capture_factory is not None:
            # 注入的 reader（测试用）；复用同一个实例
            cap = self._capture_factory
            cap.on_pcm = self.handle_pcm
            try:
                cap.start(target_pid)
            except FileNotFoundError as exc:
                log.warning("无法启动 audio-capture: %s", exc)
            return

        # 默认：新建 AudioCaptureReader
        if self._capture is None:
            from audio.capture_reader import AudioCaptureReader

            self._capture = AudioCaptureReader(on_pcm=self.handle_pcm)
        try:
            self._capture.start(target_pid)
        except FileNotFoundError as exc:
            log.warning("无法启动 audio-capture: %s", exc)

    def _stop_capture(self) -> None:
        if self._capture_factory is not None:
            try:
                self._capture_factory.stop()
            except Exception:
                pass
            return
        if self._capture is not None:
            self._capture.stop()
            self._capture = None

    def start_process_monitor(self) -> None:
        if not self.voice_enabled:
            self._report_status("Voice", "ASR: disabled in this edition")
            return
        if self._finder is not None and self._finder.is_alive():
            return

        # ProcessFinder is part of the voice edition only.  Keep it out of the
        # text-only import graph as well as out of the text package's frozen
        # dependency analysis.
        from process.process_finder import ProcessFinder

        def on_change(info: "ProcessInfo | None") -> None:
            if info is None:
                self.pipe.broadcast(protocol.status_message(backend="ready", valorant="stopped"))
                self._report_status("Game", "VALORANT: stopped")
                self._stop_capture()
                log.info("VALORANT 退出，停止音频捕获")
            else:
                self.pipe.broadcast(protocol.status_message(backend="ready", valorant="running"))
                self._report_status("Game", f"VALORANT: running (pid {info.pid})")
                log.info("发现 VALORANT (pid=%d)，启动音频捕获", info.pid)
                self._start_capture(info.pid)

        self._finder = ProcessFinder(on_change=on_change)
        self._finder.start()

    def start_chat_monitor(self, roi: RoiConfig) -> bool:
        """Start or restart the screen/OCR worker for ``roi``."""
        if not self.chat_enabled:
            self._report_status("Chat", "OCR: disabled in this edition")
            return False
        self.stop_chat_monitor()
        try:
            from ocr.chat_worker import ChatOcrWorker

            self._chat_worker = ChatOcrWorker(
                roi,
                on_line=self.handle_chat_line,
                on_status=lambda status: self._report_status("Chat", status),
                **self._text_settings,
            )
            self._chat_worker.start()
        except Exception as exc:
            self._chat_worker = None
            self._report_status("Chat", f"OCR: unavailable ({exc})")
            log.exception("启动聊天 OCR 失败")
            return False
        self._report_status("Chat", "OCR: starting")
        return True

    def configure_chat_roi(self, roi: RoiConfig) -> bool:
        """Apply a newly selected ROI and persist it through the caller."""
        return self.start_chat_monitor(roi)

    def stop_chat_monitor(self) -> None:
        if self._chat_worker is not None:
            self._chat_worker.stop()
            self._chat_worker = None
            self._report_status("Chat", "OCR: stopped")

    def start_all(self, roi: RoiConfig | None = None) -> None:
        """Start process monitoring and, when configured, chat OCR."""
        if self.voice_enabled:
            if getattr(self.transcriber, "model_available", False):
                self._report_status("Voice", "ASR: ready")
            else:
                self._report_status("Voice", "ASR model: not installed")
            self.start_process_monitor()
        else:
            self._report_status("Voice", "ASR: disabled in this edition")

        if self.chat_enabled:
            if roi is not None:
                self.start_chat_monitor(roi)
            else:
                self._report_status("Chat", "OCR: ROI not configured")
        else:
            self._report_status("Chat", "OCR: disabled in this edition")

    def stop(self) -> None:
        self.stop_chat_monitor()
        self._stop_capture()
        # Audio 流结束：冲刷未完成的语音段
        if self.audio_pipeline is not None:
            try:
                for u in self.audio_pipeline.finish():
                    self.handle_utterance(u)
            except Exception:
                pass
        if self._finder is not None:
            self._finder.stop()
            self._finder = None
