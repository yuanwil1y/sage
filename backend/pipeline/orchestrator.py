"""后端编排器（规格文档第 18、33、35 节）。

把整条流水线串起来，同时把捕获热路径与慢速 ASR/翻译解耦。
"""

from __future__ import annotations

import logging
import queue
import threading
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

_STOP = object()
VOICE_QUEUE_SIZE = 8
TRANSLATION_QUEUE_SIZE = 64


class TranslatorOrchestrator:
    """编排语音与聊天两条翻译主链，统一广播到 Game Bar Widget。

    Native stdout / OCR polling remain latency-sensitive producers. Whisper
    and Hy-MT2 run on dedicated bounded workers so a slow/failing model cannot
    block capture or terminate the producer thread.
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

        # These imports are deliberately local. A text build must not pull in
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

        self._utterance_queue: queue.Queue[object] | None = (
            queue.Queue(maxsize=VOICE_QUEUE_SIZE) if self.voice_enabled else None
        )
        self._translation_queue: queue.Queue[object] = queue.Queue(
            maxsize=TRANSLATION_QUEUE_SIZE
        )
        self._worker_lock = threading.Lock()
        self._translator_lock = threading.Lock()
        self._asr_thread: threading.Thread | None = None
        self._translation_thread: threading.Thread | None = None
        self._stopping = False

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        """Subscribe a UI/status consumer to backend lifecycle updates."""
        self._status_callback = callback

    def replace_translator(self, translator: Translator) -> None:
        """Atomically use ``translator`` for subsequent queued messages."""
        with self._translator_lock:
            self.translator = translator

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

    # ---- bounded worker lifecycle ----

    def _ensure_workers_started(self) -> bool:
        with self._worker_lock:
            if self._stopping:
                return False
            if self._translation_thread is None or not self._translation_thread.is_alive():
                self._translation_thread = threading.Thread(
                    target=self._translation_loop,
                    name="TranslationWorker",
                    daemon=True,
                )
                self._translation_thread.start()
            if self.voice_enabled and (
                self._asr_thread is None or not self._asr_thread.is_alive()
            ):
                self._asr_thread = threading.Thread(
                    target=self._asr_loop,
                    name="AsrWorker",
                    daemon=True,
                )
                self._asr_thread.start()
        return True

    @staticmethod
    def _put_latest(q: queue.Queue[object], item: object, label: str) -> None:
        """Bound latency by dropping the oldest queued item on overload."""
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            pass

        try:
            q.get_nowait()
            q.task_done()
            log.warning("%s 队列已满，丢弃最旧项目以保持实时性", label)
        except queue.Empty:
            pass

        try:
            q.put_nowait(item)
        except queue.Full:
            log.warning("%s 队列持续拥塞，丢弃新项目", label)

    @staticmethod
    def _put_stop(q: queue.Queue[object]) -> None:
        while True:
            try:
                q.put_nowait(_STOP)
                return
            except queue.Full:
                try:
                    q.get_nowait()
                    q.task_done()
                except queue.Empty:
                    return

    def _asr_loop(self) -> None:
        assert self._utterance_queue is not None
        while True:
            item = self._utterance_queue.get()
            try:
                if item is _STOP:
                    return
                try:
                    self._transcribe_and_enqueue(np.asarray(item, dtype=np.float32))
                except Exception:
                    # Per-item failures must never terminate the worker itself.
                    log.exception("ASR worker 未预期异常")
            finally:
                self._utterance_queue.task_done()

    def _translation_loop(self) -> None:
        while True:
            item = self._translation_queue.get()
            try:
                if item is _STOP:
                    return
                try:
                    self.translate_and_broadcast(item)  # type: ignore[arg-type]
                except Exception:
                    log.exception("Translation worker 未预期异常")
            finally:
                self._translation_queue.task_done()

    def _stop_workers(self, timeout: float = 5.0) -> None:
        """Stop ASR first so its final results reach translation before its stop."""
        with self._worker_lock:
            self._stopping = True
            asr_thread = self._asr_thread
            translation_thread = self._translation_thread
            if self._utterance_queue is not None and asr_thread is not None:
                self._put_stop(self._utterance_queue)

        if asr_thread is not None and asr_thread is not threading.current_thread():
            asr_thread.join(timeout=timeout)
            if asr_thread.is_alive():
                log.warning("worker %s 未在 %.1fs 内退出", asr_thread.name, timeout)

        # Only after ASR has had the chance to enqueue its last SourceMessage do
        # we place the translation stop sentinel at the end of that queue.
        if translation_thread is not None:
            self._put_stop(self._translation_queue)
            if translation_thread is not threading.current_thread():
                translation_thread.join(timeout=timeout)
                if translation_thread.is_alive():
                    log.warning(
                        "worker %s 未在 %.1fs 内退出",
                        translation_thread.name,
                        timeout,
                    )

    # ---- 语音主链 ----

    def handle_utterance(self, utterance_16k: np.ndarray) -> None:
        """Enqueue a complete utterance; never run Whisper on the capture thread."""
        if not self.voice_enabled or self.transcriber is None:
            return
        audio = np.asarray(utterance_16k, dtype=np.float32).reshape(-1)
        if audio.size == 0 or not self._ensure_workers_started():
            return
        assert self._utterance_queue is not None
        self._put_latest(self._utterance_queue, audio.copy(), "ASR")

    def _transcribe_and_enqueue(self, utterance_16k: np.ndarray) -> None:
        if self.transcriber is None:
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
        # This call originates inside the already-running ASR worker. During a
        # graceful shutdown external producers are stopped, but in-flight ASR
        # output must still be allowed to drain into translation.
        self._enqueue_source(
            SourceMessage(source_type="voice", original=text),
            ensure_workers=False,
        )

    def handle_pcm(self, pcm_bytes: bytes) -> None:
        """PCM hot path: normalize/resample/VAD only; completed speech is queued."""
        if not self.voice_enabled or self.audio_pipeline is None:
            return
        try:
            utterances = self.audio_pipeline.feed_pcm(pcm_bytes)
        except Exception as exc:
            self._report_status("Voice", f"Audio pipeline error: {exc}")
            log.exception("音频管线处理失败")
            return
        for utterance in utterances:
            self.handle_utterance(utterance)

    # ---- 聊天主链 ----

    def handle_chat_line(self, original: str) -> None:
        """Queue a deduplicated OCR line; never translate on the OCR thread."""
        if not original or not original.strip():
            return
        if not self._ensure_workers_started():
            return
        self._enqueue_source(
            SourceMessage(source_type="chat", original=original.strip()),
            ensure_workers=False,
        )

    def _enqueue_source(
        self,
        source: SourceMessage,
        *,
        ensure_workers: bool = True,
    ) -> None:
        if ensure_workers and not self._ensure_workers_started():
            return
        self._put_latest(self._translation_queue, source, "翻译")

    # ---- 翻译 + 广播 ----

    def translate_and_broadcast(self, source: SourceMessage) -> bool:
        """Translate one source message without allowing failures to escape."""
        try:
            with self._translator_lock:
                translator = self.translator
            translated = translator.translate(source.original)
        except Exception as exc:
            self._report_status("Local Translation", f"Translator error: {exc}")
            log.exception("翻译失败: %s", source.original)
            return False

        result = TranslationResult(
            id=source.id,
            source_type=source.source_type,
            original=source.original,
            translated=translated,
            created_at=source.created_at,
        )
        try:
            self.pipe.broadcast(protocol.subtitle_message(result))
        except Exception as exc:
            self._report_status("Game Bar", f"IPC error: {exc}")
            log.exception("字幕广播失败")
            return False
        log.info("[%s] %s → %s", source.source_type, source.original, translated)
        return True

    # ---- 进程发现 + 音频捕获 ----

    def _start_capture(self, target_pid: int) -> None:
        """针对 VALORANT pid 启动/重建音频捕获。"""
        if not self.voice_enabled:
            return
        if self._capture_factory is not None:
            cap = self._capture_factory
            cap.on_pcm = self.handle_pcm
            try:
                cap.start(target_pid)
            except FileNotFoundError as exc:
                log.warning("无法启动 audio-capture: %s", exc)
            return

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

    def _finish_audio_stream(self) -> None:
        if self.audio_pipeline is None:
            return
        try:
            for utterance in self.audio_pipeline.finish():
                self.handle_utterance(utterance)
        except Exception:
            log.exception("音频流收尾失败")
            try:
                self.audio_pipeline.reset()
            except Exception:
                log.exception("音频管线重置失败")

    def start_process_monitor(self) -> None:
        if not self.voice_enabled:
            self._report_status("Voice", "ASR: disabled in this edition")
            return
        if self._finder is not None and self._finder.is_alive():
            return

        from process.process_finder import ProcessFinder

        def on_change(info: "ProcessInfo | None") -> None:
            if info is None:
                self.pipe.broadcast(protocol.status_message(backend="ready", valorant="stopped"))
                self._report_status("Game", "VALORANT: stopped")
                self._stop_capture()
                self._finish_audio_stream()
                log.info("VALORANT 退出，停止音频捕获")
            else:
                self.pipe.broadcast(protocol.status_message(backend="ready", valorant="running"))
                self._report_status("Game", f"VALORANT: running (pid {info.pid})")
                log.info("发现 VALORANT (pid=%d)，启动音频捕获", info.pid)
                self._stop_capture()
                self._finish_audio_stream()
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
        self._ensure_workers_started()
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
        self._finish_audio_stream()
        if self._finder is not None:
            self._finder.stop()
            self._finder = None
        self._stop_workers()
