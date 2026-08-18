"""模型中心和本地模型路径测试。"""

from __future__ import annotations

import numpy as np
import pytest

from audio.transcriber import Transcriber
from model_store import OCR_DET, OCR_REC, WHISPER_MEDIUM, HY_MT2, model_status


def test_embedded_ocr_models_are_available() -> None:
    assert model_status(OCR_DET) == "embedded"
    assert model_status(OCR_REC) == "embedded"


def test_downloaded_models_are_missing_without_user_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VT_MODELS_DIR", str(tmp_path / "models"))

    assert model_status(HY_MT2) == "missing"
    assert model_status(WHISPER_MEDIUM) == "missing"


def test_transcriber_does_not_download_missing_model(tmp_path) -> None:
    model_path = tmp_path / "missing-asr"
    transcriber = Transcriber(model_path=model_path)

    assert transcriber.model_available is False
    with pytest.raises(FileNotFoundError, match="faster-whisper"):
        transcriber.transcribe(np.zeros(16_000, dtype=np.float32))
