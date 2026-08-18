"""HyMT2LocalTranslator 测试（规格第 32.5、32.8 节）。

用本地 fake HTTP server 验证：
- 请求发往 localhost /v1/chat/completions
- prompt 正确构建（术语干预）
- 输出经 sanitizer 清理
- 缓存命中不重复请求
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from translation.glossary import extract_relevant_terms
from translation.hy_mt2_translator import HyMT2LocalTranslator
from translation.prompt_builder import PromptBuilder
from translation.sanitizer import TranslationSanitizer

GLOSSARY = {
    "ジェット": "捷风",
    "ロー": "残血",
    "裏": "绕后",
    "ミッド": "中路",
}


class _Handler(BaseHTTPRequestHandler):
    # 记录请求
    requests = []

    def do_POST(self):
        assert self.path == "/v1/chat/completions"
        length = int(self.headers.get("Content-Length", 0))
        import json

        body = json.loads(self.rfile.read(length))
        _Handler.requests.append(body)

        content = body["messages"][-1]["content"]
        # 根据用户消息里是否含术语，返回对应「翻译结果」
        response = {
            "choices": [
                {"message": {"content": "翻译：捷风残血"}}
            ]
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass  # 静默


def _start_server():
    _Handler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_translate_calls_local_endpoint_and_sanitizes() -> None:
    server = _start_server()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    translator = HyMT2LocalTranslator(
        base_url, prompt_builder=PromptBuilder(GLOSSARY), sanitizer=TranslationSanitizer()
    )
    try:
        out = translator.translate("ジェットロー")
        assert out == "捷风残血"  # 去掉「翻译：」前缀
        assert len(_Handler.requests) == 1
        req = _Handler.requests[0]
        assert req["model"] == "local-hy-mt2"
        assert req["temperature"] == 0.0
        assert req["max_tokens"] == 128
        assert req["stream"] is False
    finally:
        server.shutdown()


def test_translate_cache_hits_avoid_second_request() -> None:
    server = _start_server()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    translator = HyMT2LocalTranslator(
        base_url, prompt_builder=PromptBuilder(GLOSSARY), sanitizer=TranslationSanitizer()
    )
    try:
        first = translator.translate("ミッド二人")
        second = translator.translate("ミッド二人")
        assert first == second == "捷风残血"
        assert len(_Handler.requests) == 1  # 第二次命中缓存
    finally:
        server.shutdown()


def test_translate_empty_returns_empty_no_request() -> None:
    server = _start_server()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    translator = HyMT2LocalTranslator(base_url)
    try:
        assert translator.translate("") == ""
        assert translator.translate("   ") == ""
        assert len(_Handler.requests) == 0
    finally:
        server.shutdown()


def _fake_terms(text):
    return extract_relevant_terms(text, GLOSSARY)


def test_prompt_contains_terms_for_relevant_text() -> None:
    pb = PromptBuilder(GLOSSARY)
    msgs = pb.build_messages("ジェットロー")
    user = msgs[-1]["content"]
    assert "ジェット 翻译成 捷风" in user
    assert "ロー 翻译成 残血" in user
