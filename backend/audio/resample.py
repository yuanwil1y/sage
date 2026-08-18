"""流式重采样（规格文档第 23 节）。

python-soxr: 44.1 kHz → 16 kHz，输出 canonical audio（mono float32, shape=(N,)）。
"""

from __future__ import annotations

import numpy as np
import soxr

INPUT_RATE = 44100
CANONICAL_RATE = 16000


class StreamResampler:
    """有状态的流式重采样器：feed 任意长度 44.1k 片段，取回 16k 片段。"""

    def __init__(
        self,
        input_rate: int = INPUT_RATE,
        output_rate: int = CANONICAL_RATE,
        quality: str = "HQ",
    ) -> None:
        self._resampler = soxr.ResampleStream(
            input_rate,
            output_rate,
            1,
            dtype="float32",
            quality=quality,
        )

    def process(self, audio_44k: np.ndarray) -> np.ndarray:
        """输入 mono float32 @44.1k，输出 mono float32 @16k。"""
        if audio_44k.size == 0:
            return np.zeros(0, dtype=np.float32)
        return self._resampler.resample_chunk(audio_44k)
