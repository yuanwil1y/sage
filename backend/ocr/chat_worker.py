"""Real-time screen/OCR worker for the VALORANT chat region.

The worker intentionally has no game-specific knowledge. It samples one
configured DXcam region, skips unchanged frames, OCRs the newest changed
frame, assembles visual fragments into lines, and emits only new lines. The
translation and IPC layers remain owned by ``TranslatorOrchestrator``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Protocol, Sequence

import numpy as np

from config.roi import RoiConfig
from ocr.dedup import OcrDeduper
from ocr.line_assembler import OcrFragment, assemble_line
from ocr.ocr_engine import OcrEngine, OcrInitializationError
from screen.screen_capture import ScreenCapture

log = logging.getLogger("ocr.chat_worker")


class FrameCapture(Protocol):
    def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
        ...

    def release(self) -> None:
        ...


class FrameRecognizer(Protocol):
    def recognize(
        self, image_bgr: np.ndarray
    ) -> tuple[list[str], list[float], list[Sequence[Sequence[float]]]]:
        ...


class FrameChangeDetector:
    """Detect meaningful changes using a small grayscale ROI signature."""

    def __init__(
        self,
        *,
        threshold: float = 2.0,
        signature_size: tuple[int, int] = (64, 32),
    ) -> None:
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        width, height = signature_size
        if width <= 0 or height <= 0:
            raise ValueError("signature_size must be positive")
        self.threshold = threshold
        self.signature_size = signature_size
        self._previous: np.ndarray | None = None

    def reset(self) -> None:
        self._previous = None

    def changed(self, frame: np.ndarray) -> bool:
        signature = self._signature(frame)
        previous = self._previous
        self._previous = signature
        if previous is None:
            return True
        difference = float(np.mean(np.abs(signature - previous)))
        return difference >= self.threshold

    def _signature(self, frame: np.ndarray) -> np.ndarray:
        image = np.asarray(frame)
        if image.ndim == 3:
            if image.shape[2] == 0:
                raise ValueError("frame has no colour channels")
            gray = image.astype(np.float32).mean(axis=2)
        elif image.ndim == 2:
            gray = image.astype(np.float32)
        else:
            raise ValueError(f"expected 2D/3D frame, got shape {image.shape}")
        if gray.size == 0:
            raise ValueError("frame is empty")

        width, height = self.signature_size
        try:
            import cv2

            return cv2.resize(
                gray,
                (width, height),
                interpolation=cv2.INTER_AREA,
            ).astype(np.float32, copy=False)
        except ImportError:
            y_idx = np.linspace(0, gray.shape[0] - 1, height).astype(int)
            x_idx = np.linspace(0, gray.shape[1] - 1, width).astype(int)
            return gray[np.ix_(y_idx, x_idx)].astype(np.float32, copy=False)


class ChatOcrWorker:
    """Poll a configured ROI and emit newly observed chat lines."""

    def __init__(
        self,
        region: RoiConfig,
        *,
        on_line: Callable[[str], None],
        capture: FrameCapture | None = None,
        recognizer: FrameRecognizer | None = None,
        deduper: OcrDeduper | None = None,
        on_status: Callable[[str], None] | None = None,
        poll_hz: float = 4.0,
        change_threshold: float = 2.0,
        min_score: float = 0.5,
    ) -> None:
        if poll_hz <= 0:
            raise ValueError("poll_hz must be positive")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")
        self.region = region
        self.on_line = on_line
        self.on_status = on_status
        self.poll_hz = poll_hz
        self.min_score = min_score
        self.capture: FrameCapture = capture or ScreenCapture(region.output_idx)
        self.recognizer: FrameRecognizer = recognizer or OcrEngine(lang="japan")
        self.deduper = deduper or OcrDeduper(threshold=90.0)
        self.change_detector = FrameChangeDetector(threshold=change_threshold)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self.change_detector.reset()
        self.deduper.reset()
        self._thread = threading.Thread(
            target=self._run,
            name="ChatOcrWorker",
            daemon=True,
        )
        self._thread.start()

    def configure(
        self,
        *,
        poll_hz: float | None = None,
        min_score: float | None = None,
        change_threshold: float | None = None,
    ) -> None:
        """Apply OCR tuning values to a running or future worker."""
        if poll_hz is not None:
            if float(poll_hz) <= 0:
                raise ValueError("poll_hz must be positive")
            self.poll_hz = float(poll_hz)
        if min_score is not None:
            if not 0 <= float(min_score) <= 1:
                raise ValueError("min_score must be between 0 and 1")
            self.min_score = float(min_score)
        if change_threshold is not None:
            self.change_detector.threshold = max(0.0, float(change_threshold))

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None
        try:
            self.capture.release()
        except Exception:
            log.exception("释放 DXcam 失败")

    def process_frame(self, frame: np.ndarray) -> list[str]:
        """OCR one frame and return the newly appended visible chat lines."""
        texts, scores, boxes = self.recognizer.recognize(frame)
        fragments: list[OcrFragment] = []
        for index, text in enumerate(texts):
            text = str(text).strip()
            if not text:
                continue
            score = float(scores[index]) if index < len(scores) else 1.0
            if score < self.min_score or index >= len(boxes):
                continue
            box = boxes[index]
            if box is None or len(box) == 0 or any(len(point) < 2 for point in box):
                continue
            fragments.append(OcrFragment(text=text, box=box, score=score))

        visible_lines = [line.strip() for line in assemble_line(fragments) if line.strip()]
        new_lines = self.deduper.filter_new(visible_lines)

        emitted: list[str] = []
        for line in new_lines:
            try:
                self.on_line(line)
            except Exception:
                log.exception("聊天行回调失败: %s", line)
                continue
            emitted.append(line)
        if emitted:
            self._report_status(f"OCR: {len(emitted)} new line(s)")
        return emitted

    def _run(self) -> None:
        final_status = "OCR: stopped"
        self._report_status("OCR: running")
        try:
            while not self._stop_event.is_set():
                started = time.monotonic()
                try:
                    frame = self.capture.grab(self.region.region)
                    if self.change_detector.changed(frame):
                        self.process_frame(frame)
                except OcrInitializationError as exc:
                    log.exception("聊天 OCR 初始化失败，文字聊天功能已停止")
                    final_status = f"OCR: unavailable ({exc})"
                    break
                except Exception as exc:
                    log.exception("聊天 OCR 轮询失败")
                    self._report_status(f"OCR: error ({exc})")
                elapsed = time.monotonic() - started
                # poll_hz may be changed from the UI while this worker is live.
                interval = 1.0 / self.poll_hz
                self._stop_event.wait(max(0.0, interval - elapsed))
        finally:
            try:
                self.capture.release()
            except Exception:
                log.exception("释放 DXcam 失败")
            self._report_status(final_status)

    def _report_status(self, status: str) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(status)
        except Exception:
            log.exception("OCR 状态回调失败")
