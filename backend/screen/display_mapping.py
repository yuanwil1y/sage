"""DXcam output matching for saved Qt screen configurations.

Qt reports screen geometry in device-independent pixels while DXcam consumes
physical desktop pixels.  The saved ROI therefore keeps the screen geometry
and scale factor, and this module validates/remaps the persisted output index
against DXcam's current physical output list before a camera is created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DxcamOutput:
    device_idx: int
    output_idx: int
    width: int
    height: int
    primary: bool


_OUTPUT_RE = re.compile(
    r"Device\[(?P<device>\d+)\]\s+Output\[(?P<output>\d+)\]:\s*"
    r"Res:\((?P<width>\d+)\s*,\s*(?P<height>\d+)\).*?"
    r"Primary:(?P<primary>True|False)",
    re.IGNORECASE,
)


def parse_output_info(text: str) -> list[DxcamOutput]:
    """Parse the stable human-readable output of ``dxcam.output_info()``."""
    outputs: list[DxcamOutput] = []
    for match in _OUTPUT_RE.finditer(text or ""):
        outputs.append(
            DxcamOutput(
                device_idx=int(match.group("device")),
                output_idx=int(match.group("output")),
                width=int(match.group("width")),
                height=int(match.group("height")),
                primary=match.group("primary").lower() == "true",
            )
        )
    return outputs


def physical_size(
    geometry: tuple[int, int, int, int], device_pixel_ratio: float
) -> tuple[int, int]:
    """Return a Qt screen's expected physical ``(width, height)``."""
    _, _, width, height = geometry
    scale = max(0.1, float(device_pixel_ratio))
    return round(width * scale), round(height * scale)


def _same_size(output: DxcamOutput, expected: tuple[int, int]) -> bool:
    return (output.width, output.height) in {expected, (expected[1], expected[0])}


def resolve_output_index(
    outputs: Iterable[DxcamOutput],
    *,
    saved_output_idx: int,
    expected_size: tuple[int, int] | None = None,
    primary: bool | None = None,
    device_idx: int = 0,
) -> int:
    """Validate or remap an output index for the default DXcam adapter.

    A saved index is accepted when its current physical resolution and primary
    status still match. If the monitor order changed, a unique physical match
    is selected. Ambiguous or missing matches fail loudly so OCR does not
    silently read a different monitor.
    """
    available = [item for item in outputs if item.device_idx == device_idx]
    if not available:
        return saved_output_idx

    saved = next(
        (item for item in available if item.output_idx == saved_output_idx), None
    )
    if expected_size is None:
        if saved is not None:
            return saved.output_idx
        raise ValueError(f"DXcam 输出 {saved_output_idx} 不存在")

    def matches(item: DxcamOutput) -> bool:
        return _same_size(item, expected_size) and (
            primary is None or item.primary == primary
        )

    if saved is not None and matches(saved):
        return saved.output_idx

    candidates = [item for item in available if matches(item)]
    if len(candidates) == 1:
        return candidates[0].output_idx
    if not candidates:
        raise ValueError(
            "保存的显示器已不可用或分辨率/DPI 已变化，请重新选择聊天区域"
        )
    raise ValueError("检测到多个相同规格显示器，请重新选择聊天区域")
