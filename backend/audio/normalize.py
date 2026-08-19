"""PCM → canonical audio（规格文档第 22 节）。

Native Helper 输出：PCM signed 16-bit LE / 44100 Hz / stereo / interleaved。
规范化后：44.1 kHz mono float32 1D NumPy。
"""

from __future__ import annotations

import numpy as np

BYTES_PER_FRAME = 4  # stereo 16-bit = 2ch × 2bytes


def pcm_to_mono_float32(data: bytes) -> np.ndarray:
    """Convert complete stereo interleaved s16le PCM frames to mono float32.

    This helper is intentionally stateless. Streaming callers should use
    :class:`StreamingPcmNormalizer` so a pipe read that ends in the middle of a
    stereo frame is carried into the next call instead of being discarded.
    """
    usable = len(data) - (len(data) % BYTES_PER_FRAME)
    pcm = np.frombuffer(data[:usable], dtype="<i2")
    if pcm.size == 0:
        return np.zeros(0, dtype=np.float32)
    pcm = pcm.reshape(-1, 2)
    audio = pcm.astype(np.float32) / 32768.0
    return audio.mean(axis=1)


class StreamingPcmNormalizer:
    """Frame arbitrary PCM byte chunks without losing partial stereo frames."""

    def __init__(self) -> None:
        self._remainder = b""

    @property
    def pending_bytes(self) -> int:
        return len(self._remainder)

    def feed(self, data: bytes) -> np.ndarray:
        if not data and not self._remainder:
            return np.zeros(0, dtype=np.float32)

        buffered = self._remainder + data
        usable = len(buffered) - (len(buffered) % BYTES_PER_FRAME)
        self._remainder = buffered[usable:]
        return pcm_to_mono_float32(buffered[:usable])

    def finish(self) -> int:
        """Clear an incomplete final frame and return the dropped byte count."""
        dropped = len(self._remainder)
        self._remainder = b""
        return dropped

    def reset(self) -> None:
        self._remainder = b""
