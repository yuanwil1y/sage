from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamebar_manager import CertificateStatus, WidgetStatus
from ui import control_window


def _process_events_until(app, predicate, timeout: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return predicate()


def test_certificate_remove_keeps_gui_event_loop_responsive(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    payload = tmp_path / "SageWidget.msix"
    certificate = tmp_path / "SageWidget.cer"
    trusted_certificate = CertificateStatus(
        available=True,
        trusted=True,
        path=certificate,
        thumbprint="A" * 40,
        detail="证书已导入到本地计算机并受 Windows 信任",
    )
    removed_certificate = CertificateStatus(
        available=True,
        trusted=False,
        path=certificate,
        thumbprint="A" * 40,
        detail="证书尚未导入到本地计算机",
    )
    installed_status = WidgetStatus(
        installed=True,
        payload=payload,
        detail="小组件已安装",
        certificate=trusted_certificate,
    )
    removed_status = WidgetStatus(
        installed=False,
        payload=payload,
        detail="请先导入小组件证书，再初始化小组件",
        certificate=removed_certificate,
    )
    state = {"removed": False}
    started = threading.Event()
    release = threading.Event()
    dialogs = []
    uninstall_calls = []

    def get_status():
        return removed_status if state["removed"] else installed_status

    def blocking_remove():
        started.set()
        if not release.wait(timeout=2.0):
            raise TimeoutError("test did not release certificate removal")
        state["removed"] = True
        return removed_certificate

    monkeypatch.setattr(control_window, "get_widget_status", get_status)
    monkeypatch.setattr(control_window, "remove_widget_certificate", blocking_remove)
    monkeypatch.setattr(
        control_window,
        "uninstall_widget",
        lambda: uninstall_calls.append(True),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )

    window = control_window.ControlWindow()
    try:
        before = time.perf_counter()
        window._on_remove_widget_certificate()
        call_duration = time.perf_counter() - before

        assert call_duration < 0.1
        assert window._certificate_remove_task is not None
        assert window.widget_cert_remove_btn.text() == "正在删除…"
        assert not window.widget_operation_progress.isHidden()
        assert "正在删除" in window.widget_operation_hint.text()
        assert started.wait(timeout=1.0)
        assert uninstall_calls == [True]

        ticks = []
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        timer.start()
        assert _process_events_until(app, lambda: len(ticks) >= 3, timeout=0.5)
        timer.stop()
        assert window._certificate_remove_task is not None

        release.set()
        assert _process_events_until(
            app,
            lambda: window._certificate_remove_task is None,
        )
        assert window.widget_cert_remove_btn.text() == "删除证书"
        assert not window.widget_cert_remove_btn.isEnabled()
        assert window.widget_operation_progress.isHidden()
        assert dialogs
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_widget_install_keeps_gui_event_loop_responsive(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    payload = tmp_path / "SageWidget.msix"
    certificate = CertificateStatus(
        available=True,
        trusted=True,
        path=tmp_path / "SageWidget.cer",
        thumbprint="A" * 40,
        detail="证书已导入到本地计算机并受 Windows 信任",
    )
    state = {"installed": False}
    started = threading.Event()
    release = threading.Event()
    dialogs = []

    def get_status():
        return WidgetStatus(
            installed=state["installed"],
            payload=payload,
            detail="小组件已安装" if state["installed"] else "证书已准备好，小组件尚未安装",
            certificate=certificate,
        )

    def blocking_install():
        started.set()
        if not release.wait(timeout=2.0):
            raise TimeoutError("test did not release widget installation")
        state["installed"] = True
        return get_status()

    monkeypatch.setattr(control_window, "get_widget_status", get_status)
    monkeypatch.setattr(control_window, "install_widget", blocking_install)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )

    window = control_window.ControlWindow()
    try:
        before = time.perf_counter()
        window._on_initialize_widget()
        call_duration = time.perf_counter() - before

        assert call_duration < 0.1
        assert window._widget_package_task is not None
        assert window.widget_init_btn.text() == "正在修复…"
        assert not window.widget_operation_progress.isHidden()
        assert "正在修复" in window.widget_operation_hint.text()
        assert started.wait(timeout=1.0)

        ticks = []
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        timer.start()
        assert _process_events_until(app, lambda: len(ticks) >= 3, timeout=0.5)
        timer.stop()

        release.set()
        assert _process_events_until(app, lambda: window._widget_package_task is None)
        assert window.widget_init_btn.text() == "修复小组件"
        assert window.widget_uninstall_btn.isEnabled()
        assert window.widget_operation_progress.isHidden()
        assert dialogs
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_widget_uninstall_keeps_gui_event_loop_responsive(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    payload = tmp_path / "SageWidget.msix"
    certificate = CertificateStatus(
        available=True,
        trusted=True,
        path=tmp_path / "SageWidget.cer",
        thumbprint="A" * 40,
        detail="证书已导入到本地计算机并受 Windows 信任",
    )
    state = {"installed": True}
    started = threading.Event()
    release = threading.Event()
    dialogs = []

    def get_status():
        return WidgetStatus(
            installed=state["installed"],
            payload=payload,
            detail="小组件已安装" if state["installed"] else "证书已准备好，小组件尚未安装",
            certificate=certificate,
        )

    def blocking_uninstall():
        started.set()
        if not release.wait(timeout=2.0):
            raise TimeoutError("test did not release widget uninstall")
        state["installed"] = False
        return get_status()

    monkeypatch.setattr(control_window, "get_widget_status", get_status)
    monkeypatch.setattr(control_window, "uninstall_widget", blocking_uninstall)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "information",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: dialogs.append((args, kwargs)),
    )

    window = control_window.ControlWindow()
    try:
        before = time.perf_counter()
        window._on_uninstall_widget()
        call_duration = time.perf_counter() - before

        assert call_duration < 0.1
        assert window._widget_package_task is not None
        assert window.widget_uninstall_btn.text() == "正在卸载…"
        assert not window.widget_operation_progress.isHidden()
        assert "正在卸载" in window.widget_operation_hint.text()
        assert started.wait(timeout=1.0)

        ticks = []
        timer = QtCore.QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
        timer.start()
        assert _process_events_until(app, lambda: len(ticks) >= 3, timeout=0.5)
        timer.stop()

        release.set()
        assert _process_events_until(app, lambda: window._widget_package_task is None)
        assert window.widget_uninstall_btn.text() == "卸载小组件"
        assert window.widget_init_btn.isEnabled()
        assert window.widget_operation_progress.isHidden()
        assert dialogs
    finally:
        release.set()
        window.close()
        app.processEvents()
