"""Automated release metadata checks used before packaging and in CI."""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from version import SAGE_VERSION, SAGE_VERSION_QUAD
from windows_capabilities import MIN_PROCESS_LOOPBACK_BUILD

BACKEND = Path(__file__).resolve().parent
REPO = BACKEND.parent
INSTALLER = BACKEND / "installer.iss"
MANIFEST = REPO / "gamebar-widget" / "Package.appxmanifest"
EXPECTED_REPO_URL = "https://github.com/yuanwil1y/sage"


def validate_release_metadata() -> list[str]:
    errors: list[str] = []

    installer = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', installer)
    installer_version = match.group(1) if match else None
    if installer_version != SAGE_VERSION:
        errors.append(
            f"installer version {installer_version!r} != Sage version {SAGE_VERSION!r}"
        )
    if f"AppPublisherURL={EXPECTED_REPO_URL}" not in installer:
        errors.append("installer AppPublisherURL does not point at yuanwil1y/sage")
    if f"AppSupportURL={EXPECTED_REPO_URL}/issues" not in installer:
        errors.append("installer AppSupportURL does not point at yuanwil1y/sage/issues")

    root = ET.parse(MANIFEST).getroot()
    ns = {"f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
    identity = root.find("f:Identity", ns)
    manifest_version = identity.attrib.get("Version") if identity is not None else None
    if manifest_version != SAGE_VERSION_QUAD:
        errors.append(
            f"Game Bar manifest version {manifest_version!r} != {SAGE_VERSION_QUAD!r}"
        )

    dependency = root.find("f:Dependencies/f:TargetDeviceFamily", ns)
    if dependency is None:
        errors.append("Game Bar manifest has no TargetDeviceFamily")
    else:
        min_version = dependency.attrib.get("MinVersion", "")
        # The widget itself can run on older Windows builds; voice support is
        # gated independently. Keep this check informational but ensure the
        # process-loopback minimum is explicit in source and release docs.
        if not min_version:
            errors.append("Game Bar TargetDeviceFamily is missing MinVersion")

    if MIN_PROCESS_LOOPBACK_BUILD < 20348:
        errors.append("process-loopback minimum Windows build regressed below 20348")

    return errors


def main() -> int:
    errors = validate_release_metadata()
    if errors:
        for error in errors:
            print(f"[release validation] ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"[release validation] OK: Sage {SAGE_VERSION} / Game Bar {SAGE_VERSION_QUAD}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
