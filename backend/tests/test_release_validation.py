from __future__ import annotations

import sys
import zipfile
from types import SimpleNamespace

import pytest

from build_package import package_has_embedded_signature
from release_validation import validate_release_metadata
from windows_capabilities import (
    MIN_PROCESS_LOOPBACK_BUILD,
    UnsupportedWindowsBuildError,
    process_loopback_support,
    require_process_loopback_support,
    voice_capture_status,
)


def test_repository_release_metadata_is_consistent():
    assert validate_release_metadata() == []


def test_msix_signature_marker_is_required(tmp_path):
    unsigned = tmp_path / "unsigned.msix"
    with zipfile.ZipFile(unsigned, "w") as archive:
        archive.writestr("AppxManifest.xml", "<Package />")
    assert not package_has_embedded_signature(unsigned)

    signed = tmp_path / "signed.msix"
    with zipfile.ZipFile(signed, "w") as archive:
        archive.writestr("AppxManifest.xml", "<Package />")
        archive.writestr("AppxSignature.p7x", b"signature")
    assert package_has_embedded_signature(signed)


def test_process_loopback_gate_rejects_old_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys,
        "getwindowsversion",
        lambda: SimpleNamespace(build=MIN_PROCESS_LOOPBACK_BUILD - 1),
        raising=False,
    )
    support = process_loopback_support()
    assert not support.supported
    assert str(MIN_PROCESS_LOOPBACK_BUILD) in support.message
    with pytest.raises(UnsupportedWindowsBuildError):
        require_process_loopback_support()

    status = voice_capture_status(model_available=True, helper_available=True)
    assert "Voice capture unavailable" in status
    assert str(MIN_PROCESS_LOOPBACK_BUILD) in status
    assert "Text chat translation remains available" in status


def test_process_loopback_gate_accepts_supported_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys,
        "getwindowsversion",
        lambda: SimpleNamespace(build=MIN_PROCESS_LOOPBACK_BUILD),
        raising=False,
    )
    assert process_loopback_support().supported
    require_process_loopback_support()
    assert (
        voice_capture_status(model_available=True, helper_available=True)
        == "ASR: ready"
    )
    assert (
        voice_capture_status(model_available=False, helper_available=True)
        == "ASR model: not installed"
    )
    assert (
        voice_capture_status(model_available=True, helper_available=False)
        == "Audio capture helper: not installed"
    )
