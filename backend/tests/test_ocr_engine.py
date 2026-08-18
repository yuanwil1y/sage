from __future__ import annotations

import sys
from types import ModuleType

import pytest

from ocr.ocr_engine import OcrEngine, OcrInitializationError


def test_ocr_engine_caches_permanent_initialization_failure(tmp_path, monkeypatch):
    detector = tmp_path / "detector"
    recognizer = tmp_path / "recognizer"
    detector.mkdir()
    recognizer.mkdir()
    (detector / "inference.onnx").write_bytes(b"onnx")
    (recognizer / "inference.onnx").write_bytes(b"onnx")
    calls = []
    paddleocr = ModuleType("paddleocr")

    def fail_to_initialize(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("missing frozen dependency metadata")

    paddleocr.PaddleOCR = fail_to_initialize
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    engine = OcrEngine(
        detector_model_dir=detector,
        recognizer_model_dir=recognizer,
    )

    with pytest.raises(OcrInitializationError) as first:
        engine._ensure()
    with pytest.raises(OcrInitializationError) as second:
        engine._ensure()

    assert first.value is second.value
    assert len(calls) == 1
