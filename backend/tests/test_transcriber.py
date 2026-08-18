"""faster-whisper 转写测试（规格第 25 节）。

用 FakeModel 注入，避免下载真实模型；验证：
- 日语参数正确传递
- 短音频返回空
- 多 segment 拼接
"""

import numpy as np

from audio.transcriber import Transcriber


class FakeSegments:
    def __init__(self, items):
        self._items = items
        self._i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._items):
            raise StopIteration
        s = self._items[self._i]
        self._i += 1
        return s


class FakeModel:
    def __init__(self):
        self.last_kwargs = None

    def transcribe(self, audio, **kwargs):
        self.last_kwargs = kwargs
        return FakeSegments([type("Seg", (), {"text": "ジェットロー"})] * 2), None


def _fake_audio(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def test_transcribe_returns_joined_japanese() -> None:
    model = FakeModel()
    tc = Transcriber(model=model)
    out = tc.transcribe(_fake_audio(1.0))
    assert out == "ジェットロージェットロー"


def test_transcribe_passes_japanese_args() -> None:
    model = FakeModel()
    tc = Transcriber(model=model)
    tc.transcribe(_fake_audio(1.0))
    kw = model.last_kwargs
    assert kw["language"] == "ja"
    assert kw["task"] == "transcribe"
    assert kw["vad_filter"] is False
    assert kw["condition_on_previous_text"] is False
    assert kw["without_timestamps"] is True


def test_short_audio_returns_empty() -> None:
    model = FakeModel()
    tc = Transcriber(model=model)
    assert tc.transcribe(_fake_audio(0.1)) == ""
    assert tc.transcribe(np.zeros(100, dtype=np.float32)) == ""
