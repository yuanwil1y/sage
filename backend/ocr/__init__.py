"""轻量 OCR 公共工具。

不要在包初始化时导入 ``chat_worker``。它会继续加载 PaddleOCR、DXcam 和
OpenCV，导致只启用语音链的构建也把整套 OCR 依赖带进来。需要 OCR worker
时由 ``pipeline.orchestrator`` 在运行时按模式惰性导入。
"""

from ocr.dedup import OcrDeduper
from ocr.line_assembler import OcrFragment, assemble_line

__all__ = ["OcrFragment", "assemble_line", "OcrDeduper"]
