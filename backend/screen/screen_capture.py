"""屏幕截图（规格文档第 26、27 节）。

DXcam 捕获指定 ROI；输出 BGR uint8 H×W×3 NumPy。ROI 必须是所选
DXcam output 内部的物理像素坐标，而不是 Windows 虚拟桌面绝对坐标。
"""

from __future__ import annotations

import logging
from typing import Any, Tuple

import numpy as np

from screen.display_mapping import parse_output_info, physical_size, resolve_output

log = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]  # output-local (left, top, right, bottom)


class ScreenCapture:
    """DXcam 封装。camera 惰性初始化（避免 import 即开 DXGI）。"""

    def __init__(
        self,
        output_idx: int = 0,
        *,
        device_idx: int = 0,
        screen_geometry: tuple[int, int, int, int] | None = None,
        device_pixel_ratio: float | None = None,
        screen_primary: bool | None = None,
    ) -> None:
        self._device_idx = device_idx
        self._output_idx = output_idx
        self._screen_geometry = screen_geometry
        self._device_pixel_ratio = device_pixel_ratio
        self._screen_primary = screen_primary
        self._camera: Any = None
        self._resolved_device_idx = device_idx
        self._resolved_output_idx = output_idx

    def _ensure_camera(self) -> Any:
        if self._camera is None:
            import dxcam

            device_idx = self._device_idx
            output_idx = self._output_idx
            output_info = getattr(dxcam, "output_info", None)
            if (
                callable(output_info)
                and self._screen_geometry is not None
                and self._device_pixel_ratio is not None
            ):
                try:
                    outputs = parse_output_info(output_info())
                    if not outputs:
                        raise ValueError("DXcam 未返回可解析的显示器列表")
                    resolved = resolve_output(
                        outputs,
                        saved_device_idx=self._device_idx,
                        saved_output_idx=self._output_idx,
                        expected_size=physical_size(
                            self._screen_geometry, self._device_pixel_ratio
                        ),
                        primary=self._screen_primary,
                        prefer_saved=True,
                    )
                    device_idx = resolved.device_idx
                    output_idx = resolved.output_idx
                    if (device_idx, output_idx) != (
                        self._device_idx,
                        self._output_idx,
                    ):
                        log.info(
                            "显示器枚举已变化，ROI 输出从 Device[%d] Output[%d] "
                            "映射到 Device[%d] Output[%d]",
                            self._device_idx,
                            self._output_idx,
                            device_idx,
                            output_idx,
                        )
                except Exception as exc:
                    raise RuntimeError(f"无法确认聊天区域所在显示器：{exc}") from exc

            self._camera = dxcam.create(
                device_idx=device_idx,
                output_idx=output_idx,
                output_color="BGR",
            )
            self._resolved_device_idx = device_idx
            self._resolved_output_idx = output_idx
            log.info(
                "DXcam camera 已创建（device_idx=%d, output_idx=%d, BGR）",
                device_idx,
                output_idx,
            )
        return self._camera

    def grab(self, region: Region) -> np.ndarray:
        """抓取 output-local ROI 一帧，返回 BGR uint8 H×W×3。"""
        left, top, right, bottom = region
        if left < 0 or top < 0 or right <= left or bottom <= top:
            raise ValueError(f"无效的 DXcam output-local region: {region}")
        camera = self._ensure_camera()
        width = getattr(camera, "width", None)
        height = getattr(camera, "height", None)
        if isinstance(width, int) and isinstance(height, int):
            if right > width or bottom > height:
                raise ValueError(
                    f"聊天区域 {region} 超出目标显示器 {width}x{height}，请重新选择聊天区域"
                )
        frame = camera.grab(region=region, new_frame_only=False)
        if frame is None:
            raise RuntimeError(f"DXcam 抓取失败（region={region}），可能无可用显示器/被独占")
        return frame

    def release(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None
