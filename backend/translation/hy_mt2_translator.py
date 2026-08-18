"""Hy-MT2 本地翻译器（规格文档第 32.5、32.8 节）。

通过 httpx 请求本地 llama-server 的 OpenAI-compatible /v1/chat/completions。
只发往 http://127.0.0.1:<port>，禁止发送到公网。
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from translation import base
from translation.cache import TranslationCache
from translation.glossary import load_glossary
from translation.prompt_builder import PromptBuilder
from translation.sanitizer import TranslationSanitizer

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "local-hy-mt2"


class HyMT2LocalTranslator:
    """本地 Hy-MT2 翻译器（经 llama-server HTTP API）。"""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = DEFAULT_MODEL_NAME,
        temperature: float = 0.0,
        max_tokens: int = 128,
        timeout: float = 15.0,
        cache_size: int = 2048,
        prompt_builder: PromptBuilder | None = None,
        sanitizer: TranslationSanitizer | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._cache = TranslationCache(cache_size)
        self._prompt = prompt_builder or PromptBuilder(load_glossary())
        self._sanitizer = sanitizer or TranslationSanitizer()

    # ---- 主接口 ----

    def translate(
        self,
        text: str,
        source_lang: str = base.SOURCE_JA,
        target_lang: str = base.TARGET_ZH_HANS,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        # 命中缓存直接返回
        cached = self._cache.get(text)
        if cached is not None:
            return cached

        messages = self._prompt.build_messages(text, source_lang, target_lang)
        raw = self._request(messages)
        cleaned = self._sanitizer.clean(raw)

        if cleaned:
            self._cache.put(text, cleaned)
        return cleaned

    # ---- 内部 ----

    def _request(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        url = f"{self._base_url}/v1/chat/completions"
        resp = httpx.post(url, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            log.error("llama-server 响应结构异常: %s", data)
            raise RuntimeError(f"无法解析 llama-server 响应: {exc}") from exc
