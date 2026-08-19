"""DXcam output matching and output-local ROI coordinate helpers.

DXcam creates one camera per ``(device_idx, output_idx)`` and expects capture
regions in physical pixels relative to that output. Qt, meanwhile, reports
screen sizes/selection rectangles in device-independent pixels and maintains a
virtual-desktop origin that may be positive or negative. Keep those coordinate
spaces separate: screen geometry is only a fingerprint; capture regions are
always output-local physical pixels.
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
    """Parse the human-readable output of ``dxcam.output_info()``."""
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


def output_local_region(
    logical_region: tuple[int, int, int, int],
    device_pixel_ratio: float,
) -> tuple[int, int, int, int]:
    """Convert a screen-local Qt rectangle to output-local physical pixels."""
    left, top, right, bottom = logical_region
    scale = max(0.1, float(device_pixel_ratio))
    result = (
        round(left * scale),
        round(top * scale),
        round(right * scale),
        round(bottom * scale),
    )
    if result[0] < 0 or result[1] < 0 or result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError(f"无效的显示器局部 ROI: {result}")
    return result


def legacy_desktop_region_to_output_local(
    region: tuple[int, int, int, int],
    screen_geometry: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    """Convert Sage 1.0.14's hybrid virtual-desktop ROI to output-local pixels.

    The old selector stored ``geometry.left/top + local_physical`` (the virtual
    origin itself was not scaled). Subtracting that saved origin therefore
    recovers the local physical region exactly. Old configs without the saved
    screen geometry cannot be converted safely and must be reselected.
    """
    if screen_geometry is None:
        raise ValueError("旧版聊天区域缺少显示器信息，请重新选择聊天区域")
    origin_x, origin_y, _, _ = screen_geometry
    left, top, right, bottom = region
    converted = (
        left - origin_x,
        top - origin_y,
        right - origin_x,
        bottom - origin_y,
    )
    if (
        converted[0] < 0
        or converted[1] < 0
        or converted[2] <= converted[0]
        or converted[3] <= converted[1]
    ):
        raise ValueError("旧版聊天区域无法安全转换，请重新选择聊天区域")
    return converted


def _same_size(output: DxcamOutput, expected: tuple[int, int]) -> bool:
    return (output.width, output.height) in {expected, (expected[1], expected[0])}


def resolve_output(
    outputs: Iterable[DxcamOutput],
    *,
    saved_device_idx: int = 0,
    saved_output_idx: int,
    expected_size: tuple[int, int] | None = None,
    primary: bool | None = None,
) -> DxcamOutput:
    """Validate/remap a persisted DXcam ``(device_idx, output_idx)`` pair.

    Prefer the persisted pair when its physical fingerprint still matches. If
    display/GPU enumeration changed, select a unique matching output across all
    adapters. Ambiguity fails loudly rather than capturing the wrong monitor.
    """
    available = list(outputs)
    if not available:
        return DxcamOutput(
            device_idx=saved_device_idx,
            output_idx=saved_output_idx,
            width=expected_size[0] if expected_size else 0,
            height=expected_size[1] if expected_size else 0,
            primary=bool(primary),
        )

    saved = next(
        (
            item
            for item in available
            if item.device_idx == saved_device_idx
            and item.output_idx == saved_output_idx
        ),
        None,
    )

    if expected_size is None:
        if saved is not None:
            return saved
        raise ValueError(
            f"DXcam 输出 Device[{saved_device_idx}] Output[{saved_output_idx}] 不存在"
        )

    def matches(item: DxcamOutput) -> bool:
        return _same_size(item, expected_size) and (
            primary is None or item.primary == primary
        )

    if saved is not None and matches(saved):
        return saved

    candidates = [item for item in available if matches(item)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(
            "保存的显示器已不可用或分辨率/DPI 已变化，请重新选择聊天区域"
        )
    raise ValueError("检测到多个相同规格显示器，无法安全判断目标，请重新选择聊天区域")


def resolve_output_index(
    outputs: Iterable[DxcamOutput],
    *,
    saved_output_idx: int,
    expected_size: tuple[int, int] | None = None,
    primary: bool | None = None,
    device_idx: int = 0,
) -> int:
    """Compatibility wrapper returning only ``output_idx``."""
    return resolve_output(
        outputs,
        saved_device_idx=device_idx,
        saved_output_idx=saved_output_idx,
        expected_size=expected_size,
        primary=primary,
    ).output_idx
