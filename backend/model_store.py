"""Model catalog and explicit model downloads.

Runtime code must never download a model implicitly.  The GUI calls this
module when the user explicitly chooses Download, while the runtime only
checks the model paths exposed here.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from paths import asr_model_dir, model_dir, ocr_model_dir

log = logging.getLogger("models")

ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str = ""


@dataclass(frozen=True)
class ModelSpec:
    key: str
    name: str
    kind: str
    source: str
    revision: str
    files: tuple[ModelFile, ...]
    target_kind: str
    description: str
    embedded: bool = False

    @property
    def expected_size(self) -> int:
        return sum(item.size for item in self.files)


HY_MT2 = ModelSpec(
    key="hy-mt2",
    name="Hy-MT2 1.8B Q4_K_M",
    kind="翻译",
    source="tencent/Hy-MT2-1.8B-GGUF",
    revision="main",
    files=(
        ModelFile(
            "Hy-MT2-1.8B-Q4_K_M.gguf",
            1_133_080_448,
            "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
        ),
    ),
    target_kind="hy-mt2",
    description="Text 和 Voice Chat 共用的日语到中文本地翻译模型",
)


WHISPER_MEDIUM = ModelSpec(
    key="whisper-medium",
    name="faster-whisper medium",
    kind="语音识别",
    source="Systran/faster-whisper-medium",
    revision="08e178d48790749d25932bbc082711ddcfdfbc4f",
    files=(
        ModelFile("config.json", 2_257),
        ModelFile("model.bin", 1_527_906_378),
        ModelFile("tokenizer.json", 2_203_239),
        ModelFile("vocabulary.txt", 459_861),
    ),
    target_kind="asr",
    description="Voice Chat 的日语语音转写模型",
)


OCR_DET = ModelSpec(
    key="ocr-det",
    name="PP-OCRv6 medium detection",
    kind="OCR",
    source="PaddlePaddle/PP-OCRv6_medium_det_onnx",
    revision="61323801669c338b7891481ec7bac61ce31b576a2",
    files=(
        ModelFile("inference.json", 312_150),
        ModelFile("inference.onnx", 62_032_837),
        ModelFile("inference.yml", 886),
    ),
    target_kind="embedded-ocr-det",
    description="检测聊天框中的文字区域",
    embedded=True,
)


OCR_REC = ModelSpec(
    key="ocr-rec",
    name="PP-OCRv6 medium recognition",
    kind="OCR",
    source="PaddlePaddle/PP-OCRv6_medium_rec_onnx",
    revision="50c7eacafc52fa7bcf4194e8cd08e46f8558504b",
    files=(
        ModelFile("inference.json", 221_814),
        ModelFile("inference.onnx", 76_554_979),
        ModelFile("inference.yml", 150_580),
    ),
    target_kind="embedded-ocr-rec",
    description="识别聊天框中的日文内容",
    embedded=True,
)


MODEL_SPECS: tuple[ModelSpec, ...] = (HY_MT2, WHISPER_MEDIUM, OCR_DET, OCR_REC)
MODEL_BY_KEY = {spec.key: spec for spec in MODEL_SPECS}
MODE_MODEL_KEYS: dict[str, tuple[str, ...]] = {
    "text": ("hy-mt2", "ocr-det", "ocr-rec"),
    "voicechat": ("hy-mt2", "whisper-medium"),
    "full": ("hy-mt2", "whisper-medium", "ocr-det", "ocr-rec"),
}


def model_specs_for_mode(mode: str) -> tuple[ModelSpec, ...]:
    """Return only the models used by one packaged edition."""
    try:
        keys = MODE_MODEL_KEYS[mode]
    except KeyError as exc:
        raise ValueError(f"未知构建模式: {mode}") from exc
    return tuple(MODEL_BY_KEY[key] for key in keys)


class DownloadCancelled(Exception):
    """Raised when the user cancels a model download."""


def get_model_spec(key: str) -> ModelSpec:
    try:
        return MODEL_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"未知模型: {key}") from exc


def model_target_dir(spec: ModelSpec) -> Path:
    if spec.target_kind == "hy-mt2":
        return model_dir()
    if spec.target_kind == "asr":
        return asr_model_dir("medium")
    if spec.target_kind == "embedded-ocr-det":
        return ocr_model_dir("PP-OCRv6_medium_det_onnx")
    if spec.target_kind == "embedded-ocr-rec":
        return ocr_model_dir("PP-OCRv6_medium_rec_onnx")
    raise ValueError(f"模型没有目标目录: {spec.key}")


def model_file_path(spec: ModelSpec, item: ModelFile) -> Path:
    return model_target_dir(spec) / item.name


def model_status(spec: ModelSpec) -> str:
    """Return one of embedded, installed, incomplete, missing, invalid."""
    target = model_target_dir(spec)
    present = [path.exists() for path in (model_file_path(spec, item) for item in spec.files)]
    if spec.embedded:
        return "embedded" if all(present) else "missing"
    if not any(present):
        return "missing"
    if not all(present):
        return "incomplete"
    for item in spec.files:
        path = model_file_path(spec, item)
        if path.stat().st_size != item.size:
            return "invalid"
    return "installed"


def verify_model(key: str) -> bool:
    """Perform the expensive checksum verification on explicit user request."""
    spec = get_model_spec(key)
    if spec.embedded:
        return model_status(spec) == "embedded"
    if model_status(spec) != "installed":
        return False
    for item in spec.files:
        if item.sha256 and _sha256(model_file_path(spec, item)) != item.sha256:
            return False
    return True


def model_size(spec: ModelSpec) -> int:
    total = 0
    for item in spec.files:
        path = model_file_path(spec, item)
        if path.exists():
            total += path.stat().st_size
    return total


def model_location(spec: ModelSpec) -> str:
    return str(model_target_dir(spec))


def download_model(
    key: str,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Download one non-embedded model atomically from a pinned HF revision."""
    spec = get_model_spec(key)
    if spec.embedded:
        raise ValueError(f"模型已内置，无需下载: {spec.name}")

    target = model_target_dir(spec)
    staging = target.with_name(f".{target.name}.download")
    staging.mkdir(parents=True, exist_ok=True)
    completed_before = 0
    for item in spec.files:
        existing = staging / item.name
        if existing.exists() and existing.stat().st_size == item.size:
            completed_before += item.size

    for item in spec.files:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        final = staging / item.name
        if final.exists() and final.stat().st_size == item.size:
            _report(progress, item.name, completed_before, spec.expected_size)
            continue
        url = f"https://huggingface.co/{spec.source}/resolve/{spec.revision}/{item.name}?download=true"
        _download_file(
            url,
            final,
            expected_size=item.size,
            completed_before=completed_before,
            total=spec.expected_size,
            progress=progress,
            cancel_event=cancel_event,
        )
        completed_before += item.size

    if spec.key == HY_MT2.key:
        digest = _sha256(staging / spec.files[0].name)
        if digest != spec.files[0].sha256:
            raise ValueError(f"{spec.name} SHA256 校验失败: {digest}")

    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    if model_status(spec) not in {"installed", "embedded"}:
        raise ValueError(f"{spec.name} 下载完成但校验失败")
    _report(progress, "完成", spec.expected_size, spec.expected_size)


