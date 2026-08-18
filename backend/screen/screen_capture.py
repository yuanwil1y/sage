"""屏幕截图（规格文档第 26、27 节）。

DXcam 捕获指定 ROI；输出 BGR uint8 H×W×3 NumPy。
区域由用户在 Desktop UI 框选（ROI 坐标保存在 config）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]  # (left, top, right, bottom)


class ScreenCapture:
    """DXcam 封装。camera 惰性初始化（避免 import 即开 DXGI）。"""

    def __init__(self, output_idx: int = 0) -> None:
        self._output_idx = output_idx
        self._camera: Any = None

    def _ensure_camera(self) -> Any:
        if self._camera is None:
            import dxcam

            self._camera = dxcam.create(output_idx=self._output_idx)
            log.info("DXcam camera 已创建（output_idx=%d）", self._output_idx)
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
