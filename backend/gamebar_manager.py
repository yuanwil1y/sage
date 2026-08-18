"""安装、卸载和检查 Sage Game Bar 小组件。

Game Bar 小组件是独立的 UWP/MSIX 应用，不会随着 PyInstaller 自动出现。
安装器会以管理员权限完成证书、依赖、MSIX 和回环权限的一体化安装；
GUI 中的初始化入口使用相同流程进行修复。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


PACKAGE_NAME = "ValorantTranslator"
CERTIFICATE_ENV = "VT_GAMEBAR_CERTIFICATE"
CERTIFICATE_LOCATIONS = (
    ("CurrentUser", "TrustedPeople"),
    ("CurrentUser", "Root"),
    ("LocalMachine", "TrustedPeople"),
    ("LocalMachine", "Root"),
)
SIGNED_PACKAGE_SUFFIXES = {".appx", ".appxbundle", ".msix", ".msixbundle"}


@dataclass(frozen=True)
class CertificateStatus:
    """Status of the self-signed certificate used by the widget package."""

    available: bool = False
    trusted: bool = False
    path: Path | None = None
    thumbprint: str | None = None
    detail: str = "未找到证书"


@dataclass(frozen=True)
class WidgetStatus:
    installed: bool
    payload: Path | None
    detail: str
    certificate: CertificateStatus = field(default_factory=CertificateStatus)


class WidgetCertificateRequiredError(RuntimeError):
    """Raised when a self-signed widget package is not trusted yet."""


@dataclass(frozen=True)
class _PackageIdentity:
    """Identity fields needed to decide whether a framework is already usable."""

    name: str
    version: tuple[int, int, int, int]
    architecture: str


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _widget_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    override = os.environ.get("VT_GAMEBAR_PACKAGE")
    if override:
        roots.append(Path(override))

    root = _app_root()
    roots.extend(
        (
            root / "gamebar-widget",
            root / "gamebar-widget" / "AppPackages",
            root / "widget",
        )
    )

    # Keep the source checkout convenient for development builds.
    if not getattr(sys, "frozen", False):
        roots.extend(
            (
                root / "gamebar-widget" / "AppPackages",
                root / "gamebar-widget",
            )
        )

    unique: list[Path] = []
    for candidate in roots:
        candidate = candidate.resolve()
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def find_widget_payload() -> Path | None:
    """Find an MSIX/APPX payload or a development AppxManifest.xml."""

    override = os.environ.get("VT_GAMEBAR_PACKAGE")
    if override:
        overridden = Path(override).expanduser().resolve()
        if overridden.is_file() and (
            overridden.suffix.lower() in {".msix", ".appx"}
            or overridden.name.lower() == "appxmanifest.xml"
        ):
            return overridden

    for root in _widget_roots():
        if not root.exists():
            continue
        packages = [
            path
            for pattern in ("*.msix", "*.appx")
            for path in root.rglob(pattern)
            if path.is_file() and "bundle" not in path.suffix.lower()
        ]
        if packages:
            packages.sort(
                key=lambda path: (
                    0 if "x64" in path.name.lower() else 1,
                    0 if path.suffix.lower() == ".msix" else 1,
                    str(path).lower(),
                )
            )
            return packages[0]

        manifests = [
            path
            for path in root.rglob("AppxManifest.xml")
            if path.is_file()
        ]
        if manifests:
            return sorted(manifests, key=lambda path: str(path).lower())[0]
    return None


def _dependency_packages(payload: Path) -> list[Path]:
    """Find architecture-specific framework packages beside a build output."""

    candidates: list[Path] = []
    for ancestor in (payload.parent, *payload.parents):
        for folder_name in ("x64", "amd64"):
            dependency_dir = ancestor / "Dependencies" / folder_name
            if not dependency_dir.is_dir():
                continue
            candidates.extend(
                path
                for path in dependency_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".appx", ".msix"}
            )
        if candidates:
            break
    return sorted(set(candidates), key=lambda path: str(path).lower())


def _version_tuple(value: str) -> tuple[int, int, int, int]:
    """Parse the four-part versions used by APPX package identities."""

    parts = value.strip().split(".")
    if not 1 <= len(parts) <= 4 or any(not part.isdigit() for part in parts):
        raise ValueError(f"无效的 APPX 版本号：{value}")
    numbers = [int(part) for part in parts]
    numbers.extend([0] * (4 - len(numbers)))
    return tuple(numbers)  # type: ignore[return-value]


def _dependency_identity(path: Path) -> _PackageIdentity:
    """Read one dependency package identity without installing or extracting it."""

    try:
        with zipfile.ZipFile(path) as archive:
            manifest_name = next(
                name
                for name in archive.namelist()
                if name.replace("\\", "/").lower() == "appxmanifest.xml"
            )
            manifest = ET.fromstring(archive.read(manifest_name))
    except (OSError, KeyError, StopIteration, ET.ParseError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"无法读取依赖包清单：{path.name}") from exc

    identity = manifest.find("./{*}Identity")
    if identity is None:
        raise RuntimeError(f"依赖包缺少 Identity：{path.name}")

    name = identity.get("Name", "").strip()
    version = identity.get("Version", "").strip()
    architecture = identity.get("ProcessorArchitecture", "neutral").strip().lower()
    if not name or not version:
        raise RuntimeError(f"依赖包 Identity 不完整：{path.name}")
    return _PackageIdentity(name, _version_tuple(version), architecture)


def _installed_framework_packages(
    names: set[str],
) -> dict[str, list[tuple[tuple[int, int, int, int], str]]]:
    """Query all relevant installed frameworks in one PowerShell process."""

    if not names:
        return {}
    quoted_names = ",".join(_ps_quote(name) for name in sorted(names))
    output = _run_powershell(
        f"$names = @({quoted_names}); "
        "foreach ($name in $names) { "
        "Get-AppxPackage -Name $name -PackageTypeFilter Framework "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "'{0}|{1}|{2}' -f $_.Name,$_.Version,$_.Architecture.ToString() "
        "} }"
    )

    installed: dict[str, list[tuple[tuple[int, int, int, int], str]]] = {}
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        name, version, architecture = parts
        try:
            parsed_version = _version_tuple(version)
        except ValueError:
            continue
        installed.setdefault(name.casefold(), []).append(
            (parsed_version, architecture.strip().lower())
        )
    return installed


def _dependency_packages_to_install(payload: Path) -> list[Path]:
    """Return only dependencies not already installed at a sufficient version."""

    dependencies = _dependency_packages(payload)
    if not dependencies:
        return []

    identities: list[tuple[Path, _PackageIdentity]] = []
    try:
        for dependency in dependencies:
            identities.append((dependency, _dependency_identity(dependency)))
        installed = _installed_framework_packages(
            {identity.name for _, identity in identities}
        )
    except Exception:
        # If metadata cannot be queried, preserve the safe old behavior and let
        # Windows resolve or reject every supplied dependency itself.
        return dependencies

    required: list[Path] = []
    for path, identity in identities:
        candidates = installed.get(identity.name.casefold(), [])
        matching_architectures = {identity.architecture, "neutral"}
        satisfied = any(
            version >= identity.version and architecture in matching_architectures
            for version, architecture in candidates
        )
        if not satisfied:
            required.append(path)
    return required


def _powershell_path() -> Path:
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return windows_dir / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _certutil_path() -> Path:
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return windows_dir / "System32" / "certutil.exe"


def _hidden_process_kwargs() -> dict[str, object]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    kwargs: dict[str, object] = {"creationflags": flags}
    if flags and hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _ps_quote(value: Path | str) -> str:
    """Quote a path as a PowerShell single-quoted string."""

    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str, *, timeout: float = 180.0) -> str:
    powershell = _powershell_path()
    if not powershell.exists():
        raise RuntimeError("找不到 Windows PowerShell")
    try:
        result = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            **_hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Windows 操作超过 {timeout:g} 秒仍未完成，请稍后重试。"
        ) from exc
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode != 0:
        raise RuntimeError(output or f"PowerShell 退出码 {result.returncode}")
    return output


def _run_elevated_powershell(script: str, *, timeout: float = 300.0) -> None:
    """Run one hidden elevated PowerShell process and propagate its exit code."""

    import base64

    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    launcher = (
        f"$p = Start-Process -FilePath {_ps_quote(_powershell_path())} "
        "-ArgumentList @('-NoLogo','-NoProfile','-NonInteractive',"
        "'-ExecutionPolicy','Bypass','-EncodedCommand',"
        f"{_ps_quote(encoded)}) -Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        "exit $p.ExitCode"
    )
    _run_powershell(launcher, timeout=timeout)


def _is_installed() -> bool:
    try:
        output = _run_powershell(
            f"$pkg = Get-AppxPackage -Name {_ps_quote(PACKAGE_NAME)} "
            "-ErrorAction SilentlyContinue; "
            "if ($pkg) { $pkg.PackageFullName }"
        )
    except Exception:
        return False
    return bool(output.strip())


def _set_loopback_exemption(enabled: bool) -> None:
    """Add or remove the AppContainer localhost exemption used by the widget.

    Game Bar runs the UWP page inside an AppContainer. The manifest's private
    network capability is necessary but does not by itself permit 127.0.0.1;
    the reference implementation registers this exemption after MSIX install.
    """

    family_name = _run_powershell(
        f"$pkg = Get-AppxPackage -Name {_ps_quote(PACKAGE_NAME)} "
        "-ErrorAction SilentlyContinue | Sort-Object Version -Descending | "
        "Select-Object -First 1; if ($pkg) { $pkg.PackageFamilyName }"
    ).strip()
    if not family_name:
        if enabled:
            raise RuntimeError("小组件已安装，但无法读取 Package Family Name")
        return

    operation = "-a" if enabled else "-d"
    check_net_isolation = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "CheckNetIsolation.exe"
    script = (
        f"$p = Start-Process -FilePath {_ps_quote(check_net_isolation)} "
        f"-ArgumentList @('LoopbackExempt',{_ps_quote(operation)},{_ps_quote('-n=' + family_name)}) "
        "-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
        "if ($p.ExitCode -ne 0) { exit $p.ExitCode }"
    )
    _run_powershell(script)


def find_widget_certificate(payload: Path | None = None) -> Path | None:
    """Find the public certificate shipped beside the widget package."""

    override = os.environ.get(CERTIFICATE_ENV)
    if override:
        certificate = Path(override).expanduser().resolve()
        if certificate.is_file() and certificate.suffix.lower() == ".cer":
            return certificate

    payload = payload or find_widget_payload()
    if payload is None:
        return None

    package_dir = payload.parent
    certificates = sorted(
        (
            path
            for path in package_dir.glob("*.cer")
            if path.is_file()
        ),
        key=lambda path: str(path).lower(),
    )
    return certificates[0] if certificates else None


def _certificate_thumbprint(certificate: Path) -> str:
    """Read the SHA-1 certificate thumbprint with certutil.

    ``Get-PfxCertificate`` is not available on every supported Windows image,
    while certutil ships with Windows and reports the same SHA-1 value used by
    the certificate store as its thumbprint.
    """

    certutil = _certutil_path()
    if not certutil.is_file():
        raise RuntimeError("找不到 Windows 证书工具 certutil.exe")
    result = subprocess.run(
        [str(certutil), "-hashfile", str(certificate), "SHA1"],
        capture_output=True,
        text=True,
        encoding="ascii",
        errors="replace",
        check=False,
        **_hidden_process_kwargs(),
    )
    if result.returncode != 0:
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise RuntimeError(output or f"certutil 退出码 {result.returncode}")

    thumbprint = None
    for line in result.stdout.splitlines():
        # certutil prints the certificate path in a heading before the digest.
        # A long path can coincidentally contain exactly 40 hexadecimal
        # characters, so only accept a line made entirely from SHA-1 digits
        # (apart from whitespace between byte groups).
        candidate = re.sub(r"\s+", "", line.strip())
        if re.fullmatch(r"[0-9a-fA-F]{40}", candidate):
            thumbprint = candidate.upper()
            break
    if not thumbprint:
        raise RuntimeError(f"无法读取证书指纹：{certificate}")
    return thumbprint


def _package_signer_thumbprint(payload: Path) -> str:
    """Read the signer thumbprint embedded in a signed APPX/MSIX package."""

    script = (
        "Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop; "
        "Add-Type -AssemblyName System.Security -ErrorAction Stop; "
        f"$archive = [System.IO.Compression.ZipFile]::OpenRead({_ps_quote(payload)}); "
        "try { "
        "$entry = $archive.GetEntry('AppxSignature.p7x'); "
        "if ($null -eq $entry) { throw 'Package signature is missing' }; "
        "$stream = $entry.Open(); "
        "try { "
        "$memory = New-Object System.IO.MemoryStream; "
        "$stream.CopyTo($memory); $bytes = $memory.ToArray(); "
        "if ($bytes.Length -lt 5 -or "
        "[System.Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne 'PKCX') { "
        "throw 'Invalid package signature header' }; "
        "$cms = New-Object System.Security.Cryptography.Pkcs.SignedCms; "
        "$cms.Decode($bytes[4..($bytes.Length - 1)]); "
        "if ($cms.SignerInfos.Count -lt 1) { throw 'Package signer is missing' }; "
        "$cms.SignerInfos[0].Certificate.Thumbprint "
        "} finally { $stream.Dispose() } "
        "} finally { $archive.Dispose() }"
    )
    output = _run_powershell(script)
    for line in output.splitlines():
        candidate = re.sub(r"\s+", "", line.strip())
        if re.fullmatch(r"[0-9a-fA-F]{40}", candidate):
            return candidate.upper()
    raise RuntimeError(f"无法读取小组件安装包的签名证书：{payload}")


def _ensure_certificate_matches_payload(
    payload: Path | None,
    certificate: Path,
    *,
    certificate_thumbprint: str | None = None,
) -> None:
    """Refuse to trust a certificate that did not sign the selected package."""

    if payload is None or payload.suffix.lower() not in SIGNED_PACKAGE_SUFFIXES:
        return
    certificate_thumbprint = certificate_thumbprint or _certificate_thumbprint(certificate)
    signer_thumbprint = _package_signer_thumbprint(payload)
    if signer_thumbprint != certificate_thumbprint:
        raise WidgetCertificateRequiredError(
            "随安装包提供的证书与 Game Bar 小组件签名不匹配，请重新安装 Sage。"
        )


def _certificate_in_store(store: str, thumbprint: str, *, user: bool) -> bool:
    """Check one Windows certificate store without relying on PowerShell providers."""

    certutil = _certutil_path()
    arguments = [str(certutil)]
    if user:
        arguments.append("-user")
    arguments.extend(("-store", store, thumbprint))
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **_hidden_process_kwargs(),
    )
    return result.returncode == 0


def _certificate_is_trusted(thumbprint: str) -> bool:
    """Check the four certificate locations used by the reference installer."""

    normalized = "".join(thumbprint.split()).upper()
    if not _certutil_path().is_file():
        return False
    return any(
        _certificate_in_store(store, normalized, user=location == "CurrentUser")
        for location, store in CERTIFICATE_LOCATIONS
    )


def get_certificate_status(payload: Path | None = None) -> CertificateStatus:
    """Return whether the widget's public certificate is trusted by Windows."""

    certificate = find_widget_certificate(payload)
    if certificate is None:
        return CertificateStatus(detail="未找到小组件证书")

    try:
        thumbprint = _certificate_thumbprint(certificate)
        _ensure_certificate_matches_payload(
            payload,
            certificate,
            certificate_thumbprint=thumbprint,
        )
        trusted = _certificate_is_trusted(thumbprint)
    except Exception as exc:
        return CertificateStatus(
            available=True,
            path=certificate,
            detail=f"证书无法使用：{exc}",
        )

    if trusted:
        detail = "证书已导入并受 Windows 信任"
    else:
        detail = "证书尚未导入"

    return CertificateStatus(
        available=True,
        trusted=trusted,
        path=certificate,
        thumbprint=thumbprint,
        detail=detail,
    )


