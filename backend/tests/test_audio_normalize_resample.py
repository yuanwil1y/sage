"""PCM normalize + resample 测试（规格第 22、23 节）。"""

import numpy as np

from audio.normalize import StreamingPcmNormalizer, pcm_to_mono_float32
from audio.pipeline import AudioPipeline
from audio.resample import CANONICAL_RATE, INPUT_RATE, StreamResampler


def _stereo_s16le(left: np.ndarray, right: np.ndarray) -> bytes:
    interleaved = np.empty(left.size * 2, dtype="<i2")
    interleaved[0::2] = (left * 32767).astype("<i2")
    interleaved[1::2] = (right * 32767).astype("<i2")
    return interleaved.tobytes()


def test_pcm_to_mono_float32() -> None:
    n = 4410
    left = np.full(n, 0.5, dtype=np.float64)
    right = np.full(n, -0.5, dtype=np.float64)
    mono = pcm_to_mono_float32(_stereo_s16le(left, right))
    assert mono.dtype == np.float32
    assert mono.shape == (n,)
    assert np.allclose(mono, 0.0, atol=1e-3)


def test_pcm_trailing_partial_frame_is_dropped_by_stateless_helper() -> None:
    data = _stereo_s16le(np.array([0.1, 0.2]), np.array([0.1, 0.2])) + b"\x00\x01"
    mono = pcm_to_mono_float32(data)
    assert mono.shape == (2,)


def test_streaming_pcm_preserves_random_chunk_boundaries() -> None:
    left = np.linspace(-0.9, 0.9, 2000, dtype=np.float64)
    right = np.linspace(0.8, -0.8, 2000, dtype=np.float64)
    data = _stereo_s16le(left, right)
    expected = pcm_to_mono_float32(data)

    rng = np.random.default_rng(20260819)
    normalizer = StreamingPcmNormalizer()
    chunks: list[np.ndarray] = []
    offset = 0
    while offset < len(data):
        size = int(rng.integers(1, 65))
        chunks.append(normalizer.feed(data[offset : offset + size]))
        offset += size

    assert normalizer.finish() == 0
    actual = np.concatenate([chunk for chunk in chunks if chunk.size])
    np.testing.assert_array_equal(actual, expected)


def test_streaming_pcm_reports_incomplete_final_frame() -> None:
    normalizer = StreamingPcmNormalizer()
    assert normalizer.feed(b"\x01\x02\x03").size == 0
    assert normalizer.pending_bytes == 3
    assert normalizer.finish() == 3
    assert normalizer.pending_bytes == 0


def test_pcm_empty_input() -> None:
    assert pcm_to_mono_float32(b"").size == 0


def test_resampler_rate_and_duration() -> None:
    resampler = StreamResampler()
    seconds = 1.0
    x = np.sin(2 * np.pi * 440 * np.arange(int(INPUT_RATE * seconds)) / INPUT_RATE)
    x = x.astype(np.float32)

    out = resampler.process(x)
    assert out.dtype == np.float32
    assert abs(out.size - CANONICAL_RATE) <= CANONICAL_RATE * 0.04


def test_resampler_is_streaming_stateful() -> None:
    r1, r2 = StreamResampler(), StreamResampler()
    x = (np.sin(np.arange(44100, dtype=np.float32) * 0.01) * 0.5).astype(np.float32)

    whole = r1.process(x)
    parts = np.concatenate(
        [r2.process(x[:10000]), r2.process(x[10000:30000]), r2.process(x[30000:])]
    )
    assert abs(whole.size - parts.size) <= 2


def test_resampler_finish_flushes_tail_and_resets_stream() -> None:
    x = (np.sin(np.arange(44100, dtype=np.float32) * 0.01) * 0.5).astype(np.float32)
    resampler = StreamResampler()

    first = np.concatenate([resampler.process(x), resampler.finish()])
    second = np.concatenate([resampler.process(x), resampler.finish()])

    assert abs(first.size - CANONICAL_RATE) <= 2
    np.testing.assert_array_equal(second, first)


class _ResetProbe:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1


class _SegmenterResetProbe(_ResetProbe):
    def configure(self, **kwargs) -> None:
        pass

    def process(self, audio):
        return []

    def finish(self):
        return []


def test_empty_pcm_marker_resets_every_stream_stage() -> None:
    segmenter = _SegmenterResetProbe()
    pipeline = AudioPipeline(segmenter=segmenter)
    normalizer = _ResetProbe()
    resampler = _ResetProbe()
    pipeline._normalizer = normalizer
    pipeline._resampler = resampler

    assert pipeline.feed_pcm(b"") == []
    assert normalizer.reset_count == 1
    assert resampler.reset_count == 1
    assert segmenter.reset_count == 1
