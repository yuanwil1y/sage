"""UtteranceSegmenter 状态机测试（规格第 24 节）。

用 ScriptedModel 注入每帧概率，纯离线验证切分逻辑：
- 约 800ms 连续静音 → 整句结束
- 200~500ms 停顿不切句
- speech_pad 前后保留
- 超过 20s 强制切分
- 任意输入 chunk 边界不丢失 512-sample VAD remainder
"""

import numpy as np

from audio.vad import FRAME_SIZE, UtteranceSegmenter

SAMPLE_RATE = 16000
FRAME_MS = FRAME_SIZE * 1000 / SAMPLE_RATE  # 32


class ScriptedModel:
    """按帧序返回预设概率的 fake VAD 模型。"""

    def __init__(self, probs: list[float]) -> None:
        self._probs = probs
        self._i = 0

    def __call__(self, frame: np.ndarray, sampling_rate: int) -> float:
        p = self._probs[min(self._i, len(self._probs) - 1)]
        self._i += 1
        return p


def _frames(n: int, start_value: float = 0.1) -> np.ndarray:
    """生成 n 帧一维 canonical audio（16k mono float32, shape=(n*512,)）。"""
    return np.arange(n * FRAME_SIZE, dtype=np.float32) / 1e6 + start_value


def _probs(speech: int, silence: int) -> list[float]:
    return [0.9] * speech + [0.1] * silence


def _feed(segmenter: UtteranceSegmenter, probs: list[float]) -> list[np.ndarray]:
    model = ScriptedModel(probs)
    segmenter.model = model
    return segmenter.process(_frames(len(probs)))


def test_silence_800ms_ends_utterance() -> None:
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    speech_frames = 10
    silence_frames = round(800 / FRAME_MS)
    utterances = _feed(seg, _probs(speech_frames, silence_frames))

    assert len(utterances) == 1
    assert utterances[0].shape[0] == speech_frames * FRAME_SIZE


def test_short_pause_does_not_split() -> None:
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    probs = [0.9] * 5 + [0.1] * 12 + [0.9] * 5 + [0.1] * 25
    utterances = _feed(seg, probs)
    assert len(utterances) == 1
    assert utterances[0].shape[0] == (5 + 12 + 5) * FRAME_SIZE


def test_speech_pad_keeps_context() -> None:
    pad_frames = round(300 / FRAME_MS)
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=300,
    )
    lead_silence = 4
    probs = [0.1] * lead_silence + _probs(10, 25)
    utterances = _feed(seg, probs)

    assert len(utterances) == 1
    expected = (lead_silence + 10 + pad_frames) * FRAME_SIZE
    assert utterances[0].shape[0] == expected


def test_max_utterance_force_split() -> None:
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=0,
        max_utterance_ms=320,
    )
    utterances = _feed(seg, _probs(25, 0))
    assert len(utterances) >= 2
    for u in utterances:
        assert u.shape[0] == 10 * FRAME_SIZE


def test_finish_emits_partial_speech() -> None:
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    _feed(seg, [0.9] * 30)
    partial = seg.finish()
    assert len(partial) == 1
    assert partial[0].shape[0] == 30 * FRAME_SIZE
    assert not seg.in_speech


def test_random_chunk_boundaries_match_one_shot_segmentation() -> None:
    probs = [0.9] * 8 + [0.1] * 25 + [0.9] * 6 + [0.1] * 25
    audio = _frames(len(probs))

    one_shot = UtteranceSegmenter(
        model=ScriptedModel(probs.copy()),
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    expected = one_shot.process(audio) + one_shot.finish()

    chunked = UtteranceSegmenter(
        model=ScriptedModel(probs.copy()),
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    rng = np.random.default_rng(20260819)
    actual: list[np.ndarray] = []
    offset = 0
    while offset < audio.size:
        size = int(rng.integers(1, 1300))
        actual.extend(chunked.process(audio[offset : offset + size]))
        offset += size
    actual.extend(chunked.finish())

    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        np.testing.assert_array_equal(got, want)


def test_finish_keeps_subframe_tail_when_speech_is_active() -> None:
    probs = [0.9]
    seg = UtteranceSegmenter(
        model=ScriptedModel(probs),
        speech_pad_ms=0,
    )
    audio = np.ones(FRAME_SIZE + 123, dtype=np.float32)

    assert seg.process(audio) == []
    partial = seg.finish()

    assert len(partial) == 1
    assert partial[0].shape[0] == FRAME_SIZE + 123


def test_finish_does_not_reintroduce_unclassified_tail_after_silence() -> None:
    seg = UtteranceSegmenter(
        model=ScriptedModel([0.9, 0.1]),
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    audio = np.ones(FRAME_SIZE * 2 + 123, dtype=np.float32)

    assert seg.process(audio) == []
    partial = seg.finish()

    assert len(partial) == 1
    # One speech frame is kept; the classified silence frame and the unknown
    # sub-frame tail after it are both trimmed.
    assert partial[0].shape[0] == FRAME_SIZE