def _wait_for_certificate_trust(
    payload: Path | None,
    *,
    attempts: int,
    delay: float = 0.25,
) -> CertificateStatus:
    """Wait briefly for Windows certificate-store changes to become visible."""

    attempts = max(1, attempts)
    status = get_certificate_status(payload)
    for _ in range(attempts - 1):
        if status.trusted:
            return status
        time.sleep(delay)
        status = get_certificate_status(payload)
    return status


def get_widget_status() -> WidgetStatus:
    payload = find_widget_payload()
    certificate = get_certificate_status(payload)
    installed = _is_installed()
    if installed:
        return WidgetStatus(True, payload, "小组件已安装", certificate)
    if payload is None:
        return WidgetStatus(False, None, "当前安装包没有包含 Game Bar 小组件包", certificate)
    if not certificate.available:
        return WidgetStatus(False, payload, "小组件包已找到，但缺少安装证书", certificate)
    if not certificate.trusted:
        return WidgetStatus(False, payload, "小组件尚未准备好，可点击“修复小组件”", certificate)
    return WidgetStatus(False, payload, "证书已准备好，可点击“修复小组件”完成安装", certificate)


def import_widget_certificate() -> CertificateStatus:
    """Import the public certificate into all stores used by the reference app."""

    payload = find_widget_payload()
    certificate = find_widget_certificate(payload)
    if certificate is None:
        raise FileNotFoundError(
            "没有找到小组件证书。请确认安装包中包含 gamebar-widget 文件夹和 .cer 文件。"
        )
    if not _certutil_path().is_file():
        raise RuntimeError("找不到 Windows 证书工具 certutil.exe")

    thumbprint = _certificate_thumbprint(certificate)
    _ensure_certificate_matches_payload(
        payload,
        certificate,
        certificate_thumbprint=thumbprint,
    )
    current = get_certificate_status(payload)
    if current.trusted:
        return current

    stores = ",".join(
        _ps_quote(f"Cert:\\{location}\\{store}")
        for location, store in CERTIFICATE_LOCATIONS
    )
    script = (
        "$ErrorActionPreference = 'Continue'; "
        f"$certificate = {_ps_quote(certificate)}; "
        f"$stores = @({stores}); $imported = 0; "
        "foreach ($store in $stores) { try { "
        "Import-Certificate -FilePath $certificate -CertStoreLocation $store "
        "-ErrorAction Stop | Out-Null; $imported++ "
        "} catch { } }; if ($imported -eq 0) { exit 1 }"
    )
    try:
        _run_elevated_powershell(script)
    except Exception as exc:
        raise WidgetCertificateRequiredError(
            "证书没有导入到本地计算机。请在 Windows 管理员确认窗口中选择“是”。"
        ) from exc

    updated = _wait_for_certificate_trust(payload, attempts=20)
    if not updated.trusted:
        raise WidgetCertificateRequiredError(
            "导入程序已结束，但 Windows 未在标准信任证书库中检测到"
            f"该证书（指纹 {thumbprint}）。请检查管理员权限或系统证书策略。"
        )
    return updated


