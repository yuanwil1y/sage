"""faster-whisper 日语转写（规格文档第 25 节）。

输入：canonical audio（16 kHz mono float32, shape=(N,)）。
输出：完整日语句子。

模型：medium / cpu / int8，language="ja"，整句转写。
本模块延迟加载模型，避免 import 时即触发下载。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from paths import asr_model_dir

log = logging.getLogger(__name__)


class Transcriber:
    """faster-whisper 封装。model 可注入（测试用 fake），默认真实模型。"""

    def __init__(
        self,
        model: Any | None = None,
        *,
        model_size: str = "medium",
        device: str = "cpu",
        compute_type: str = "int8",
        model_path: Path | str | None = None,
    ) -> None:
        self._model = model
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model_path = Path(model_path) if model_path else asr_model_dir(model_size)

    def _ensure_model(self) -> Any:
        if self._model is None:
            if not self._model_path.exists():
                raise FileNotFoundError(
                    "faster-whisper 模型不存在。请打开模型管理下载 "
                    f"{self._model_size}: {self._model_path}"
                )
            from faster_whisper import WhisperModel  # 延迟 import，模型目录已先检查

            log.info(
                "加载 faster-whisper 模型: %s (%s / %s): %s",
                self._model_size,
                self._device,
                self._compute_type,
                self._model_path,
            )
            self._model = WhisperModel(
                str(self._model_path),
                device=self._device,
                compute_type=self._compute_type,
            )
        return self._model

    @property
    def model_available(self) -> bool:
        """Whether the configured local model is ready without downloading."""
        if self._model is not None:
            return True
        return all(
            (self._model_path / name).exists()
            for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
        )

    def transcribe(self, audio_16k: np.ndarray) -> str:
        """转写一段日语语音为文本（整句）。空/过短音频返回空串。"""
        # 少于约 0.2 秒（16k * 0.2 = 3200 采样）视为无效
        if audio_16k is None or audio_16k.size < 3200:
            return ""
        model = self._ensure_model()
        segments, _info = model.transcribe(
            audio_16k,
            language="ja",
            task="transcribe",
            beam_size=5,
            vad_filter=False,  # VAD 已在上游整句切分
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = "".join(seg.text for seg in segments).strip()
        return text
