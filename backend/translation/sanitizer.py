"""翻译输出清理（规格文档第 32.9 节）。

TranslationSanitizer：
- strip whitespace
- 去除意外 Markdown code fence
- 去除 “翻译：” 等前缀
- 限制最大字符数
- 拒绝空输出（返回空串）
"""

from __future__ import annotations

import re

MAX_CHARS = 200

_PREFIXES = (
    "翻译：",
    "翻译:",
    "译文：",
    "译文:",
    "翻译结果：",
    "翻译结果:",
    "中文：",
    "中文:",
)


class TranslationSanitizer:
    def __init__(self, max_chars: int = MAX_CHARS) -> None:
        self.max_chars = max_chars

    def clean(self, raw: str) -> str:
        if not raw:
            return ""
        text = raw.strip()

        # 去除整段 code fence（` ```text ... ``` ` 或裸 ``` 包裹）
        text = re.sub(r"```[a-zA-Z]*\n?(.*?)\n?```", r"\1", text, flags=re.DOTALL)
        text = text.replace("```", "").strip()

        # 去除常见前缀
        for prefix in _PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break

        # 去掉可能残留的引号包裹
        text = text.strip("“”\"'「」")

        # 限制长度
        if len(text) > self.max_chars:
            text = text[: self.max_chars].strip()

        return text
