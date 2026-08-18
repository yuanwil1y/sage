"""PCM normalize + resample 测试（规格第 22、23 节）。"""

import numpy as np

from audio.normalize import pcm_to_mono_float32
from audio.resample import CANONICAL_RATE, INPUT_RATE, StreamResampler


def _stereo_s16le(left: np.ndarray, right: np.ndarray) -> bytes:
    interleaved = np.empty(left.size * 2, dtype="<i2")
    interleaved[0::2] = (left * 32767).astype("<i2")
    interleaved[1::2] = (right * 32767).astype("<i2")
    return interleaved.tobytes()


def test_pcm_to_mono_float32() -> None:
    n = 4410  # 0.1s @44.1k
    left = np.full(n, 0.5, dtype=np.float64)
    right = np.full(n, -0.5, dtype=np.float64)
    mono = pcm_to_mono_float32(_stereo_s16le(left, right))
    assert mono.dtype == np.float32
    assert mono.shape == (n,)
    assert np.allclose(mono, 0.0, atol=1e-3)  # (0.5 + -0.5) / 2


def test_pcm_trailing_partial_frame_is_dropped() -> None:
    data = _stereo_s16le(np.array([0.1, 0.2]), np.array([0.1, 0.2])) + b"\x00\x01"
    mono = pcm_to_mono_float32(data)
    assert mono.shape == (2,)


def test_pcm_empty_input() -> None:
    assert pcm_to_mono_float32(b"").size == 0


def test_resampler_rate_and_duration() -> None:
    resampler = StreamResampler()
    seconds = 1.0
    x = np.sin(2 * np.pi * 440 * np.arange(int(INPUT_RATE * seconds)) / INPUT_RATE)
    x = x.astype(np.float32)

    out = resampler.process(x)
    assert out.dtype == np.float32
    # soxr 流式 warm-up 会使输出采样略少（约 2~3%），故容忍 4%
    assert abs(out.size - CANONICAL_RATE) <= CANONICAL_RATE * 0.04


def test_resampler_is_streaming_stateful() -> None:
    """分片输入与整体输入的输出时长应一致（状态在内部保留）。"""
    r1, r2 = StreamResampler(), StreamResampler()
    x = (np.sin(np.arange(44100, dtype=np.float32) * 0.01) * 0.5).astype(np.float32)

    whole = r1.process(x)
    parts = np.concatenate(
        [r2.process(x[:10000]), r2.process(x[10000:30000]), r2.process(x[30000:])]
    )
    assert abs(whole.size - parts.size) <= 2  # 允许边界处 ±1 帧差异
