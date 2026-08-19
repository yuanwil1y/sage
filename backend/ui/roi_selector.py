"""Small full-screen PySide6 ROI selector for the chat OCR region."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from config.roi import RoiConfig
from screen.display_mapping import (
    output_local_region,
    parse_output_info,
    physical_size,
    resolve_output,
)


class RegionSelectorDialog(QtWidgets.QDialog):
    """Let the user drag a rectangle on one monitor and return a DXcam ROI."""

    MIN_SIZE = 10

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        screen: QtGui.QScreen | None = None,
    ) -> None:
        super().__init__(parent)
        self._screen = screen or QtGui.QGuiApplication.primaryScreen()
        if self._screen is None:
            raise RuntimeError("没有可用的显示器")
        self._start: QtCore.QPoint | None = None
        self._selection = QtCore.QRect()

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setGeometry(self._screen.geometry())

    def selected_roi(self) -> RoiConfig | None:
        if self._selection.width() < self.MIN_SIZE or self._selection.height() < self.MIN_SIZE:
            return None

        geometry = self._screen.geometry()
        handle = self.windowHandle()
        scale = float(
            handle.devicePixelRatio()
            if handle is not None
            else self._screen.devicePixelRatio()
        )

        # DXcam's region is relative to one output, so never add the virtual
        # desktop origin here. Only scale the screen-local logical rectangle.
        local_region = output_local_region(
            (
                self._selection.left(),
                self._selection.top(),
                self._selection.right() + 1,
                self._selection.bottom() + 1,
            ),
            scale,
        )

        screens = QtGui.QGuiApplication.screens()
        qt_index = screens.index(self._screen) if self._screen in screens else 0
        primary = QtGui.QGuiApplication.primaryScreen()
        is_primary = self._screen == primary
        device_idx = 0
        output_idx = qt_index

        # Resolve the Qt screen to an actual DXcam device/output while the user
        # is selecting it. The Qt index remains only a hint; physical size and
        # primary status validate/remap across all adapters. Ambiguous mappings
        # are rejected instead of silently capturing another monitor.
        try:
            import dxcam

            outputs = parse_output_info(dxcam.output_info())
            if outputs:
                resolved = resolve_output(
                    outputs,
                    saved_device_idx=0,
                    saved_output_idx=qt_index,
                    expected_size=physical_size(
                        (
                            geometry.left(),
                            geometry.top(),
                            geometry.width(),
                            geometry.height(),
                        ),
                        scale,
                    ),
                    primary=is_primary,
                )
                device_idx = resolved.device_idx
                output_idx = resolved.output_idx
        except ImportError as exc:
            raise RuntimeError("DXcam 未安装，无法确认聊天区域所在显示器") from exc
        except Exception as exc:
            raise RuntimeError(f"无法确认聊天区域所在显示器：{exc}") from exc

        serial = ""
        try:
            serial = self._screen.serialNumber() or ""
        except Exception:
            pass
        return RoiConfig(
            output_idx,
            *local_region,
            device_idx=device_idx,
            coordinate_space="output",
            screen_name=self._screen.name() or None,
            screen_serial=serial or None,
            screen_geometry=(
                geometry.left(),
                geometry.top(),
                geometry.width(),
                geometry.height(),
            ),
            device_pixel_ratio=scale,
            screen_primary=is_primary,
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: ARG002
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 90))

        painter.setPen(QtGui.QPen(QtGui.QColor("white"), 2))
        painter.drawText(
            24,
            42,
            "拖动选择 VALORANT 左下角聊天区域；Esc 取消",
        )
        if not self._selection.isNull():
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(self._selection, QtCore.Qt.GlobalColor.transparent)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.fillRect(self._selection, QtGui.QColor(40, 180, 120, 70))
            painter.setPen(QtGui.QPen(QtGui.QColor(80, 255, 170), 2))
            painter.drawRect(self._selection)
        painter.end()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._selection = QtCore.QRect(self._start, self._start)
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._start is not None:
            self._selection = QtCore.QRect(self._start, event.position().toPoint()).normalized()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start is not None:
            self._selection = QtCore.QRect(self._start, event.position().toPoint()).normalized()
            self._start = None
            self.update()
            try:
                roi = self.selected_roi()
            except RuntimeError as exc:
                QtWidgets.QMessageBox.warning(self, "无法选择显示器", str(exc))
                return
            if roi is not None:
                self.accept()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
