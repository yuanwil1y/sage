from __future__ import annotations

import sys
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gamebar_manager as manager


def _write_appx(path: Path, *, name: str, version: str, architecture: str) -> None:
    manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">
  <Identity Name="{name}" Version="{version}" Publisher="CN=Test"
            ProcessorArchitecture="{architecture}" />
</Package>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AppxManifest.xml", manifest)


def test_certificate_thumbprint_ignores_hex_like_path_heading(tmp_path, monkeypatch):
    certificate = (
        tmp_path
        / "AppData"
        / "Local"
        / "Programs"
        / "ValorantTranslator"
        / "gamebar-widget"
        / "ValorantTranslator_1.0.0.0_x64_Test"
        / "ValorantTranslator_Test.cer"
    )
    certificate.parent.mkdir(parents=True)
    certificate.write_bytes(b"cer")
    certutil = tmp_path / "certutil.exe"
    certutil.write_bytes(b"exe")
    digest = "1030d47c339c1c4890bed66dd13bf7423c46751a"
    # This mirrors the long installed path that triggered the bug.  Removing
    # every non-hex character from this heading produces exactly 40 characters,
    # even though it is not the certificate digest.
    installed_heading = (
        r"SHA1 C:\Users\Yuan\AppData\Local\Programs\ValorantTranslator"
        r"\gamebar-widget\ValorantTranslator_1.0.0.0_x64_Test"
        r"\ValorantTranslator_Test.cer:"
    )
    output = (
        f"{installed_heading}\n"
        f"{digest}\n"
        "CertUtil: -hashfile command completed successfully.\n"
    )

    monkeypatch.setattr(manager, "_certutil_path", lambda: certutil)
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=output,
            stderr="",
        ),
    )

    assert manager._certificate_thumbprint(certificate) == digest.upper()


def test_package_signer_thumbprint_uses_embedded_pkcs7(monkeypatch, tmp_path):
    payload = tmp_path / "SageWidget.msix"
    payload.write_bytes(b"package")
    digest = "1030D47C339C1C4890BED66DD13BF7423C46751A"
    scripts = []

    def fake_run_powershell(script):
        scripts.append(script)
        return digest + "\n"

    monkeypatch.setattr(manager, "_run_powershell", fake_run_powershell)

    assert manager._package_signer_thumbprint(payload) == digest
    assert "AppxSignature.p7x" in scripts[0]
    assert "SignedCms" in scripts[0]
    assert "Get-AuthenticodeSignature" not in scripts[0]


def test_certificate_trust_checks_all_reference_stores(tmp_path, monkeypatch):
    certutil = tmp_path / "certutil.exe"
    certutil.write_bytes(b"exe")
    calls = []

    def fake_in_store(store, thumbprint, *, user):
        calls.append((store, thumbprint, user))
        return user and store == "TrustedPeople"

    monkeypatch.setattr(manager, "_certutil_path", lambda: certutil)
    monkeypatch.setattr(manager, "_certificate_in_store", fake_in_store)

    assert manager._certificate_is_trusted("aa bb")
    assert calls[0] == ("TrustedPeople", "AABB", True)


