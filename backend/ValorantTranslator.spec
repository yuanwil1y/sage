# -*- mode: python ; coding: utf-8 -*-
"""Canonical PyInstaller target for the complete Sage application."""

from spec_common import executable_icon, spec_options

options = spec_options()
a = Analysis(["main.py"], **options)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ValorantTranslator',
    icon=executable_icon(),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # The installed desktop app is GUI-first.  Runtime logs are routed to the
    # in-app Debug Log tab; --headless remains available for development.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ValorantTranslator',
)
