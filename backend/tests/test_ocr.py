"""OCR LineAssembler + visible-window dedup tests."""

from ocr.dedup import OcrDeduper
from ocr.line_assembler import OcrFragment, assemble_line


def _rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_assemble_same_line_sorts_by_x() -> None:
    f1 = OcrFragment(text="Raze:", box=_rect(40, 100, 60, 20))
    f2 = OcrFragment(text="ミッド二人", box=_rect(110, 100, 120, 20))
    lines = assemble_line([f2, f1])
    assert lines == ["Raze: ミッド二人"]


def test_assemble_multiple_lines() -> None:
    line1a = OcrFragment(text="Raze:", box=_rect(40, 100, 60, 20))
    line1b = OcrFragment(text="ミッド二人", box=_rect(110, 100, 120, 20))
    line2 = OcrFragment(text="エコ", box=_rect(40, 140, 60, 20))
    lines = assemble_line([line2, line1b, line1a])
    assert len(lines) == 2
    assert lines[0] == "Raze: ミッド二人"
    assert lines[1] == "エコ"


def test_assemble_empty() -> None:
    assert assemble_line([]) == []


def test_dedup_same_visible_window_emits_once() -> None:
    d = OcrDeduper(threshold=90.0)
    assert d.filter_new(["ミッド二人", "裏来てる"]) == ["ミッド二人", "裏来てる"]
    assert d.filter_new(["ミッド二人", "裏来てる"]) == []


def test_dedup_fuzzy_ocr_jitter_keeps_visible_overlap() -> None:
    d = OcrDeduper(threshold=90.0)
    assert d.filter_new(["ミッド二人"]) == ["ミッド二人"]
    assert d.filter_new(["ミッド二人。"]) == []


def test_dedup_scrolling_window_emits_only_appended_line() -> None:
    d = OcrDeduper(threshold=90.0)
    d.filter_new(["A", "B", "C"])
    assert d.filter_new(["B", "C", "D"]) == ["D"]


def test_dedup_allows_same_valid_callout_to_be_sent_again() -> None:
    d = OcrDeduper(threshold=90.0)
    d.filter_new(["A", "B", "ミッド二人"])
    # Chat scrolls and a new player/message repeats the exact same callout.
    assert d.filter_new(["B", "ミッド二人", "ミッド二人"]) == ["ミッド二人"]


def test_dedup_reset_treats_next_window_as_fresh() -> None:
    d = OcrDeduper()
    d.filter_new(["A", "B"])
    d.reset()
    assert d.filter_new(["A", "B"]) == ["A", "B"]
