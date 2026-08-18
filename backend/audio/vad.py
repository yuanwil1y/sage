"""Silero VAD 整句切分（规格文档第 24 节）。

输入：16 kHz mono float32 流式片段。
输出：完整 utterances（16k mono float32 ndarray）。

参数（规格初始值）：
- threshold:               0.50
- min_silence_duration_ms: 800   （连续静音约 800ms 判定整句结束）
- speech_pad_ms:           300   （语音前后各保留约 300ms 上下文）
- max_utterance_ms:        20000 （超长强制切分）
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import numpy as np

from audio.onnx_vad import OnnxVadModel

log = logging.getLogger(__name__)

FRAME_SIZE = 512  # 16 kHz 下 = 32 ms/帧
FRAME_MS = FRAME_SIZE * 1000 / 16000  # 32 ms


class VadModel(Protocol):
    """Silero 模型的最小接口：返回该帧为语音的概率 [0,1]。"""

    def __call__(self, frame: np.ndarray, sampling_rate: int) -> Any:
        ...


class UtteranceSegmenter:
    """流式整句切分器。model 可注入（测试时用 fake）。"""

    def __init__(
        self,
        model: VadModel | None = None,
        *,
        threshold: float = 0.50,
        min_silence_duration_ms: int = 800,
        speech_pad_ms: int = 300,
        max_utterance_ms: int = 20000,
    ) -> None:
        if model is None:
            model = OnnxVadModel()

        self.model = model
        self.threshold = threshold
        self.min_silence_frames = max(1, round(min_silence_duration_ms / FRAME_MS))
        self.pad_frames = max(0, round(speech_pad_ms / FRAME_MS))
        self.max_frames = max(1, max_utterance_ms // int(FRAME_MS))
        self.reset()

    def reset(self) -> None:
        self._in_speech = False
        self._pre_roll: list[np.ndarray] = []
        self._buf: list[np.ndarray] = []
        self._silence_frames = 0
        self._remainder = np.zeros(0, dtype=np.float32)
        reset_states = getattr(self.model, "reset_states", None)
        if callable(reset_states):
            reset_states()

    def configure(
        self,
        *,
        threshold: float | None = None,
        min_silence_duration_ms: int | None = None,
    ) -> None:
        """Update tunable voice settings without replacing the VAD model."""

        if threshold is not None:
            if not 0.0 <= float(threshold) <= 1.0:
                raise ValueError("threshold must be between 0 and 1")
            self.threshold = float(threshold)
        if min_silence_duration_ms is not None:
            if int(min_silence_duration_ms) <= 0:
                raise ValueError("min_silence_duration_ms must be positive")
            self.min_silence_frames = max(
                1, round(int(min_silence_duration_ms) / FRAME_MS)
            )

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def process(self, audio_16k: np.ndarray) -> list[np.ndarray]:
        """Feed arbitrary-sized 16 kHz chunks without dropping frame remainders."""
        audio = np.asarray(audio_16k, dtype=np.float32).reshape(-1)
        if self._remainder.size:
            audio = np.concatenate([self._remainder, audio])

        n_frames = audio.size // FRAME_SIZE
        usable = n_frames * FRAME_SIZE
        self._remainder = audio[usable:].copy()

        utterances: list[np.ndarray] = []
        for i in range(n_frames):
            frame = audio[i * FRAME_SIZE : (i + 1) * FRAME_SIZE]
            is_speech = self._is_speech(frame)
            done = self._advance(frame, is_speech)
            if done is not None:
                utterances.append(done)
        return utterances

    def finish(self) -> list[np.ndarray]:
        """Flush a partial stream, including a final sub-512-sample speech tail."""
        utterances: list[np.ndarray] = []
        # A sub-frame tail cannot be classified by Silero. Preserve it only when
        # the last classified VAD frame was speech. If we are already inside a
        # trailing-silence run, treating unknown tail samples as speech would
        # re-introduce silence that _emit() is intentionally trimming.
        if self._remainder.size and self._in_speech and self._silence_frames == 0:
            self._buf.append(self._remainder.copy())
        self._remainder = np.zeros(0, dtype=np.float32)

        if self._in_speech and self._buf:
            utterances.append(self._emit())
        self.reset()
        return utterances

    def _is_speech(self, frame: np.ndarray) -> bool:
        prob = float(self.model(frame[None, :], 16000))
        return prob >= self.threshold

    def _advance(self, frame: np.ndarray, is_speech: bool) -> np.ndarray | None:
        if not self._in_speech:
            if not is_speech:
                self._pre_roll.append(frame)
                if len(self._pre_roll) > self.pad_frames:
                    self._pre_roll.pop(0)
                return None
            self._in_speech = True
            self._buf = list(self._pre_roll) + [frame]
            self._silence_frames = 0
            return None

        self._buf.append(frame)
        if is_speech:
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            if self._silence_frames >= self.min_silence_frames:
                return self._emit()

        if len(self._buf) >= self.max_frames:
            log.debug("utterance 达到 %d 帧上限，强制切分", self.max_frames)
            return self._emit()
        return None

    def _emit(self) -> np.ndarray:
        if self._silence_frames > self.pad_frames:
            keep = len(self._buf) - self._silence_frames + self.pad_frames
            audio = np.concatenate(self._buf[:keep])
        else:
            audio = np.concatenate(self._buf)

        if self.pad_frames > 0:
            tail = self._buf[-self.pad_frames :]
            self._pre_roll = list(tail)
        else:
            self._pre_roll = []
        self._buf = []
        self._silence_frames = 0
        self._in_speech = False
        return audio
