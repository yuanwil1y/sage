"""翻译包：本地 Hy-MT2 翻译（规格文档第 32 节）。

组件：
- base:                Translator 抽象接口
- glossary:            VALORANT 术语表加载/检索
- prompt_builder:      Hy-MT2 翻译 Prompt + 术语干预
- sanitizer:           输出清理
- cache:               bounded LRU 缓存
- hy_mt2_server:       llama-server 生命周期管理
- hy_mt2_translator:   本地翻译客户端
"""

from translation.base import SOURCE_JA, TARGET_ZH_HANS, Translator
from translation.hy_mt2_translator import HyMT2LocalTranslator

__all__ = [
    "Translator",
    "SOURCE_JA",
    "TARGET_ZH_HANS",
    "HyMT2LocalTranslator",
]
