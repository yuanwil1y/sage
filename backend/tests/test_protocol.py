"""IPC 协议测试（规格第 15 节）。"""

import json

from ipc import protocol
from models.messages import TranslationResult


def test_subtitle_voice_message_shape() -> None:
    result = TranslationResult(
        source_type="voice", original="ジェットロー", translated="捷风残血", id="v-1021"
    )
    msg = protocol.subtitle_message(result)
    assert msg["v"] == 1
    assert msg["type"] == "subtitle"
    assert msg["source"] == "voice"
    assert msg["id"] == "v-1021"
    assert msg["original"] == "ジェットロー"
    assert msg["translated"] == "捷风残血"
    assert isinstance(msg["ts"], float)


def test_status_and_clear_and_heartbeat() -> None:
    status = protocol.status_message(backend="ready", valorant="running")
    assert status["type"] == "status"
    assert status["backend"] == "ready"
    assert status["valorant"] == "running"

    clear = protocol.clear_message()
    assert clear["type"] == "clear"
    assert clear["v"] == 1

    hb = protocol.heartbeat_message()
    assert hb["type"] == "heartbeat"


def test_encode_utf8_ndjson_one_line() -> None:
    result = TranslationResult(
        source_type="chat", original="ミッド二人", translated="中路两个", id="c-4031"
    )
    payload = protocol.encode(protocol.subtitle_message(result))
    text = payload.decode("utf-8")
    assert text.endswith("\n")
    assert text.count("\n") == 1  # 一行一个 object
    parsed = json.loads(text)
    assert parsed["original"] == "ミッド二人"  # 日文原样保留（非 ASCII 不转义）


def test_decode_line_roundtrip() -> None:
    result = TranslationResult(
        source_type="voice", original="テスト", translated="测试", id="v-9"
    )
    raw = protocol.encode(protocol.subtitle_message(result))
    decoded = protocol.decode_line(raw)
    assert decoded["id"] == "v-9"
    assert decoded["translated"] == "测试"
