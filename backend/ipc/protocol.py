"""IPC 消息协议（规格文档第 15 节）。

UTF-8 NDJSON：一行一个 JSON object，protocol version = 1。

消息类型：
- subtitle:  {"v":1,"type":"subtitle","source":"voice|chat","id","original","translated","ts"}
- status:    {"v":1,"type":"status","backend":"ready","valorant":"running","ts"}
- clear:     {"v":1,"type":"clear","ts"}
- heartbeat: {"v":1,"type":"heartbeat","ts"}
"""

from __future__ import annotations

import json
import time
from typing import Any

from models.messages import TranslationResult

PROTOCOL_VERSION = 1


def _base(payload_type: str) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": payload_type, "ts": time.time()}


def subtitle_message(result: TranslationResult) -> dict[str, Any]:
    """TranslationResult → subtitle JSON object。"""
    msg = _base("subtitle")
    msg.update(
        {
            "source": result.source_type,
            "id": result.id,
            "original": result.original,
            "translated": result.translated,
        }
    )
    return msg


def status_message(*, backend: str, valorant: str) -> dict[str, Any]:
    msg = _base("status")
    msg.update({"backend": backend, "valorant": valorant})
    return msg


def clear_message() -> dict[str, Any]:
    return _base("clear")


def heartbeat_message() -> dict[str, Any]:
    return _base("heartbeat")


def encode(obj: dict[str, Any]) -> bytes:
    """序列化为一行 NDJSON（UTF-8，不带 BOM，以 \\n 结尾）。"""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line: bytes) -> dict[str, Any]:
    """解析一行 NDJSON。"""
    return json.loads(line.decode("utf-8").strip())
