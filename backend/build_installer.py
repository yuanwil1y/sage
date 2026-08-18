"""Compile the Inno Setup installer after the Full PyInstaller build exists.

Usage (from ``backend``)::

    python build_installer.py
    python build_installer.py --compression lzma2/normal

The large Hy-MT2 and Whisper weights are intentionally not part of any
payload. They are downloaded explicitly by the installed GUI's model center.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ISS = BACKEND / "installer.iss"
PACKAGE_ROOT = BACKEND / "dist" / "variants" / "full" / "ValorantTranslator"


def find_iscc(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))

    env_path = os.environ.get("VT_INNO_COMPILER")
    if env_path:
        candidates.append(Path(env_path))

    command = shutil.which("ISCC.exe") or shutil.which("iscc")
    if command:
        candidates.append(Path(command))

    for program_files in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))):
        root = Path(program_files)
        candidates.extend(
            [
                root / "Inno Setup 7" / "ISCC.exe",
                root / "Inno Setup 6" / "ISCC.exe",
            ]
        )

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "Programs"
        candidates.extend(
            [
                root / "Inno Setup 7" / "ISCC.exe",
                root / "Inno Setup 6" / "ISCC.exe",
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = "\n  ".join(str(item) for item in candidates) or "PATH 和常见安装目录"
    raise FileNotFoundError(
        "找不到 Inno Setup 编译器 ISCC.exe。请安装 Inno Setup 6/7，或设置 VT_INNO_COMPILER。\n"
        f"已检查：\n  {searched}"
    )


def validate_payloads() -> None:
    missing: list[str] = []
    if not PACKAGE_ROOT.is_dir():
        missing.append(f"full: {PACKAGE_ROOT}")
    elif not (PACKAGE_ROOT / "ValorantTranslator.exe").is_file():
        missing.append("full: ValorantTranslator.exe")

    ocr_root = PACKAGE_ROOT / "resources" / "ocr"
    if PACKAGE_ROOT.is_dir():
        for name in ("PP-OCRv6_medium_det_onnx", "PP-OCRv6_medium_rec_onnx"):
            for filename in ("inference.json", "inference.onnx", "inference.yml"):
                if not (ocr_root / name / filename).is_file():
                    missing.append(f"full: resources/ocr/{name}/{filename}")

    if PACKAGE_ROOT.is_dir():
        for relative in (Path("resources/silero_vad.onnx"), Path("native/valorant_audio_capture.exe")):
            if not (PACKAGE_ROOT / relative).is_file():
                missing.append(f"full: {relative.as_posix()}")

    widget_root = PACKAGE_ROOT / "gamebar-widget"
    if not any(widget_root.rglob("*.msix")):
        missing.append("full: gamebar-widget x64 MSIX")
    if not any(widget_root.rglob("*.cer")):
        missing.append("full: gamebar-widget certificate")

    if missing:
        raise FileNotFoundError(
            "安装包输入不完整，请先运行 `python build_package.py`：\n  "
            + "\n  ".join(missing)
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compile the Inno Setup installer")
    parser.add_argument(
        "--compression",
        choices=("lzma2/fast", "lzma2/normal"),
        default="lzma2/fast",
        help="Inno compression profile; fast is the default for shorter builds",
    )
    parser.add_argument("--iscc", help="path to ISCC.exe; otherwise auto-detect")
    args = parser.parse_args(argv)

    if not ISS.is_file():
        raise FileNotFoundError(f"找不到 Inno 脚本: {ISS}")
    validate_payloads()
    iscc = find_iscc(args.iscc)
    output = BACKEND / "dist" / "installer" / "Sage_Setup.exe"
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"[Inno] 编译器: {iscc}")
    print(f"[Inno] 压缩: {args.compression}")
    print(f"[Inno] 输出: {output}")
    subprocess.run(
        [str(iscc), f"/DCompressionMode={args.compression}", str(ISS)],
        cwd=BACKEND,
        check=True,
    )
    if not output.is_file():
        raise FileNotFoundError(f"ISCC 返回成功但未生成安装包: {output}")
    print(f"[完成] {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
