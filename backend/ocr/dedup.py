"""OCR Dedup（规格文档第 31 节）。

使用 RapidFuzz，默认 similarity >= 90 判定为同一条；
TTL=30 秒内相同聊天只生成一次 SourceMessage。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from rapidfuzz import fuzz


@dataclass
class _Seen:
    text: str
    ts: float


class OcrDeduper:
    def __init__(self, *, threshold: float = 90.0, ttl: float = 30.0) -> None:
        self.threshold = threshold
        self.ttl = ttl
        self._seen: List[_Seen] = []

    def is_duplicate(self, text: str) -> bool:
        """返回该文本是否在 TTL 内与已见过的条目高度相似（重复）。"""
        now = time.time()
        # 清理过期
        self._seen = [s for s in self._seen if now - s.ts < self.ttl]

        for s in self._seen:
            if fuzz.ratio(text, s.text) >= self.threshold:
                return True
        return False

    def mark_seen(self, text: str) -> None:
        """记录一条已见过/已处理的文本。"""
        self._seen = [s for s in self._seen if time.time() - s.ts < self.ttl]
        self._seen.append(_Seen(text=text, ts=time.time()))
