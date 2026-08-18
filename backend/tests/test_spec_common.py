from __future__ import annotations

from spec_common import (
    OCR_CORE_DISTRIBUTIONS,
    VOICE_RUNTIME_DISTRIBUTIONS,
    spec_options,
)


def _has_distribution_metadata(options: dict, distribution: str) -> bool:
    destinations = {destination.lower() for _, destination in options["datas"]}
    normalized = distribution.replace("-", "_").lower()
    return any(
        destination.endswith(".dist-info") and destination.startswith(normalized)
        for destination in destinations
    )


def test_full_build_includes_paddlex_ocr_core_distribution_metadata():
    options = spec_options()

    for distribution in OCR_CORE_DISTRIBUTIONS:
        assert _has_distribution_metadata(options, distribution), distribution


def test_full_build_includes_voice_runtime_distribution_metadata():
    options = spec_options()

    for distribution in VOICE_RUNTIME_DISTRIBUTIONS:
        assert _has_distribution_metadata(options, distribution), distribution


def test_voice_native_packages_are_explicit_hidden_imports():
    hidden_imports = set(spec_options()["hiddenimports"])

    assert {
        "av",
        "ctranslate2",
        "faster_whisper",
        "huggingface_hub",
        "onnxruntime",
        "soxr",
        "tokenizers",
    } <= hidden_imports
