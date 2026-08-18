"""Silero VAD 整句切分（规格文档第 24 节）。

输入：16 kHz mono float32 流式片段。
输出：完整 utterances（16k mono float32 ndarray）。

参数（规格初始值）：
- threshold:               0.50
- min_silence_duration_ms: 800   （连续静音约 800ms 判定整句结束）
- speech_pad_ms:           300   （语音前后各保留约 300ms 上下文）
- max_utterance_ms:        20000 （超长强制切分）

状态机：
    silence ──speech──▶ speech（预滚 speech_pad 帧加入句首）
    speech  ──silence≥800ms──▶ 句结束（保留 speech_pad 尾部静音）
    speech  ──超 20s──▶ 强制切分
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import numpy as np

from audio.onnx_vad import OnnxVadModel

log = logging.getLogger(__name__)

FRAME_SIZE = 512  # 16 kHz 下 = 32 ms/帧

# Silero VAD 内部以 512-sample 窗口处理，模型输入恰好一帧
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

    # ---- 状态 ----

    def reset(self) -> None:
        self._in_speech = False
        self._pre_roll: list[np.ndarray] = []  # 语音开始前保留的静音帧
        self._buf: list[np.ndarray] = []       # 当前 utterance 的音频帧
        self._silence_frames = 0

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

    # ---- 主入口 ----

    def process(self, audio_16k: np.ndarray) -> list[np.ndarray]:
        """喂入一段 16k mono float32 音频，返回完整切分出的 utterances。"""
        utterances: list[np.ndarray] = []
        n_frames = audio_16k.size // FRAME_SIZE
        for i in range(n_frames):
            frame = audio_16k[i * FRAME_SIZE : (i + 1) * FRAME_SIZE]
            is_speech = self._is_speech(frame)
            done = self._advance(frame, is_speech)
            if done is not None:
                utterances.append(done)
        return utterances

    def finish(self) -> list[np.ndarray]:
        """音频流结束：把未完结的语音段按整句发出（用于进程退出/捕获重启）。"""
        utterances: list[np.ndarray] = []
        if self._in_speech and len(self._buf) > self.min_silence_frames:
            utterances.append(self._emit())
        self.reset()
        return utterances

    # ---- 内部 ----

    def _is_speech(self, frame: np.ndarray) -> bool:
        prob = float(self.model(frame[None, :], 16000))
        return prob >= self.threshold

    def _advance(self, frame: np.ndarray, is_speech: bool) -> np.ndarray | None:
        if not self._in_speech:
            if not is_speech:
                # 静音态：滚动保留 pre-roll
                self._pre_roll.append(frame)
                if len(self._pre_roll) > self.pad_frames:
                    self._pre_roll.pop(0)
                return None
            # 语音开始：之前保留的 pre-roll 静音 + 当前语音帧
            self._in_speech = True
            self._buf = list(self._pre_roll) + [frame]
            self._silence_frames = 0
            return None

        # 语音态
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
        """切出当前 utterance：截掉尾部多余静音，保留 speech_pad 尾部。"""
        if self._silence_frames > self.pad_frames:
            keep = len(self._buf) - self._silence_frames + self.pad_frames
            audio = np.concatenate(self._buf[:keep])
        else:
            audio = np.concatenate(self._buf)

        # 回填 pre-roll（句尾保留的静音可能属于下一句）
        if self.pad_frames > 0:
            tail = self._buf[-self.pad_frames :]
            self._pre_roll = list(tail)
        else:
            self._pre_roll = []
        self._buf = []
        self._silence_frames = 0
        self._in_speech = False
        return audio
