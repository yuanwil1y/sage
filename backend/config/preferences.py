"""User-facing audio and OCR preferences.

The GUI stores only small, human-editable settings here.  Models and the chat
region keep their existing storage locations; this file is safe to remove and
will be recreated with sensible defaults.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from paths import config_dir


@dataclass(slots=True)
class VoicePreferences:
    """Settings for the voice activity detector."""

    vad_threshold: float = 0.50
    min_silence_ms: int = 800

    def normalize(self) -> None:
        self.vad_threshold = min(0.90, max(0.10, float(self.vad_threshold)))
        self.min_silence_ms = min(2000, max(300, int(self.min_silence_ms)))


@dataclass(slots=True)
class TextPreferences:
    """Settings for polling and accepting OCR frames."""

    poll_hz: float = 4.0
    min_score: float = 0.50
    change_threshold: float = 2.0

    def normalize(self) -> None:
        self.poll_hz = min(10.0, max(1.0, float(self.poll_hz)))
        self.min_score = min(0.95, max(0.10, float(self.min_score)))
        self.change_threshold = min(10.0, max(0.0, float(self.change_threshold)))


@dataclass(slots=True)
class AppPreferences:
    voice: VoicePreferences
    text: TextPreferences

    @classmethod
    def defaults(cls) -> "AppPreferences":
        return cls(voice=VoicePreferences(), text=TextPreferences())

    def normalize(self) -> None:
        self.voice.normalize()
        self.text.normalize()


def preferences_path() -> Path:
    return config_dir() / "preferences.json"


def _number(data: Mapping[str, Any], key: str, default: Any) -> Any:
    value = data.get(key, default)
    return default if isinstance(value, bool) else value


def load_preferences(path: Path | str | None = None) -> AppPreferences:
    """Load preferences, falling back to defaults if the file is invalid."""

    target = Path(path) if path is not None else preferences_path()
    prefs = AppPreferences.defaults()
    try:
        with target.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        voice = raw.get("voice", {}) if isinstance(raw, dict) else {}
        text = raw.get("text", {}) if isinstance(raw, dict) else {}
        if isinstance(voice, dict):
            prefs.voice.vad_threshold = _number(voice, "vad_threshold", prefs.voice.vad_threshold)
            prefs.voice.min_silence_ms = _number(voice, "min_silence_ms", prefs.voice.min_silence_ms)
        if isinstance(text, dict):
            prefs.text.poll_hz = _number(text, "poll_hz", prefs.text.poll_hz)
            prefs.text.min_score = _number(text, "min_score", prefs.text.min_score)
            prefs.text.change_threshold = _number(
                text, "change_threshold", prefs.text.change_threshold
            )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        prefs.normalize()
    except (TypeError, ValueError):
        # A hand-edited or interrupted file should never prevent the app from
        # opening; reset only the small preferences file to safe defaults.
        prefs = AppPreferences.defaults()
    return prefs


def save_preferences(prefs: AppPreferences, path: Path | str | None = None) -> Path:
    """Atomically persist preferences and return the destination path."""

    prefs.normalize()
    target = Path(path) if path is not None else preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{target.stem}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {
                    "voice": {
                        "vad_threshold": prefs.voice.vad_threshold,
                        "min_silence_ms": prefs.voice.min_silence_ms,
                    },
                    "text": {
                        "poll_hz": prefs.text.poll_hz,
                        "min_score": prefs.text.min_score,
                        "change_threshold": prefs.text.change_threshold,
                    },
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target