def remove_widget_certificate() -> CertificateStatus:
    """Remove the bundled certificate from all four reference-app stores."""

    payload = find_widget_payload()
    certificate = find_widget_certificate(payload)
    if certificate is None:
        raise FileNotFoundError(
            "没有找到小组件证书，无法确定要删除的证书。请重新安装后再执行清理。"
        )
    if not _certutil_path().is_file():
        raise RuntimeError("找不到 Windows 证书工具 certutil.exe")

    thumbprint = _certificate_thumbprint(certificate)
    stores = ",".join(
        _ps_quote(f"Cert:\\{location}\\{store}")
        for location, store in CERTIFICATE_LOCATIONS
    )
    script = (
        "$ErrorActionPreference = 'Continue'; "
        f"$thumbprint = {_ps_quote(thumbprint)}; $stores = @({stores}); "
        "foreach ($store in $stores) { "
        "$path = Join-Path $store $thumbprint; "
        "if (Test-Path -LiteralPath $path) { "
        "Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue "
        "} }"
    )
    _run_elevated_powershell(script)

    updated = get_certificate_status(payload)
    if updated.trusted:
        raise RuntimeError("小组件证书仍在 Windows 信任列表中，请确认管理员提示后重试。")
    return updated


def install_widget() -> WidgetStatus:
    payload = find_widget_payload()
    if payload is None:
        raise FileNotFoundError(
            "没有找到 Game Bar 小组件包。请把构建生成的 MSIX 放入安装目录的 gamebar-widget 文件夹。"
        )

    certificate = get_certificate_status(payload)
    if not certificate.available:
        raise WidgetCertificateRequiredError(
            "当前小组件包没有附带证书，无法进行自签名安装。请重新构建并随包提供 .cer 文件。"
        )
    if not certificate.trusted:
        certificate = import_widget_certificate()

    if certificate.path is not None:
        _ensure_certificate_matches_payload(
            payload,
            certificate.path,
            certificate_thumbprint=certificate.thumbprint,
        )

    # The reference installer avoids in-place registration of an equal/newer
    # package. For Sage's repair action, remove the existing registration first
    # and then perform a clean Add-AppxPackage pass.
    if _is_installed():
        _run_powershell(
            "Get-Process -Name 'SageWidgetService' -ErrorAction SilentlyContinue | "
            "Stop-Process -Force -ErrorAction SilentlyContinue; "
            f"Get-AppxPackage -Name {_ps_quote(PACKAGE_NAME)} "
            "-ErrorAction SilentlyContinue | Remove-AppxPackage -ErrorAction Stop"
        )

    if payload.name.lower() == "appxmanifest.xml":
        dependency_commands = " ".join(
            f"Add-AppxPackage -Path {_ps_quote(path)} "
            "-ErrorAction Stop;"
            for path in _dependency_packages_to_install(payload)
        )
        script = (
            f"{dependency_commands} Add-AppxPackage -Register {_ps_quote(payload)} "
            "-ForceTargetApplicationShutdown -ErrorAction Stop"
        )
    else:
        dependencies = _dependency_packages_to_install(payload)
        dependency_assignment = ""
        if dependencies:
            dependency_assignment = "$params.DependencyPath = @(" + ",".join(
                _ps_quote(path) for path in dependencies
            ) + "); "
        script = (
            "$command = Get-Command Add-AppxPackage -ErrorAction Stop; "
            f"$params = @{{ Path = {_ps_quote(payload)}; ErrorAction = 'Stop' }}; "
            f"{dependency_assignment}"
            "if ($command.Parameters.ContainsKey('ForceUpdateFromAnyVersion')) { "
            "$params.ForceUpdateFromAnyVersion = $true }; "
            "if ($command.Parameters.ContainsKey('DeferRegistrationWhenPackagesAreInUse')) { "
            "$params.DeferRegistrationWhenPackagesAreInUse = $true }; "
            "Add-AppxPackage @params"
        )
    _run_powershell(script)
    _set_loopback_exemption(True)
    status = get_widget_status()
    if not status.installed:
        raise RuntimeError("小组件安装命令已结束，但 Windows 没有检测到已注册的软件包")
    return status


def uninstall_widget() -> WidgetStatus:
    # The full-trust local service lives in WindowsApps rather than the desktop
    # install directory. Stop it first so package removal is not blocked by a
    # file lock and so no background process survives a user-requested cleanup.
    _set_loopback_exemption(False)
    _run_powershell(
        "Get-Process -Name 'SageWidgetService' -ErrorAction SilentlyContinue | "
        "Stop-Process -Force -ErrorAction SilentlyContinue; "
        f"Get-AppxPackage -Name {_ps_quote(PACKAGE_NAME)} "
        "-ErrorAction SilentlyContinue | "
        "Remove-AppxPackage -ErrorAction SilentlyContinue"
    )
    return get_widget_status()
