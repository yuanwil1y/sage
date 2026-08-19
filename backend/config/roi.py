"""Persistent screen region configuration for the chat OCR worker."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from paths import config_dir
from screen.display_mapping import legacy_desktop_region_to_output_local

log = logging.getLogger(__name__)

_COORD_OUTPUT = "output"
_COORD_LEGACY_DESKTOP = "legacy-desktop"


@dataclass(frozen=True, slots=True)
class RoiConfig:
    """A physical-pixel region for one DXcam output.

    New configurations store coordinates relative to the selected output, not
    the Windows virtual desktop. ``device_idx`` + ``output_idx`` identify the
    DXcam camera. Configs written by the pre-fix 1.0.14 selector are loaded as
    ``legacy-desktop`` and converted lazily from their saved screen geometry.
    """

    output_idx: int
    left: int
    top: int
    right: int
    bottom: int
    device_idx: int = 0
    coordinate_space: str = _COORD_OUTPUT
    # Optional screen fingerprint. Old configs without these fields remain
    # readable where possible, but unsafe legacy coordinates require reselect.
    screen_name: str | None = None
    screen_serial: str | None = None
    screen_geometry: tuple[int, int, int, int] | None = None
    device_pixel_ratio: float | None = None
    screen_primary: bool | None = None

    def __post_init__(self) -> None:
        if self.device_idx < 0:
            raise ValueError("device_idx must be non-negative")
        if self.output_idx < 0:
            raise ValueError("output_idx must be non-negative")
        if self.coordinate_space not in {_COORD_OUTPUT, _COORD_LEGACY_DESKTOP}:
            raise ValueError(f"unknown ROI coordinate space: {self.coordinate_space}")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError(
                "ROI must have positive size: "
                f"({self.left}, {self.top}, {self.right}, {self.bottom})"
            )
        if self.coordinate_space == _COORD_OUTPUT and (self.left < 0 or self.top < 0):
            raise ValueError("output-local ROI coordinates must be non-negative")
        if self.screen_geometry is not None:
            if len(self.screen_geometry) != 4:
                raise ValueError("screen_geometry must contain four integers")
            try:
                geometry = tuple(int(value) for value in self.screen_geometry)
            except (TypeError, ValueError) as exc:
                raise ValueError("screen_geometry must contain integers") from exc
            if geometry[2] <= 0 or geometry[3] <= 0:
                raise ValueError("screen_geometry must have positive size")
            object.__setattr__(self, "screen_geometry", geometry)
        if self.device_pixel_ratio is not None and self.device_pixel_ratio <= 0:
            raise ValueError("device_pixel_ratio must be positive")

    @property
    def raw_region(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    @property
    def region(self) -> tuple[int, int, int, int]:
        """Return the DXcam-ready output-local physical region."""
        if self.coordinate_space == _COORD_OUTPUT:
            return self.raw_region
        return legacy_desktop_region_to_output_local(
            self.raw_region,
            self.screen_geometry,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "device_idx": self.device_idx,
            "output_idx": self.output_idx,
            "coordinate_space": self.coordinate_space,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }
        if self.screen_name:
            data["screen_name"] = self.screen_name
        if self.screen_serial:
            data["screen_serial"] = self.screen_serial
        if self.screen_geometry is not None:
            data["screen_geometry"] = list(self.screen_geometry)
        if self.device_pixel_ratio is not None:
            data["device_pixel_ratio"] = self.device_pixel_ratio
        if self.screen_primary is not None:
            data["screen_primary"] = self.screen_primary
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RoiConfig":
        keys = ("output_idx", "left", "top", "right", "bottom")
        missing = [key for key in keys if key not in data]
        if missing:
            raise ValueError(f"ROI config missing keys: {', '.join(missing)}")
        try:
            values: dict[str, Any] = {key: int(data[key]) for key in keys}
            values["device_idx"] = int(data.get("device_idx", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("ROI coordinates/device indices must be integers") from exc
        screen_geometry = data.get("screen_geometry")
        if screen_geometry is not None:
            try:
                screen_geometry = tuple(int(value) for value in screen_geometry)
            except (TypeError, ValueError) as exc:
                raise ValueError("screen_geometry must contain integers") from exc
        # Old files did not record a coordinate-space marker. They were written
        # by a selector that added the virtual-desktop origin to local physical
        # pixels, so treat them as legacy instead of silently misreading them.
        coordinate_space = str(data.get("coordinate_space") or _COORD_LEGACY_DESKTOP)
        values.update(
            {
                "coordinate_space": coordinate_space,
                "screen_name": str(data["screen_name"])
                if data.get("screen_name")
                else None,
                "screen_serial": str(data["screen_serial"])
                if data.get("screen_serial")
                else None,
                "screen_geometry": screen_geometry,
                "device_pixel_ratio": (
                    float(data["device_pixel_ratio"])
                    if data.get("device_pixel_ratio") is not None
                    else None
                ),
                "screen_primary": (
                    bool(data["screen_primary"])
                    if data.get("screen_primary") is not None
                    else None
                ),
            }
        )
        return cls(**values)


def default_roi_path() -> Path:
    return config_dir() / "roi.json"


def load_roi_config(path: Path | str | None = None) -> RoiConfig | None:
    """Load a saved ROI, returning ``None`` when it is not configured."""
    target = Path(path) if path is not None else default_roi_path()
    if not target.exists():
        return None
    try:
        with target.open("r", encoding="utf-8") as handle:
            return RoiConfig.from_mapping(json.load(handle))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.warning("忽略无效 ROI 配置 %s: %s", target, exc)
        return None


def save_roi_config(config: RoiConfig, path: Path | str | None = None) -> Path:
    """Atomically save an ROI config and return its destination path."""
    target = Path(path) if path is not None else default_roi_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{target.stem}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(config.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target
