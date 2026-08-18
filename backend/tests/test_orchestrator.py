"""编排器测试（规格第 18、33 节）。

用 fake Translator/Transcriber/PipeServer 验证数据流：
utterance/聊天 → 翻译 → broadcast subtitle。
"""

import numpy as np

from ipc.pipe_server import PipeServer
from pipeline.orchestrator import TranslatorOrchestrator


class FakeTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text, source_lang="日语", target_lang="简体中文"):
        self.calls.append(text)
        return f"译:{text}"


class FakeTranscriber:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio):
        self.calls.append(audio)
        return "こんにちは"


class FakePipe:
    def __init__(self):
        self.messages = []

    def broadcast(self, msg):
        self.messages.append(msg)


def _make_orchestrator():
    pipe = FakePipe()
    translator = FakeTranslator()
    transcriber = FakeTranscriber()
    orch = TranslatorOrchestrator(pipe, translator, transcriber=transcriber)
    return orch, pipe, translator, transcriber


def test_handle_utterance_translates_and_broadcasts():
    orch, pipe, translator, transcriber = _make_orchestrator()
    audio = np.zeros(16000, dtype=np.float32)
    orch.handle_utterance(audio)

    assert len(transcriber.calls) == 1
    assert translator.calls == ["こんにちは"]
    assert len(pipe.messages) == 1
    msg = pipe.messages[0]
    assert msg["type"] == "subtitle"
    assert msg["source"] == "voice"
    assert msg["original"] == "こんにちは"
    assert msg["translated"] == "译:こんにちは"


def test_handle_chat_line_translates_and_broadcasts():
    orch, pipe, translator, _ = _make_orchestrator()
    orch.handle_chat_line("ミッド二人")

    assert translator.calls == ["ミッド二人"]
    assert len(pipe.messages) == 1
    assert pipe.messages[0]["source"] == "chat"
    assert pipe.messages[0]["translated"] == "译:ミッド二人"


def test_empty_chat_line_ignored():
    orch, pipe, translator, _ = _make_orchestrator()
    orch.handle_chat_line("")
    orch.handle_chat_line("   ")
    assert translator.calls == []
    assert pipe.messages == []


def test_empty_utterance_ignored():
    orch, pipe, translator, transcriber = _make_orchestrator()
    orch.handle_utterance(np.zeros(0, dtype=np.float32))
    assert transcriber.calls == []
    assert translator.calls == []
    assert pipe.messages == []


def test_text_mode_does_not_construct_voice_chain():
    pipe = FakePipe()
    translator = FakeTranslator()
    orch = TranslatorOrchestrator(pipe, translator, mode="text")

    assert orch.voice_enabled is False
    assert orch.chat_enabled is True
    assert orch.transcriber is None
    assert orch.audio_pipeline is None
    orch.handle_utterance(np.ones(16_000, dtype=np.float32))
    assert pipe.messages == []


def test_voicechat_mode_does_not_start_chat_chain():
    pipe = FakePipe()
    translator = FakeTranslator()
    orch = TranslatorOrchestrator(pipe, translator, mode="voicechat")

    assert orch.voice_enabled is True
    assert orch.chat_enabled is False
    assert orch.start_chat_monitor(None) is False
