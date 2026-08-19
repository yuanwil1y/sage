from __future__ import annotations

import sys
import types

import numpy as np

from screen.screen_capture import ScreenCapture


class _Camera:
    def grab(self, *, region, new_frame_only):  # noqa: ARG002
        return np.zeros((2, 2, 3), dtype=np.uint8)


def test_screen_capture_requests_bgr_and_validates_physical_output(monkeypatch):
    calls = []
    fake_dxcam = types.SimpleNamespace(
        output_info=lambda: "Device[0] Output[0]: Res:(2560, 1600) Rot:0 Primary:True\n",
        create=lambda **kwargs: (calls.append(kwargs) or _Camera()),
    )
    monkeypatch.setitem(sys.modules, "dxcam", fake_dxcam)

    capture = ScreenCapture(
        output_idx=0,
        screen_geometry=(0, 0, 1707, 1067),
        device_pixel_ratio=1.5,
        screen_primary=True,
    )
    frame = capture.grab((0, 0, 2, 2))

    assert frame.shape == (2, 2, 3)
    assert calls == [{"output_idx": 0, "output_color": "BGR"}]
