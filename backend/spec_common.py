"""PyInstaller options for the single complete Sage application."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


# PaddleX checks these distributions through ``importlib.metadata`` before it
# creates the lightweight OCR pipeline.  PyInstaller discovers their modules
# and native libraries, but does not automatically include every dist-info
# directory.  Without the metadata, PaddleX incorrectly reports that the
# bundled OCR dependencies are missing.
OCR_CORE_DISTRIBUTIONS = (
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
)

# The voice stack contains several native extensions.  PyInstaller's hooks
# collect their modules and DLLs, while the metadata below preserves runtime
# version/dependency discovery through ``importlib.metadata``.  This mirrors
# the explicit metadata handling above for PaddleX instead of relying on an
# incidental transitive hook.
VOICE_RUNTIME_DISTRIBUTIONS = (
    "faster-whisper",
    "ctranslate2",
    "onnxruntime",
    "soxr",
    "av",
    "tokenizers",
    "huggingface-hub",
)


def executable_icon() -> str | None:
    """Return the checked-in Sage icon for frozen executables."""
    icon_path = Path(__file__).resolve().parent / "resources" / "Sage.ico"
    return str(icon_path) if icon_path.is_file() else None


def spec_options() -> dict:
    """Return ``Analysis`` options for OCR, voice chat and the desktop UI."""
    # PaddleOCR 3.x delegates pipeline construction to PaddleX.  The OCR
    # pipeline YAML lives in PaddleX's package data (not in paddleocr), and
    # PyInstaller does not collect it from the import graph automatically.
    # Without this file the installed build fails with:
    # "The pipeline (OCR) does not exist!".
    datas = []
    package_identity = Path(__file__).resolve().parent / "config" / "package_identity.json"
    if package_identity.is_file():
        datas.append((str(package_identity), "config"))
    datas.extend(
        collect_data_files("paddlex", includes=["configs/pipelines/OCR.yaml"])
    )
    for distribution in OCR_CORE_DISTRIBUTIONS + VOICE_RUNTIME_DISTRIBUTIONS:
        datas.extend(copy_metadata(distribution))
    hiddenimports = [
        # These imports are reached from functions or from importlib at runtime.
        "ui.control_window",
        "ui.model_manager",
    ]

    hiddenimports.extend(
        [
            "ocr.chat_worker",
            "ocr.ocr_engine",
            "screen.screen_capture",
            "ui.roi_selector",
            "dxcam",
            "cv2",
            "onnxruntime",
            "paddleocr",
            "audio.capture_reader",
            "audio.pipeline",
            "audio.transcriber",
            "audio.vad",
            "audio.onnx_vad",
            "process.process_finder",
            "faster_whisper",
            "ctranslate2",
            "av",
            "tokenizers",
            "huggingface_hub",
            "soxr",
        ]
    )

    excludes = [
        # PaddleOCR 3.x scans optional GenAI/audio backends at import time.  The
        # project uses the local CPU ONNX OCR path, never torch VAD.
        "torch",
        "torchaudio",
    ]
    return {
        "pathex": [],
        "binaries": [],
        "datas": datas,
        "hiddenimports": sorted(set(hiddenimports)),
        "hookspath": [],
        "hooksconfig": {},
        "runtime_hooks": [],
        "excludes": sorted(set(excludes)),
        "noarchive": False,
        "optimize": 0,
    }
