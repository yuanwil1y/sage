"""OCR LineAssembler（规格文档第 29 节）。

PaddleOCR 返回 rec_texts/rec_scores/rec_boxes（boxes 为四边形或矩形）。
根据 box 的 y_center / height / x_min 把同一视觉行的 fragment 拼成一行。

示例：
    fragments  [Raze:]   y_center=100
               [ミッド二人] y_center=100  (同一行)
    得到       "Raze: ミッド二人"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass
class OcrFragment:
    """一个 OCR 识别片段（一段文本 + 其 box）。"""

    text: str
    box: Sequence[Sequence[float]]  # 四边形顶点 [[x,y]*4]
    score: float = 0.0

    @property
    def y_center(self) -> float:
        ys = [p[1] for p in self.box]
        return (min(ys) + max(ys)) / 2.0

    @property
    def height(self) -> float:
        ys = [p[1] for p in self.box]
        return max(ys) - min(ys)

    @property
    def x_min(self) -> float:
        xs = [p[0] for p in self.box]
        return min(xs)


def assemble_line(
    fragments: Sequence[OcrFragment],
    *,
    y_tolerance_ratio: float = 0.6,
) -> List[str]:
    """把 fragments 按视觉行分组，每行按 x_min 排序后拼接文本。

    同一行判定：两个 fragment 的 y_center 距离 < max(height_a, height_b) * y_tolerance_ratio。
    """
    if not fragments:
        return []

    # 按 y_center 排序，扫描分组成行
    ordered = sorted(fragments, key=lambda f: f.y_center)
    lines: List[List[OcrFragment]] = []

    for frag in ordered:
        placed = False
        for line in lines:
            # 与已有行的代表（最后一个）比较
            rep = line[-1]
            threshold = max(rep.height, frag.height) * y_tolerance_ratio
            if abs(rep.y_center - frag.y_center) < threshold:
                line.append(frag)
                placed = True
                break
        if not placed:
            lines.append([frag])

    # 每行按 x_min 排序拼文本
    results: List[str] = []
    for line in lines:
        line.sort(key=lambda f: f.x_min)
        text = " ".join(f.text.strip() for f in line if f.text.strip())
        if text:
            results.append(text)
    return results