def import_model(
    key: str,
    source_dir: Path | str,
    *,
    progress: ProgressCallback | None = None,
) -> None:
    """Import a previously downloaded model directory without network access.

    ``source_dir`` may be the model directory itself or a parent directory
    containing the expected files in a nested folder.  Files are copied to a
    staging directory and only become active after size/checksum validation.
    """
    spec = get_model_spec(key)
    if spec.embedded:
        raise ValueError(f"模型已内置，无需导入: {spec.name}")

    source = Path(source_dir).resolve()
    target = model_target_dir(spec).resolve()
    if not source.is_dir():
        raise ValueError(f"模型目录不存在: {source}")
    if source == target:
        raise ValueError("导入目录就是当前模型目录")

    sources: list[tuple[ModelFile, Path]] = []
    for item in spec.files:
        direct = source / item.name
        candidates = [direct] if direct.is_file() else list(source.rglob(item.name))
        candidates = [candidate for candidate in candidates if candidate.is_file()]
        if not candidates:
            raise FileNotFoundError(f"导入目录缺少文件: {item.name}")
        candidate = candidates[0]
        if candidate.stat().st_size != item.size:
            raise ValueError(
                f"{item.name} 大小不符: {candidate.stat().st_size} != {item.size}"
            )
        sources.append((item, candidate))

    staging = target.with_name(f".{target.name}.import")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        copied = 0
        for item, source_file in sources:
            destination = staging / item.name
            shutil.copy2(source_file, destination)
            copied += item.size
            _report(progress, item.name, copied, spec.expected_size)

        if spec.key == HY_MT2.key:
            digest = _sha256(staging / spec.files[0].name)
            if digest != spec.files[0].sha256:
                raise ValueError(f"{spec.name} SHA256 校验失败: {digest}")

        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    if model_status(spec) != "installed":
        raise ValueError(f"{spec.name} 导入完成但校验失败")
    _report(progress, "完成", spec.expected_size, spec.expected_size)


def delete_model(key: str) -> None:
    spec = get_model_spec(key)
    if spec.embedded:
        raise ValueError(f"内置模型不能删除: {spec.name}")
    target = model_target_dir(spec)
    if target.exists():
        shutil.rmtree(target)


def _download_file(
    url: str,
    destination: Path,
    *,
    expected_size: int,
    completed_before: int,
    total: int,
    progress: ProgressCallback | None,
    cancel_event: threading.Event | None,
) -> None:
    part = destination.with_suffix(destination.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "valorant-translator/1.0"}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        if start and getattr(response, "status", 200) == 200:
            start = 0
            part.unlink()
        mode = "ab" if start else "wb"
        downloaded = start
        with part.open(mode) as handle:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                _report(progress, destination.name, completed_before + downloaded, total)
    if downloaded != expected_size:
        raise ValueError(
            f"{destination.name} 大小不符: {downloaded} != {expected_size}"
        )
    part.replace(destination)


def _report(progress: ProgressCallback | None, label: str, done: int, total: int) -> None:
    if progress is not None:
        progress(label, done, total)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
