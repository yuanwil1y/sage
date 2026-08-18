"""TranslatorOrchestrator data-flow and fault-isolation tests."""

from __future__ import annotations

import threading
import time

import numpy as np

from pipeline.orchestrator import TranslatorOrchestrator


class FakeTranslator:
    def __init__(self):
        self.calls: list[str] = []

    def translate(self, text, source_lang="日语", target_lang="简体中文"):
        self.calls.append(text)
        return f"译:{text}"


class FlakyTranslator:
    def __init__(self):
        self.calls: list[str] = []

    def translate(self, text):
        self.calls.append(text)
        if len(self.calls) == 1:
            raise RuntimeError("temporary translator failure")
        return f"译:{text}"


class BlockingTranslator(FakeTranslator):
    def __init__(self, gate: threading.Event):
        super().__init__()
        self.gate = gate

    def translate(self, text, source_lang="日语", target_lang="简体中文"):
        self.calls.append(text)
        self.gate.wait(timeout=2.0)
        return f"译:{text}"


class FakeTranscriber:
    model_available = True

    def __init__(self):
        self.calls: list[np.ndarray] = []

    def transcribe(self, audio):
        self.calls.append(audio)
        return "こんにちは"


class BlockingTranscriber(FakeTranscriber):
    def __init__(self, gate: threading.Event):
        super().__init__()
        self.gate = gate

    def transcribe(self, audio):
        self.calls.append(audio)
        self.gate.wait(timeout=2.0)
        return "こんにちは"


class FakeAudioPipeline:
    def __init__(self):
        self.finished = 0
        self.reset_count = 0

    def configure(self, **kwargs):
        pass

    def feed_pcm(self, pcm_bytes):
        return []

    def finish(self):
        self.finished += 1
        return []

    def reset(self):
        self.reset_count += 1


class FakePipe:
    def __init__(self):
        self.messages = []

    def broadcast(self, msg):
        self.messages.append(msg)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _make_orchestrator(*, translator=None, transcriber=None):
    pipe = FakePipe()
    translator = translator or FakeTranslator()
    transcriber = transcriber or FakeTranscriber()
    orch = TranslatorOrchestrator(
        pipe,
        translator,
        transcriber=transcriber,
        audio_pipeline=FakeAudioPipeline(),
    )
    return orch, pipe, translator, transcriber


def test_handle_utterance_translates_and_broadcasts():
    orch, pipe, translator, transcriber = _make_orchestrator()
    try:
        audio = np.zeros(16000, dtype=np.float32)
        orch.handle_utterance(audio)

        assert _wait_until(lambda: len(pipe.messages) == 1)
        assert len(transcriber.calls) == 1
        assert translator.calls == ["こんにちは"]
        msg = pipe.messages[0]
        assert msg["type"] == "subtitle"
        assert msg["source"] == "voice"
        assert msg["original"] == "こんにちは"
        assert msg["translated"] == "译:こんにちは"
    finally:
        orch.stop()


def test_handle_chat_line_translates_and_broadcasts():
    orch, pipe, translator, _ = _make_orchestrator()
    try:
        orch.handle_chat_line("ミッド二人")
        assert _wait_until(lambda: len(pipe.messages) == 1)
        assert translator.calls == ["ミッド二人"]
        assert pipe.messages[0]["source"] == "chat"
        assert pipe.messages[0]["translated"] == "译:ミッド二人"
    finally:
        orch.stop()


def test_empty_chat_line_ignored():
    orch, pipe, translator, _ = _make_orchestrator()
    try:
        orch.handle_chat_line("")
        orch.handle_chat_line("   ")
        assert translator.calls == []
        assert pipe.messages == []
    finally:
        orch.stop()


def test_empty_utterance_ignored():
    orch, pipe, translator, transcriber = _make_orchestrator()
    try:
        orch.handle_utterance(np.zeros(0, dtype=np.float32))
        assert transcriber.calls == []
        assert translator.calls == []
        assert pipe.messages == []
    finally:
        orch.stop()


def test_slow_asr_does_not_block_capture_callback():
    gate = threading.Event()
    transcriber = BlockingTranscriber(gate)
    orch, pipe, _, _ = _make_orchestrator(transcriber=transcriber)
    try:
        started = time.perf_counter()
        orch.handle_utterance(np.ones(16000, dtype=np.float32))
        elapsed = time.perf_counter() - started

        assert elapsed < 0.1
        assert _wait_until(lambda: len(transcriber.calls) == 1)
        assert pipe.messages == []
        gate.set()
        assert _wait_until(lambda: len(pipe.messages) == 1)
    finally:
        gate.set()
        orch.stop()


def test_slow_translation_does_not_block_chat_callback():
    gate = threading.Event()
    translator = BlockingTranslator(gate)
    orch, pipe, _, _ = _make_orchestrator(translator=translator)
    try:
        started = time.perf_counter()
        orch.handle_chat_line("first")
        orch.handle_chat_line("second")
        elapsed = time.perf_counter() - started

        assert elapsed < 0.1
        assert _wait_until(lambda: len(translator.calls) >= 1)
        gate.set()
        assert _wait_until(lambda: len(pipe.messages) == 2)
    finally:
        gate.set()
        orch.stop()


def test_translator_exception_does_not_stop_later_messages():
    translator = FlakyTranslator()
    orch, pipe, _, _ = _make_orchestrator(translator=translator)
    try:
        orch.handle_chat_line("first")
        orch.handle_chat_line("second")

        assert _wait_until(lambda: len(translator.calls) == 2)
        assert _wait_until(lambda: len(pipe.messages) == 1)
        assert pipe.messages[0]["original"] == "second"
        assert pipe.messages[0]["translated"] == "译:second"
    finally:
        orch.stop()


def test_replace_translator_affects_subsequent_messages():
    orch, pipe, old_translator, _ = _make_orchestrator()
    replacement = FakeTranslator()
    try:
        orch.replace_translator(replacement)
        orch.handle_chat_line("new model")
        assert _wait_until(lambda: len(pipe.messages) == 1)
        assert old_translator.calls == []
        assert replacement.calls == ["new model"]
    finally:
        orch.stop()


def test_stop_drains_inflight_asr_before_translation_worker_exits():
    orch, pipe, translator, transcriber = _make_orchestrator()
    orch.handle_utterance(np.ones(16000, dtype=np.float32))

    # Stop immediately instead of waiting for either worker. Graceful shutdown
    # must let the queued ASR item reach translation before placing its stop.
    orch.stop()

    assert len(transcriber.calls) == 1
    assert translator.calls == ["こんにちは"]
    assert len(pipe.messages) == 1
    assert pipe.messages[0]["translated"] == "译:こんにちは"


def test_text_mode_does_not_construct_voice_chain():
    pipe = FakePipe()
    translator = FakeTranslator()
    orch = TranslatorOrchestrator(pipe, translator, mode="text")
    try:
        assert orch.voice_enabled is False
        assert orch.chat_enabled is True
        assert orch.transcriber is None
        assert orch.audio_pipeline is None
        orch.handle_utterance(np.ones(16_000, dtype=np.float32))
        assert pipe.messages == []
    finally:
        orch.stop()


def test_voicechat_mode_does_not_start_chat_chain():
    pipe = FakePipe()
    translator = FakeTranslator()
    orch = TranslatorOrchestrator(
        pipe,
        translator,
        transcriber=FakeTranscriber(),
        audio_pipeline=FakeAudioPipeline(),
        mode="voicechat",
    )
    try:
        assert orch.voice_enabled is True
        assert orch.chat_enabled is False
        assert orch.start_chat_monitor(None) is False
    finally:
        orch.stop()
