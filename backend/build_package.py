"""Build and assemble the Sage Full PyInstaller distribution.

Production builds are intentionally strict: every external runtime payload,
including a signed x64 Game Bar package, must be present. ``--allow-missing``
is reserved for local dependency/packaging experiments.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from release_validation import validate_release_metadata

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent
DIST_ROOT = BACKEND / "dist" / "variants" / "full"
BUILD_ROOT = BACKEND / "build" / "variants" / "full"
PACKAGE_DIR = DIST_ROOT / "ValorantTranslator"
SPEC = BACKEND / "ValorantTranslator.spec"

SRC_NATIVE = REPO / "native" / "audio-capture" / "build" / "Release" / "valorant_audio_capture.exe"
SRC_RESOURCES = BACKEND / "resources" / "valorant_ja_zh.json"
SRC_ICON = BACKEND / "resources" / "Sage.ico"
SRC_VAD_MODEL = BACKEND / "resources" / "silero_vad.onnx"
SRC_OCR = BACKEND / "resources" / "ocr"
SRC_GAMEBAR = REPO / "gamebar-widget" / "AppPackages"


def copy(src: Path, dst: Path, desc: str) -> bool:
    if not src.exists():
        print(f"[缺失] {desc} 源不存在: {src}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        print(f"[已存在] {desc}")
        return True
    print(f"[复制] {desc}: {src} -> {dst}")
    shutil.copy2(src, dst)
    print(f"        完成 ({src.stat().st_size / 1024 / 1024:.1f} MB)")
    return True


def copy_dir(src_dir: Path, dst_dir: Path, desc: str) -> bool:
    if not src_dir.exists():
        print(f"[缺失] {desc} 源不存在: {src_dir}")
        return False
    dst_dir.mkdir(parents=True, exist_ok=True)
    print(f"[复制目录] {desc}: {src_dir} -> {dst_dir}")
    for source in src_dir.iterdir():
        if source.is_file():
            shutil.copy2(source, dst_dir / source.name)
    count = len([item for item in dst_dir.iterdir() if item.is_file()])
    print(f"        完成 ({count} 个文件)")
    return True


def package_has_embedded_signature(path: Path) -> bool:
    """MSIX/APPX signing adds AppxSignature.p7x to the ZIP container."""
    try:
        with zipfile.ZipFile(path) as package:
            names = {name.lower() for name in package.namelist()}
    except (OSError, zipfile.BadZipFile):
        return False
    return "appxsignature.p7x" in names


def _package_version(path: Path) -> tuple[int, int, int, int]:
    match = re.search(r"_(\d+\.\d+\.\d+\.\d+)_", path.parent.name)
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(part) for part in match.group(1).split("."))  # type: ignore[return-value]


def copy_gamebar_widget(src_dir: Path, dst_dir: Path) -> bool:
    """Stage the newest signed x64 Game Bar payload plus cert/dependencies."""
    if not src_dir.exists():
        print(f"[缺失] Game Bar AppPackages 目录不存在: {src_dir}")
        return False

    candidates = [
        path
        for path in src_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".msix", ".appx"}
        and "x64" in path.name.lower()
        and any(
            product_name in path.name.lower()
            for product_name in ("sagegamebar.package", "valoranttranslator")
        )
    ]
    signed = [path for path in candidates if package_has_embedded_signature(path)]
    if not signed:
        if candidates:
            print("[缺失] 找到了 x64 Game Bar 包，但没有包包含 AppxSignature.p7x")
        else:
            print("[缺失] 未找到 x64 Game Bar MSIX/APPX")
        return False

    payload = max(
        signed,
        key=lambda path: (
            path.suffix.lower() == ".msix",
            _package_version(path),
            str(path).lower(),
        ),
    )
    package_dir = payload.parent
    certificates = sorted(package_dir.glob("*.cer"), key=lambda path: str(path).lower())
    if not certificates:
        print(f"[缺失] signed Game Bar 包没有配套 .cer: {package_dir}")
        return False

    dependency_dir = package_dir / "Dependencies" / "x64"
    dependencies = (
        sorted(
            (
                path
                for path in dependency_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".appx", ".msix"}
            ),
            key=lambda path: str(path).lower(),
        )
        if dependency_dir.is_dir()
        else []
    )

    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    staged_package = dst_dir / package_dir.name
    staged_package.mkdir(parents=True, exist_ok=True)
    shutil.copy2(payload, staged_package / payload.name)
    for certificate in certificates:
        shutil.copy2(certificate, staged_package / certificate.name)
    if dependencies:
        staged_dependencies = staged_package / "Dependencies" / "x64"
        staged_dependencies.mkdir(parents=True, exist_ok=True)
        for dependency in dependencies:
            shutil.copy2(dependency, staged_dependencies / dependency.name)

    print(f"[复制目录] signed Game Bar x64 小组件: {package_dir} -> {staged_package}")
    print(
        "        完成（1 个 signed MSIX/APPX、"
        f"{len(certificates)} 个证书、{len(dependencies)} 个 x64 依赖）"
    )
    return True


def run_pyinstaller() -> Path:
    destination = PACKAGE_DIR
    if destination.exists():
        shutil.rmtree(destination)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)

    print("=== PyInstaller 构建 Full 版本 ===")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST_ROOT),
            "--workpath",
            str(BUILD_ROOT),
            str(SPEC),
        ],
        cwd=BACKEND,
        check=True,
    )
    if not destination.exists():
        raise FileNotFoundError(f"PyInstaller 未生成目标目录: {destination}")
    return destination


def assemble(destination: Path, allow_missing: bool) -> None:
    print("=== 布置 Full 版本外部资源 ===")
    missing: list[str] = []

    def require_copy(ok: bool, label: str) -> None:
        if not ok:
            missing.append(label)

    require_copy(
        copy_dir(
            REPO / "runtime" / "llama.cpp",
            destination / "runtime" / "llama.cpp",
            "llama.cpp 运行时",
        ),
        "runtime/llama.cpp",
    )
    require_copy(
        copy(SRC_RESOURCES, destination / "resources" / "valorant_ja_zh.json", "术语表"),
        "backend/resources/valorant_ja_zh.json",
    )
    require_copy(
        copy(SRC_ICON, destination / "resources" / "Sage.ico", "Sage 应用图标"),
        "backend/resources/Sage.ico",
    )
    require_copy(
        copy_gamebar_widget(SRC_GAMEBAR, destination / "gamebar-widget"),
        "signed x64 Game Bar package + certificate",
    )
    require_copy(
        copy(
            SRC_NATIVE,
            destination / "native" / "valorant_audio_capture.exe",
            "native audio-capture",
        ),
        "native/audio-capture/build/Release/valorant_audio_capture.exe",
    )
    require_copy(
        copy(SRC_VAD_MODEL, destination / "resources" / "silero_vad.onnx", "Silero VAD ONNX 模型"),
        "backend/resources/silero_vad.onnx",
    )

    for ocr_model in (
        "PP-OCRv6_medium_det_onnx",
        "PP-OCRv6_medium_rec_onnx",
    ):
        require_copy(
            copy_dir(
                SRC_OCR / ocr_model,
                destination / "resources" / "ocr" / ocr_model,
                f"内置 OCR 模型 {ocr_model}",
            ),
            f"backend/resources/ocr/{ocr_model}",
        )

    if missing and not allow_missing:
        raise SystemExit(
            "[错误] Full 生产包缺少必需资源: "
            f"{', '.join(missing)}；"
            "如只需验证 PyInstaller 依赖，请显式使用 --allow-missing"
        )
    if missing:
        print(f"[警告] --allow-missing 生成的是不完整开发包: {', '.join(missing)}")
    print(f"=== Full 版本完成: {destination} ===")


def build_full(allow_missing: bool = False) -> Path:
    metadata_errors = validate_release_metadata()
    if metadata_errors:
        raise SystemExit(
            "[错误] release metadata 校验失败:\n- " + "\n- ".join(metadata_errors)
        )
    destination = run_pyinstaller()
    assemble(destination, allow_missing)
    return destination


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the Sage Full distribution")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="允许 signed Widget/运行时/helper/OCR 等资源缺失，仅生成开发包",
    )
    args = parser.parse_args(argv)
    build_full(allow_missing=args.allow_missing)


if __name__ == "__main__":
    main()
