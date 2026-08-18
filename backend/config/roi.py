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

    def __post_init__(self) -> None:
        if self.output_idx < 0:
            raise ValueError("output_idx must be non-negative")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError(
                "ROI must have positive size: "
                f"({self.left}, {self.top}, {self.right}, {self.bottom})"
            )

    @property
    def region(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def to_dict(self) -> dict[str, int]:
        return {
            "output_idx": self.output_idx,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
        }

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
