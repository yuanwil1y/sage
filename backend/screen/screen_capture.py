"""屏幕截图（规格文档第 26、27 节）。

DXcam 捕获指定 ROI；输出 BGR uint8 H×W×3 NumPy。
区域由用户在 Desktop UI 框选（ROI 坐标保存在 config）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import numpy as np

from screen.display_mapping import parse_output_info, physical_size, resolve_output_index

log = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]  # (left, top, right, bottom)


class ScreenCapture:
    """DXcam 封装。camera 惰性初始化（避免 import 即开 DXGI）。"""

    def __init__(
        self,
        output_idx: int = 0,
        *,
        screen_geometry: tuple[int, int, int, int] | None = None,
        device_pixel_ratio: float | None = None,
        screen_primary: bool | None = None,
    ) -> None:
        self._output_idx = output_idx
        self._screen_geometry = screen_geometry
        self._device_pixel_ratio = device_pixel_ratio
        self._screen_primary = screen_primary
        self._camera: Any = None

    def _ensure_camera(self) -> Any:
        if self._camera is None:
            import dxcam

            output_idx = self._output_idx
            output_info = getattr(dxcam, "output_info", None)
            if (
                callable(output_info)
                and self._screen_geometry is not None
                and self._device_pixel_ratio is not None
            ):
                try:
                    outputs = parse_output_info(output_info())
                    if outputs:
                        output_idx = resolve_output_index(
                            outputs,
                            saved_output_idx=self._output_idx,
                            expected_size=physical_size(
                                self._screen_geometry, self._device_pixel_ratio
                            ),
                            primary=self._screen_primary,
                        )
                        if output_idx != self._output_idx:
                            log.info(
                                "显示器顺序已变化，ROI 输出从 %d 映射到 %d",
                                self._output_idx,
                                output_idx,
                            )
                except Exception as exc:
                    raise RuntimeError(f"无法确认聊天区域所在显示器：{exc}") from exc

            self._camera = dxcam.create(output_idx=output_idx, output_color="BGR")
            log.info("DXcam camera 已创建（output_idx=%d，BGR）", output_idx)
        return self._camera

    def grab(self, region: Region) -> np.ndarray:
        """抓取指定 ROI 一帧，返回 BGR uint8 H×W×3。"""
        camera = self._ensure_camera()
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
