"""OCR LineAssembler + RapidFuzz 去重测试（规格第 29、31 节）。"""

import time

from ocr.dedup import OcrDeduper
from ocr.line_assembler import OcrFragment, assemble_line


def _rect(x, y, w, h):
    """构造一个矩形 box（左上/右上/右下/左下）。"""
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_assemble_same_line_sorts_by_x() -> None:
    # 同一行：两个 fragment，x 位置不同
    f1 = OcrFragment(text="Raze:", box=_rect(40, 100, 60, 20))
    f2 = OcrFragment(text="ミッド二人", box=_rect(110, 100, 120, 20))
    lines = assemble_line([f2, f1])  # 乱序传入，应按 x 排序
    assert lines == ["Raze: ミッド二人"]


def test_assemble_multiple_lines() -> None:
    line1a = OcrFragment(text="Raze:", box=_rect(40, 100, 60, 20))
    line1b = OcrFragment(text="ミッド二人", box=_rect(110, 100, 120, 20))
    line2 = OcrFragment(text="エコ", box=_rect(40, 140, 60, 20))  # 下一行 y=140
    lines = assemble_line([line2, line1b, line1a])
    assert len(lines) == 2
    assert lines[0] == "Raze: ミッド二人"
    assert lines[1] == "エコ"


def test_assemble_empty() -> None:
    assert assemble_line([]) == []


def test_dedup_similar_within_ttl() -> None:
    d = OcrDeduper(threshold=90.0, ttl=30.0)
    assert not d.is_duplicate("ミッド二人")
    d.mark_seen("ミッド二人")
    # 完全相同
    assert d.is_duplicate("ミッド二人")
    # 高度相似（OCR 微量误差）
    assert d.is_duplicate("ミッド二人。")
    # 不同内容
    assert not d.is_duplicate("裏来てる")


def test_dedup_expires_after_ttl() -> None:
    d = OcrDeduper(threshold=90.0, ttl=1.0)
    d.mark_seen("ミッド二人")
    assert d.is_duplicate("ミッド二人")
    time.sleep(1.2)  # 超过 TTL
    assert not d.is_duplicate("ミッド二人")
