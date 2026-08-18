"""音频管线组合（规格文档第 18 节 AudioPipeline）。

Native PCM bytes → mono float32(44.1k) → 16k canonical → VAD → utterances
"""

from __future__ import annotations

import logging

import numpy as np

from audio.normalize import StreamingPcmNormalizer
from audio.resample import StreamResampler
from audio.vad import UtteranceSegmenter

log = logging.getLogger(__name__)


class AudioPipeline:
    """把 Native Helper 的 PCM 字节流加工成完整语音段。"""

    def __init__(
        self,
        segmenter: UtteranceSegmenter | None = None,
        *,
        vad_threshold: float = 0.50,
        min_silence_ms: int = 800,
    ) -> None:
        self._normalizer = StreamingPcmNormalizer()
        self._resampler = StreamResampler()
        self._segmenter = segmenter or UtteranceSegmenter(
            threshold=vad_threshold,
            min_silence_duration_ms=min_silence_ms,
        )

    @property
    def segmenter(self) -> UtteranceSegmenter:
        return self._segmenter

    def configure(self, *, vad_threshold: float, min_silence_ms: int) -> None:
        """Apply voice settings to the live segmenter."""

        self._segmenter.configure(
            threshold=vad_threshold,
            min_silence_duration_ms=min_silence_ms,
        )

    def feed_pcm(self, pcm_bytes: bytes) -> list[np.ndarray]:
        """输入任意边界的 PCM s16le stereo 44.1k 字节，输出新完成的 utterances。"""
        mono_44k = self._normalizer.feed(pcm_bytes)
        audio_16k = self._resampler.process(mono_44k)
        return self._segmenter.process(audio_16k)

    def finish(self) -> list[np.ndarray]:
        """Flush all stream stages and leave the pipeline ready for a new capture."""
        utterances: list[np.ndarray] = []
        tail_16k = self._resampler.finish()
        if tail_16k.size:
            utterances.extend(self._segmenter.process(tail_16k))
        utterances.extend(self._segmenter.finish())

        dropped = self._normalizer.finish()
        if dropped:
            log.warning("音频流结束时丢弃 %d 个不完整 PCM 帧字节", dropped)
        return utterances

    def reset(self) -> None:
        """Discard all pending stream state without emitting a partial utterance."""
        self._normalizer.reset()
        self._resampler.reset()
        self._segmenter.reset()
