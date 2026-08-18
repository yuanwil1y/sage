"""OCR 翻译链路准确率与响应时间测试。

流程：生成含日语文字的图片 → PaddleOCR 识别 → LineAssembler 拼行
     → 对比预期算 OCR 准确率 → Hy-MT2 翻译 → 记录翻译耗时。

用法：
    python test_ocr_accuracy.py
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ocr.line_assembler import OcrFragment, assemble_line
from ocr.ocr_engine import OcrEngine

# 测试句子（聊天框每行一句）
TEST_LINES = [
    "ジェットロー",
    "ミッド二人いる",
    "裏来てる",
    "Aサイトに行こう",
]

# 字体：优先微软雅黑（能渲染日文），否则默认
def _load_font(size=32):
    for name in ["C:\\Windows\\Fonts\\msgothic.ttc",   # MS Gothic（日文）
                 "C:\\Windows\\Fonts\\meiryo.ttc",      # Meiryo（日文）
                 "C:\\Windows\\Fonts\\arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_chat_image(lines, width=400, line_h=50):
    """生成白底黑字的聊天框图片。返回 np.ndarray (H,W,3) BGR。"""
    font = _load_font(30)
    height = line_h * len(lines) + 20
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = 10
    for line in lines:
        draw.text((10, y), line, fill="black", font=font)
        y += line_h
    # PIL RGB → numpy BGR
    arr = np.array(img)[:, :, ::-1].copy()
    return arr


def norm(s: str) -> str:
    return "".join(s.replace("　", "").replace(" ", "").replace("、", "").replace("。", ""))


async def main():
    from translation.hy_mt2_server import HyMT2ServerManager
    from translation.hy_mt2_translator import HyMT2LocalTranslator

    print("=" * 60)
    print("OCR 翻译链路测试")
    print("=" * 60)

    # ---- 1. OCR 识别 ----
    ocr = OcrEngine(lang="japan")
    img = make_chat_image(TEST_LINES)
    print(f"\n测试图片: {img.shape[1]}x{img.shape[0]}，含 {len(TEST_LINES)} 行日文")

    t0 = time.time()
    texts, scores, boxes = ocr.recognize(img)
    ocr_time = time.time() - t0
    print(f"OCR 耗时: {ocr_time:.2f}s（PaddleOCR 首次会下载模型）")
    print(f"识别到 {len(texts)} 个 fragment")

    # 转换为 OcrFragment 并拼行
    fragments = [OcrFragment(text=t, box=b, score=s) for t, s, b in zip(texts, scores, boxes)]
    lines = assemble_line(fragments)
    print(f"拼行结果: {len(lines)} 行")

    # ---- 2. OCR 准确率 ----
    print("\n--- OCR 识别对比 ---")
    for i, expected in enumerate(TEST_LINES):
        got = lines[i] if i < len(lines) else "(缺失)"
        from rapidfuzz import fuzz
        sim = fuzz.ratio(norm(got), norm(expected))
        print(f"  [{i+1}] 预期: {expected}")
        print(f"      识别: {got}  (相似度 {sim}%)")

    # ---- 3. 翻译链路 ----
    print("\n--- Hy-MT2 翻译 ---")
    mt2 = HyMT2ServerManager()
    try:
        mt2.start()
        mt2.wait_ready(timeout=60.0)
        translator = HyMT2LocalTranslator(mt2.base_url)

        for line in lines:
            t0 = time.time()
            translated = translator.translate(line)
            elapsed = time.time() - t0
            print(f"  {line} → {translated}  ({elapsed:.2f}s)")
    finally:
        mt2.stop()

    print("\n" + "=" * 60)
    print(f"OCR 总耗时: {ocr_time:.2f}s")
    print("=" * 60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
