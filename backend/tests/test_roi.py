from __future__ import annotations

import json

import pytest

from config.roi import RoiConfig, load_roi_config, save_roi_config


def test_roi_roundtrip(tmp_path):
    config = RoiConfig(output_idx=1, left=-20, top=30, right=640, bottom=400)
    path = tmp_path / "roi.json"

    assert save_roi_config(config, path) == path
    assert load_roi_config(path) == config
    assert json.loads(path.read_text(encoding="utf-8")) == config.to_dict()


def test_roi_roundtrip_preserves_screen_fingerprint(tmp_path):
    config = RoiConfig(
        output_idx=0,
        left=100,
        top=200,
        right=500,
        bottom=400,
        screen_name="MNG007DA6-4",
        screen_serial="SERIAL-1",
        screen_geometry=(0, 0, 1707, 1067),
        device_pixel_ratio=1.5,
        screen_primary=True,
    )
    path = tmp_path / "roi.json"

    assert load_roi_config(save_roi_config(config, path)) == config


def test_missing_or_invalid_roi_is_unconfigured(tmp_path):
    path = tmp_path / "roi.json"
    assert load_roi_config(path) is None

    path.write_text("{not json", encoding="utf-8")
    assert load_roi_config(path) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"output_idx": -1, "left": 0, "top": 0, "right": 10, "bottom": 10},
        {"output_idx": 0, "left": 10, "top": 0, "right": 10, "bottom": 10},
        {"output_idx": 0, "left": 0, "top": 10, "right": 10, "bottom": 10},
    ],
)
def test_invalid_roi_rejected(kwargs):
    with pytest.raises(ValueError):
        RoiConfig(**kwargs)
