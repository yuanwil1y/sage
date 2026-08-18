"""UtteranceSegmenter 状态机测试（规格第 24 节）。

用 ScriptedModel 注入每帧概率，纯离线验证切分逻辑：
- 约 800ms 连续静音 → 整句结束
- 200~500ms 停顿不切句
- speech_pad 前后保留
- 超过 20s 强制切分
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
    speech_frames = 10  # 320ms 语音
    silence_frames = round(800 / FRAME_MS)  # 25 帧静音
    utterances = _feed(seg, _probs(speech_frames, silence_frames))

    assert len(utterances) == 1
    # 句长 = 语音帧 + 静音帧 - pad(0)；emit 时 silence_frames(25) > pad(0)
    # 截掉全部尾部静音 → 恰好 10 帧
    assert utterances[0].shape[0] == speech_frames * FRAME_SIZE


def test_short_pause_does_not_split() -> None:
    """语音中间 384ms（12 帧）停顿 < 800ms：应保持整句。"""
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=0,
    )
    probs = [0.9] * 5 + [0.1] * 12 + [0.9] * 5 + [0.1] * 25
    utterances = _feed(seg, probs)
    assert len(utterances) == 1
    assert utterances[0].shape[0] == (5 + 12 + 5) * FRAME_SIZE  # 尾部 25 帧静音被截掉


def test_speech_pad_keeps_context() -> None:
    """speech_pad=300ms：句首/句尾各保留约 300ms。"""
    pad_frames = round(300 / FRAME_MS)  # ≈ 9
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=300,
    )
    lead_silence = 4  # 语音前有 4 帧静音（< pad_frames）
    probs = [0.1] * lead_silence + _probs(10, 25)
    utterances = _feed(seg, probs)

    assert len(utterances) == 1
    # 句首：pre-roll 全部 4 帧 + 语音 10 帧 + 句尾 pad 9 帧
    expected = (lead_silence + 10 + pad_frames) * FRAME_SIZE
    assert utterances[0].shape[0] == expected


def test_max_utterance_force_split() -> None:
    """max_utterance_ms=320（10 帧）：超长语音被强制切分。"""
    seg = UtteranceSegmenter(
        model=ScriptedModel([]),
        threshold=0.5,
        min_silence_duration_ms=800,
        speech_pad_ms=0,
        max_utterance_ms=320,
    )
    utterances = _feed(seg, _probs(25, 0))  # 25 帧连续语音
    # 第 10 帧时触发强制切分，第 20 帧再次触发 → 3 段
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
    _feed(seg, [0.9] * 30)  # 连续语音，未静音结尾
    partial = seg.finish()
    assert len(partial) == 1
    assert partial[0].shape[0] == 30 * FRAME_SIZE
    assert not seg.in_speech
