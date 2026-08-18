"""Bounded LRU 翻译缓存（规格文档第 32.10 节）。

key: normalize(original)
value: translated
cache_size 默认 2048。VALORANT 报点高度重复，命中直接返回。
"""

from __future__ import annotations

from collections import OrderedDict

DEFAULT_CACHE_SIZE = 2048


def normalize(text: str) -> str:
    """归一化原文作为 cache key（去首尾空白）。"""
    return (text or "").strip()


class TranslationCache:
    def __init__(self, max_size: int = DEFAULT_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._data: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> str | None:
        k = normalize(key)
        if k in self._data:
            # LRU：命中时移到末尾
            self._data.move_to_end(k)
            return self._data[k]
        return None

    def put(self, key: str, value: str) -> None:
        k = normalize(key)
        if k in self._data:
            self._data.move_to_end(k)
        self._data[k] = value
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)  # 淘汰最久未用

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()
