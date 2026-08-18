"""VALORANT 术语表（规格文档第 32.7 节）。

术语表在 backend/resources/valorant_ja_zh.json，本模块负责加载与检索。
PromptBuilder 只选取当前 source_text 中实际出现的术语。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from paths import resources_dir

_RESOURCE_PATH = resources_dir() / "valorant_ja_zh.json"


def load_glossary(path: Path | None = None) -> Mapping[str, str]:
    """加载术语表（ja → zh）。加载失败返回空 dict。"""
    p = path or _RESOURCE_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def extract_relevant_terms(
    text: str,
    glossary: Mapping[str, str] | None = None,
    *,
    max_terms: int = 8,
) -> dict[str, str]:
    """返回 text 中实际出现的术语（按 dict 顺序，最多 max_terms 个）。

    只做简单子串匹配；长术语优先（先按长度降序，避免短词误命中）。
    """
    glossary = glossary if glossary is not None else load_glossary()
    hits: dict[str, str] = {}
    # 长术语优先匹配，避免 "ロー" 在更长的词里被单独命中
    for ja in sorted(glossary, key=len, reverse=True):
        if len(hits) >= max_terms:
            break
        if ja and ja in text:
            hits[ja] = glossary[ja]
    return hits
