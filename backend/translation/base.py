"""翻译抽象接口（规格文档第 32.5 节）。

统一的 Translator 抽象，V1 实现为 HyMT2LocalTranslator（本地 llama.cpp / Hy-MT2）。
source_lang 默认 "日语"，target_lang 默认 "简体中文"。
"""

from __future__ import annotations

from typing import Protocol

SOURCE_JA = "日语"
TARGET_ZH_HANS = "简体中文"


class Translator(Protocol):
    def translate(
        self,
        text: str,
        source_lang: str = SOURCE_JA,
        target_lang: str = TARGET_ZH_HANS,
    ) -> str:
        ...
