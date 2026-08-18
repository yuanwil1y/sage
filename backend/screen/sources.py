"""SourceMessage 采集封装（chat / voice 共用）。

把识别产出的原文包装成 SourceMessage，供翻译管线消费。
"""

from __future__ import annotations

from models.messages import SourceMessage


def make_voice_message(original: str) -> SourceMessage:
    return SourceMessage(source_type="voice", original=original)


def make_chat_message(original: str) -> SourceMessage:
    return SourceMessage(source_type="chat", original=original)
