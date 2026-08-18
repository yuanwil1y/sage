"""翻译 Prompt 构建（规格文档第 32.6、32.7 节）。

Hy-MT2 默认翻译 prompt + VALORANT 术语干预。
只选取 source_text 中实际出现的术语，不把完整大词典塞进 prompt。
"""

from __future__ import annotations

from translation.glossary import extract_relevant_terms

SYSTEM_PROMPT = (
    "你是一个专业的日译中翻译引擎，用于 VALORANT 游戏内队伍报点翻译。"
    "将日语简短报点翻译为简洁、自然、符合中文游戏习惯的简体中文。"
    "只输出翻译结果，不要任何解释、标点前后缀或 markdown。"
)

# 用户指令模板（保留 {glossary_block} 与 {source_text} 两个占位符）
USER_TEMPLATE = (
    "{glossary_block}"
    "将以下文本翻译为简体中文。"
    "这是 VALORANT 游戏中的简短队伍交流，请使用简洁自然的中文游戏报点表达。"
    "注意只需要输出翻译后的结果，不要额外解释：\n"
    "{source_text}"
)


class PromptBuilder:
    """构建发给 Hy-MT2 的 messages（chat 格式）。"""

    def __init__(self, glossary: dict[str, str] | None = None) -> None:
        self._glossary = glossary

    def build_messages(
        self,
        text: str,
        source_lang: str = "日语",
        target_lang: str = "简体中文",
    ) -> list[dict[str, str]]:
        terms = extract_relevant_terms(text, self._glossary)
        if terms:
            lines = [f"{ja} 翻译成 {zh}" for ja, zh in terms.items()]
            glossary_block = "参考下面的翻译：\n" + "\n".join(lines) + "\n\n"
        else:
            glossary_block = ""

        user = USER_TEMPLATE.format(
            glossary_block=glossary_block, source_text=text.strip()
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