def test_certificate_status_reports_untrusted_certificate(tmp_path, monkeypatch):
    payload = tmp_path / "SageWidget.msix"
    certificate = tmp_path / "SageWidget.cer"
    payload.write_bytes(b"package")
    certificate.write_bytes(b"certificate")
    monkeypatch.setattr(manager, "find_widget_certificate", lambda _: certificate)
    monkeypatch.setattr(manager, "_certificate_thumbprint", lambda _: "A" * 40)
    monkeypatch.setattr(manager, "_ensure_certificate_matches_payload", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_certificate_is_trusted", lambda _: False)

    status = manager.get_certificate_status(payload)

    assert not status.trusted
    assert status.detail == "证书尚未导入"


def test_certificate_must_match_package_signer(tmp_path, monkeypatch):
    payload = tmp_path / "SageWidget.msix"
    certificate = tmp_path / "SageWidget.cer"
    payload.write_bytes(b"package")
    certificate.write_bytes(b"certificate")
    monkeypatch.setattr(manager, "_package_signer_thumbprint", lambda _: "B" * 40)

    with pytest.raises(manager.WidgetCertificateRequiredError, match="签名不匹配"):
        manager._ensure_certificate_matches_payload(
            payload,
            certificate,
            certificate_thumbprint="A" * 40,
        )


def test_import_certificate_uses_all_reference_stores(tmp_path, monkeypatch):
    payload = tmp_path / "SageWidget.msix"
    certificate = tmp_path / "SageWidget.cer"
    certutil = tmp_path / "certutil.exe"
    payload.write_bytes(b"package")
    certificate.write_bytes(b"certificate")
    certutil.write_bytes(b"exe")
    commands = []
    initial = manager.CertificateStatus(
        available=True,
        trusted=False,
        path=certificate,
        thumbprint="A" * 40,
        detail="证书尚未导入到本地计算机",
    )
    imported = manager.CertificateStatus(
        available=True,
        trusted=True,
        path=certificate,
        thumbprint="A" * 40,
        detail="证书已导入到本地计算机并受 Windows 信任",
    )

    monkeypatch.setattr(manager, "find_widget_payload", lambda: payload)
    monkeypatch.setattr(manager, "find_widget_certificate", lambda _: certificate)
    monkeypatch.setattr(manager, "_certutil_path", lambda: certutil)
    monkeypatch.setattr(manager, "_certificate_thumbprint", lambda _: "A" * 40)
    monkeypatch.setattr(manager, "_ensure_certificate_matches_payload", lambda *a, **k: None)
    monkeypatch.setattr(manager, "get_certificate_status", lambda _: initial)
    monkeypatch.setattr(manager, "_wait_for_certificate_trust", lambda *a, **k: imported)
    monkeypatch.setattr(manager, "_run_elevated_powershell", commands.append)

    status = manager.import_widget_certificate()

    assert status.trusted
    assert len(commands) == 1
    assert "Cert:\\CurrentUser\\TrustedPeople" in commands[0]
    assert "Cert:\\CurrentUser\\Root" in commands[0]
    assert "Cert:\\LocalMachine\\TrustedPeople" in commands[0]
    assert "Cert:\\LocalMachine\\Root" in commands[0]


def test_find_widget_certificate_next_to_payload(tmp_path, monkeypatch):
    package_dir = tmp_path / "widget"
    package_dir.mkdir()
    payload = package_dir / "ValorantTranslator.msix"
    certificate = package_dir / "ValorantTranslator_Test.cer"
    payload.write_bytes(b"msix")
    certificate.write_bytes(b"cer")
    monkeypatch.delenv(manager.CERTIFICATE_ENV, raising=False)

    assert manager.find_widget_certificate(payload) == certificate.resolve()


def test_widget_status_offers_repair_when_certificate_is_untrusted(tmp_path, monkeypatch):
    payload = tmp_path / "ValorantTranslator.msix"
    certificate = tmp_path / "ValorantTranslator.cer"
    payload.write_bytes(b"msix")
    certificate.write_bytes(b"cer")

    monkeypatch.setattr(manager, "find_widget_payload", lambda: payload)
    monkeypatch.setattr(manager, "_certificate_thumbprint", lambda _: "ABC123")
    monkeypatch.setattr(manager, "_ensure_certificate_matches_payload", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_certificate_is_trusted", lambda _: False)
    monkeypatch.setattr(manager, "_is_installed", lambda: False)

    status = manager.get_widget_status()

    assert not status.installed
    assert status.detail == "小组件尚未准备好，可点击“修复小组件”"
    assert status.certificate.available
    assert not status.certificate.trusted


def test_install_widget_automatically_imports_untrusted_certificate(tmp_path, monkeypatch):
    payload = tmp_path / "ValorantTranslator.msix"
    payload.write_bytes(b"msix")
    certificate = manager.CertificateStatus(
        available=True,
        trusted=False,
        path=tmp_path / "ValorantTranslator.cer",
    )
    monkeypatch.setattr(manager, "find_widget_payload", lambda: payload)
    trusted = manager.CertificateStatus(
        available=True,
        trusted=True,
        path=certificate.path,
        thumbprint="A" * 40,
    )
    expected = manager.WidgetStatus(True, payload, "小组件已安装", trusted)
    imports = []
    monkeypatch.setattr(manager, "get_certificate_status", lambda _: certificate)
    monkeypatch.setattr(manager, "import_widget_certificate", lambda: imports.append(True) or trusted)
    monkeypatch.setattr(manager, "_ensure_certificate_matches_payload", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_dependency_packages_to_install", lambda _: [])
    monkeypatch.setattr(manager, "_is_installed", lambda: False)
    monkeypatch.setattr(manager, "_run_powershell", lambda _: "")
    monkeypatch.setattr(manager, "_set_loopback_exemption", lambda _: None)
    monkeypatch.setattr(manager, "get_widget_status", lambda: expected)

    assert manager.install_widget().installed
    assert imports == [True]


def test_dependency_identity_reads_manifest_without_extracting(tmp_path):
    dependency = tmp_path / "Framework.appx"
    _write_appx(
        dependency,
        name="Contoso.Framework",
        version="2.3.4.5",
        architecture="x64",
    )

    identity = manager._dependency_identity(dependency)

    assert identity.name == "Contoso.Framework"
    assert identity.version == (2, 3, 4, 5)
    assert identity.architecture == "x64"


def test_dependency_filter_skips_satisfied_framework(tmp_path, monkeypatch):
    payload = tmp_path / "Sage.msix"
    vclibs = tmp_path / "Microsoft.VCLibs.x64.appx"
    runtime = tmp_path / "Microsoft.NET.CoreRuntime.x64.appx"
    for path in (payload, vclibs, runtime):
        path.write_bytes(b"package")

    identities = {
        vclibs: manager._PackageIdentity(
            "Microsoft.VCLibs.140.00", (14, 0, 33519, 0), "x64"
        ),
        runtime: manager._PackageIdentity(
            "Microsoft.NET.CoreRuntime.2.2", (2, 2, 31331, 1), "x64"
        ),
    }
    monkeypatch.setattr(manager, "_dependency_packages", lambda _: [vclibs, runtime])
    monkeypatch.setattr(manager, "_dependency_identity", identities.__getitem__)
    monkeypatch.setattr(
        manager,
        "_installed_framework_packages",
        lambda _: {
            "microsoft.vclibs.140.00": [((14, 0, 33519, 0), "x64")],
        },
    )

    assert manager._dependency_packages_to_install(payload) == [runtime]


def test_installed_framework_query_parses_powershell_rows(monkeypatch):
    monkeypatch.setattr(
        manager,
        "_run_powershell",
        lambda _: (
            "Microsoft.VCLibs.140.00|14.0.33519.0|X64\n"
            "Microsoft.VCLibs.140.00|14.0.33519.0|X86"
        ),
    )

    installed = manager._installed_framework_packages({"Microsoft.VCLibs.140.00"})

    assert installed == {
        "microsoft.vclibs.140.00": [
            ((14, 0, 33519, 0), "x64"),
            ((14, 0, 33519, 0), "x86"),
        ]
    }


def test_dependency_filter_requires_matching_architecture_and_version(tmp_path, monkeypatch):
    payload = tmp_path / "Sage.msix"
    framework = tmp_path / "Framework.appx"
    payload.write_bytes(b"package")
    framework.write_bytes(b"package")
    identity = manager._PackageIdentity("Contoso.Framework", (2, 0, 0, 0), "x64")
    monkeypatch.setattr(manager, "_dependency_packages", lambda _: [framework])
    monkeypatch.setattr(manager, "_dependency_identity", lambda _: identity)
    monkeypatch.setattr(
        manager,
        "_installed_framework_packages",
        lambda _: {
            "contoso.framework": [
                ((3, 0, 0, 0), "x86"),
                ((1, 9, 9, 9), "x64"),
            ],
        },
    )

    assert manager._dependency_packages_to_install(payload) == [framework]


def test_install_widget_skips_installed_dependencies_and_only_stops_target(
    tmp_path, monkeypatch
):
    payload = tmp_path / "Sage.msix"
    needed = tmp_path / "Runtime.appx"
    payload.write_bytes(b"package")
    needed.write_bytes(b"package")
    certificate = manager.CertificateStatus(
        available=True,
        trusted=True,
        path=tmp_path / "Sage.cer",
        thumbprint="A" * 40,
    )
    expected_status = manager.WidgetStatus(True, payload, "小组件已安装", certificate)
    scripts = []
    loopback_changes = []

    monkeypatch.setattr(manager, "find_widget_payload", lambda: payload)
    monkeypatch.setattr(manager, "get_certificate_status", lambda _: certificate)
    monkeypatch.setattr(manager, "_ensure_certificate_matches_payload", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_dependency_packages_to_install", lambda _: [needed])
    monkeypatch.setattr(manager, "_is_installed", lambda: False)
    monkeypatch.setattr(manager, "_run_powershell", scripts.append)
    monkeypatch.setattr(manager, "_set_loopback_exemption", loopback_changes.append)
    monkeypatch.setattr(manager, "get_widget_status", lambda: expected_status)

    status = manager.install_widget()

    assert status.installed
    assert len(scripts) == 1
    assert "ForceUpdateFromAnyVersion" in scripts[0]
    assert "DeferRegistrationWhenPackagesAreInUse" in scripts[0]
    assert "-ForceApplicationShutdown" not in scripts[0]
    assert str(needed) in scripts[0]
    assert loopback_changes == [True]


def test_repair_removes_existing_package_with_reference_compatible_command(
    tmp_path, monkeypatch
):
    payload = tmp_path / "Sage.msix"
    payload.write_bytes(b"package")
    certificate = manager.CertificateStatus(
        available=True,
        trusted=True,
        path=tmp_path / "Sage.cer",
        thumbprint="A" * 40,
    )
    expected = manager.WidgetStatus(True, payload, "小组件已安装", certificate)
    scripts = []

    monkeypatch.setattr(manager, "find_widget_payload", lambda: payload)
    monkeypatch.setattr(manager, "get_certificate_status", lambda _: certificate)
    monkeypatch.setattr(manager, "_ensure_certificate_matches_payload", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_is_installed", lambda: True)
    monkeypatch.setattr(manager, "_dependency_packages_to_install", lambda _: [])
    monkeypatch.setattr(manager, "_run_powershell", scripts.append)
    monkeypatch.setattr(manager, "_set_loopback_exemption", lambda _: None)
    monkeypatch.setattr(manager, "get_widget_status", lambda: expected)

    assert manager.install_widget().installed
    assert "Remove-AppxPackage" in scripts[0]
    assert "ForceApplicationShutdown" not in scripts[0]
    assert "Add-AppxPackage" in scripts[1]


def test_set_loopback_exemption_uses_package_family_and_elevation(monkeypatch):
    scripts = []

    def fake_run(script):
        scripts.append(script)
        if len(scripts) == 1:
            return "ValorantTranslator_avvsga3yt4pz2\n"
        return ""

    monkeypatch.setattr(manager, "_run_powershell", fake_run)

    manager._set_loopback_exemption(True)

    assert len(scripts) == 2
    assert "PackageFamilyName" in scripts[0]
    assert "LoopbackExempt" in scripts[1]
    assert "-a" in scripts[1]
    assert "-n=ValorantTranslator_avvsga3yt4pz2" in scripts[1]
    assert "-Verb RunAs" in scripts[1]


def test_remove_widget_certificate_cleans_user_and_machine_stores(tmp_path, monkeypatch):
    payload = tmp_path / "ValorantTranslator.msix"
    certificate = tmp_path / "ValorantTranslator.cer"
    payload.write_bytes(b"msix")
    certificate.write_bytes(b"cer")
    certutil = tmp_path / "certutil.exe"
    certutil.write_bytes(b"exe")

    monkeypatch.setattr(manager, "find_widget_payload", lambda: payload)
    monkeypatch.setattr(manager, "find_widget_certificate", lambda _: certificate)
    monkeypatch.setattr(manager, "_certificate_thumbprint", lambda _: "ABC123")
    powershell_calls = []
    monkeypatch.setattr(manager, "_run_elevated_powershell", powershell_calls.append)
    monkeypatch.setattr(
        manager,
        "get_certificate_status",
        lambda _: manager.CertificateStatus(
            available=True,
            trusted=False,
            path=certificate,
            thumbprint="ABC123",
            detail="证书尚未导入",
        ),
    )

    status = manager.remove_widget_certificate()

    assert not status.trusted
    assert len(powershell_calls) == 1
    for location, store in manager.CERTIFICATE_LOCATIONS:
        assert f"Cert:\\{location}\\{store}" in powershell_calls[0]


def test_wait_for_certificate_trust_allows_store_propagation(monkeypatch):
    statuses = iter(
        (
            manager.CertificateStatus(available=True, trusted=False),
            manager.CertificateStatus(available=True, trusted=False),
            manager.CertificateStatus(available=True, trusted=True),
        )
    )
    sleeps = []
    monkeypatch.setattr(manager, "get_certificate_status", lambda _: next(statuses))
    monkeypatch.setattr(manager.time, "sleep", sleeps.append)

    status = manager._wait_for_certificate_trust(None, attempts=5, delay=0.1)

    assert status.trusted
    assert sleeps == [0.1, 0.1]
