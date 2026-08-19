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

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RoiConfig:
    """A physical-pixel DXcam region.

    Coordinates are absolute desktop coordinates in the same coordinate
    space accepted by ``dxcam.grab(region=...)``.  Negative left/top values
    are valid for monitors positioned left/above the primary display.
    """

    output_idx: int
    left: int
    top: int
    right: int
    bottom: int
    # Optional screen fingerprint. Old configs without these fields remain
    # readable, but newly selected regions can be validated against DXcam.
    screen_name: str | None = None
    screen_serial: str | None = None
    screen_geometry: tuple[int, int, int, int] | None = None
    device_pixel_ratio: float | None = None
    screen_primary: bool | None = None

    def __post_init__(self) -> None:
        if self.output_idx < 0:
            raise ValueError("output_idx must be non-negative")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError(
                "ROI must have positive size: "
                f"({self.left}, {self.top}, {self.right}, {self.bottom})"
            )
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
    def region(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "output_idx": self.output_idx,
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
            values = {key: int(data[key]) for key in keys}
        except (TypeError, ValueError) as exc:
            raise ValueError("ROI coordinates must be integers") from exc
        screen_geometry = data.get("screen_geometry")
        if screen_geometry is not None:
            try:
                screen_geometry = tuple(int(value) for value in screen_geometry)
            except (TypeError, ValueError) as exc:
                raise ValueError("screen_geometry must contain integers") from exc
        values.update(
            {
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
    """Load a saved ROI, returning ``None`` when it is not configured.

    A malformed user config is treated as unconfigured and logged.  This
    keeps the backend startable after a manually edited config file breaks.
    """

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
