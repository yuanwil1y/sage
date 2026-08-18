from __future__ import annotations

import numpy as np

from config.roi import RoiConfig
from ocr.chat_worker import ChatOcrWorker, FrameChangeDetector
from ocr.ocr_engine import OcrInitializationError


class FakeRecognizer:
    def __init__(self):
        self.calls = 0

    def recognize(self, image_bgr):
        self.calls += 1
        return (
            ["Raze:", "ミッド二人"],
            [0.99, 0.98],
            [
                [[0, 0], [30, 0], [30, 20], [0, 20]],
                [[35, 0], [90, 0], [90, 20], [35, 20]],
            ],
        )


class FakeCapture:
    def __init__(self):
        self.regions = []
        self.released = False

    def grab(self, region):
        self.regions.append(region)
        return np.zeros((40, 100, 3), dtype=np.uint8)

    def release(self):
        self.released = True


class FailingRecognizer:
    def __init__(self):
        self.calls = 0

    def recognize(self, image_bgr):
        self.calls += 1
        raise OcrInitializationError("OCR unavailable")


def test_frame_change_detector_skips_identical_frames():
    detector = FrameChangeDetector(threshold=1.0, signature_size=(8, 8))
    blank = np.zeros((32, 32, 3), dtype=np.uint8)
    changed = np.full((32, 32, 3), 30, dtype=np.uint8)

    assert detector.changed(blank)
    assert not detector.changed(blank)
    assert detector.changed(changed)


def test_chat_worker_assembles_and_deduplicates_lines():
    recognizer = FakeRecognizer()
    capture = FakeCapture()
    emitted = []
    worker = ChatOcrWorker(
        RoiConfig(0, 10, 20, 110, 120),
        on_line=emitted.append,
        capture=capture,
        recognizer=recognizer,
        min_score=0.5,
    )

    frame = np.zeros((40, 100, 3), dtype=np.uint8)
    assert worker.process_frame(frame) == ["Raze: ミッド二人"]
    assert worker.process_frame(frame) == []
    assert emitted == ["Raze: ミッド二人"]
    assert recognizer.calls == 2


def test_chat_worker_stops_after_permanent_ocr_initialization_failure(caplog):
    recognizer = FailingRecognizer()
    capture = FakeCapture()
    statuses = []
    worker = ChatOcrWorker(
        RoiConfig(0, 10, 20, 110, 120),
        on_line=lambda _: None,
        on_status=statuses.append,
        capture=capture,
        recognizer=recognizer,
    )

    worker._run()

    assert recognizer.calls == 1
    assert capture.released
    assert statuses[-1].startswith("OCR: unavailable")
    assert [record.message for record in caplog.records].count(
        "聊天 OCR 初始化失败，文字聊天功能已停止"
    ) == 1
