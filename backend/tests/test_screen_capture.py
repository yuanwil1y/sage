from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from screen.screen_capture import ScreenCapture


class _Camera:
    width = 2560
    height = 1440

    def grab(self, *, region, new_frame_only):  # noqa: ARG002
        return np.zeros((2, 2, 3), dtype=np.uint8)

    def release(self):
        pass


def test_screen_capture_requests_bgr_and_validates_physical_output(monkeypatch):
    calls = []
    fake_dxcam = types.SimpleNamespace(
        output_info=lambda: "Device[0] Output[0]: Res:(2560, 1600) Rot:0 Primary:True\n",
        create=lambda **kwargs: (calls.append(kwargs) or _Camera()),
    )
    monkeypatch.setitem(sys.modules, "dxcam", fake_dxcam)

    capture = ScreenCapture(
        output_idx=0,
        device_idx=0,
        screen_geometry=(0, 0, 1707, 1067),
        device_pixel_ratio=1.5,
        screen_primary=True,
    )
    frame = capture.grab((0, 0, 2, 2))

    assert frame.shape == (2, 2, 3)
    assert calls == [{"device_idx": 0, "output_idx": 0, "output_color": "BGR"}]


def test_screen_capture_remaps_across_gpu_devices(monkeypatch):
    calls = []
    fake_dxcam = types.SimpleNamespace(
        output_info=lambda: (
            "Device[0] Output[0]: Res:(1920, 1080) Rot:0 Primary:True\n"
            "Device[1] Output[0]: Res:(2560, 1440) Rot:0 Primary:False\n"
        ),
        create=lambda **kwargs: (calls.append(kwargs) or _Camera()),
    )
    monkeypatch.setitem(sys.modules, "dxcam", fake_dxcam)

    capture = ScreenCapture(
        output_idx=1,
        device_idx=0,
        screen_geometry=(1920, 0, 2560, 1440),
        device_pixel_ratio=1.0,
        screen_primary=False,
    )
    capture.grab((100, 100, 600, 500))

    assert calls == [{"device_idx": 1, "output_idx": 0, "output_color": "BGR"}]


def test_screen_capture_rejects_virtual_desktop_or_out_of_bounds_regions(monkeypatch):
    fake_dxcam = types.SimpleNamespace(
        output_info=lambda: "Device[0] Output[0]: Res:(2560, 1440) Rot:0 Primary:True\n",
        create=lambda **kwargs: _Camera(),
    )
    monkeypatch.setitem(sys.modules, "dxcam", fake_dxcam)
    capture = ScreenCapture(output_idx=0)

    with pytest.raises(ValueError, match="output-local"):
        capture.grab((-100, 100, 200, 300))
    with pytest.raises(ValueError, match="超出"):
        capture.grab((2500, 100, 2700, 300))
