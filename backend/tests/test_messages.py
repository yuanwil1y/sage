"""SourceMessage / TranslationResult 数据契约测试（规格第 38 节）。"""

from models.messages import SourceMessage, TranslationResult


def test_source_message_defaults() -> None:
    msg = SourceMessage(source_type="voice", original="ジェットロー")
    assert msg.id.startswith("v-")
    assert msg.source_type == "voice"
    assert msg.original == "ジェットロー"
    assert msg.created_at > 0


def test_chat_message_id_prefix() -> None:
    msg = SourceMessage(source_type="chat", original="ミッド二人")
    assert msg.id.startswith("c-")


def test_translation_result_defaults() -> None:
    result = TranslationResult(
        source_type="voice", original="ジェットロー", translated="捷风残血"
    )
    assert result.id.startswith("v-")
    assert result.translated == "捷风残血"


def test_explicit_id_is_preserved() -> None:
    msg = SourceMessage(source_type="chat", original="x", id="c-4031")
    assert msg.id == "c-4031"
