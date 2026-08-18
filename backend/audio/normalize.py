"""PCM → canonical audio（规格文档第 22 节）。

Native Helper 输出：PCM signed 16-bit LE / 44100 Hz / stereo / interleaved。
规范化后：44.1 kHz mono float32 1D NumPy。
"""

from __future__ import annotations

import numpy as np

BYTES_PER_FRAME = 4  # stereo 16-bit = 2ch × 2bytes


def pcm_to_mono_float32(data: bytes) -> np.ndarray:
    """stereo interleaved s16le PCM bytes → mono float32 in [-1, 1]（44.1 kHz）。

    尾部不足一帧的字节直接丢弃（防御式处理）。
    """
    usable = len(data) - (len(data) % BYTES_PER_FRAME)
    pcm = np.frombuffer(data[:usable], dtype="<i2")
    if pcm.size == 0:
        return np.zeros(0, dtype=np.float32)
    pcm = pcm.reshape(-1, 2)
    audio = pcm.astype(np.float32) / 32768.0
    return audio.mean(axis=1)
