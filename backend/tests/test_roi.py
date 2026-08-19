from __future__ import annotations

import json

import pytest

from config.roi import RoiConfig, load_roi_config, save_roi_config


def test_roi_roundtrip(tmp_path):
    config = RoiConfig(
        device_idx=1,
        output_idx=2,
        left=20,
        top=30,
        right=640,
        bottom=400,
    )
    path = tmp_path / "roi.json"

    assert save_roi_config(config, path) == path
    assert load_roi_config(path) == config
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == config.to_dict()
    assert data["coordinate_space"] == "output"
    assert data["device_idx"] == 1


def test_roi_roundtrip_preserves_screen_fingerprint(tmp_path):
    config = RoiConfig(
        output_idx=0,
        left=100,
        top=200,
        right=500,
        bottom=400,
        screen_name="MNG007DA6-4",
        screen_serial="SERIAL-1",
        screen_geometry=(-1707, 0, 1707, 1067),
        device_pixel_ratio=1.5,
        screen_primary=False,
    )
    path = tmp_path / "roi.json"

    assert load_roi_config(save_roi_config(config, path)) == config


def test_legacy_positive_virtual_offset_converts_to_output_local(tmp_path):
    path = tmp_path / "roi.json"
    path.write_text(
        json.dumps(
            {
                "output_idx": 1,
                "left": 2020,
                "top": 100,
                "right": 2520,
                "bottom": 500,
                "screen_geometry": [1920, 0, 1920, 1080],
                "device_pixel_ratio": 1.0,
                "screen_primary": False,
            }
        ),
        encoding="utf-8",
    )

    config = load_roi_config(path)
    assert config is not None
    assert config.coordinate_space == "legacy-desktop"
    assert config.region == (100, 100, 600, 500)


def test_legacy_negative_virtual_offset_converts_to_output_local(tmp_path):
    path = tmp_path / "roi.json"
    path.write_text(
        json.dumps(
            {
                "output_idx": 1,
                "left": -1600,
                "top": 120,
                "right": -1100,
                "bottom": 520,
                "screen_geometry": [-1700, 0, 1700, 1000],
            }
        ),
        encoding="utf-8",
    )

    config = load_roi_config(path)
    assert config is not None
    assert config.region == (100, 120, 600, 520)


def test_legacy_roi_without_geometry_requires_reselection():
    config = RoiConfig.from_mapping(
        {
            "output_idx": 1,
            "left": 2000,
            "top": 100,
            "right": 2500,
            "bottom": 500,
        }
    )
    with pytest.raises(ValueError, match="重新选择"):
        _ = config.region


def test_missing_or_invalid_roi_is_unconfigured(tmp_path):
    path = tmp_path / "roi.json"
    assert load_roi_config(path) is None

    path.write_text("{not json", encoding="utf-8")
    assert load_roi_config(path) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"device_idx": -1, "output_idx": 0, "left": 0, "top": 0, "right": 10, "bottom": 10},
        {"output_idx": -1, "left": 0, "top": 0, "right": 10, "bottom": 10},
        {"output_idx": 0, "left": -1, "top": 0, "right": 10, "bottom": 10},
        {"output_idx": 0, "left": 10, "top": 0, "right": 10, "bottom": 10},
        {"output_idx": 0, "left": 0, "top": 10, "right": 10, "bottom": 10},
    ],
)
def test_invalid_roi_rejected(kwargs):
    with pytest.raises(ValueError):
        RoiConfig(**kwargs)
