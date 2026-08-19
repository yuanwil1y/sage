"""Small full-screen PySide6 ROI selector for the chat OCR region."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from config.roi import RoiConfig


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
        # Qt keeps the virtual desktop origin in native desktop coordinates on
        # Windows but reports each screen's size in device-independent pixels.
        # Scale only the local selection; scaling the global origin breaks
        # monitors placed left/above the primary display.
        handle = self.windowHandle()
        scale = float(
            handle.devicePixelRatio()
            if handle is not None
            else self._screen.devicePixelRatio()
        )
        left = geometry.left() + round(self._selection.left() * scale)
        top = geometry.top() + round(self._selection.top() * scale)
        right = geometry.left() + round((self._selection.right() + 1) * scale)
        bottom = geometry.top() + round((self._selection.bottom() + 1) * scale)
        screens = QtGui.QGuiApplication.screens()
        output_idx = screens.index(self._screen) if self._screen in screens else 0
        primary = QtGui.QGuiApplication.primaryScreen()
        serial = ""
        try:
            serial = self._screen.serialNumber() or ""
        except Exception:
            pass
        return RoiConfig(
            output_idx,
            left,
            top,
            right,
            bottom,
            screen_name=self._screen.name() or None,
            screen_serial=serial or None,
            screen_geometry=(
                geometry.left(),
                geometry.top(),
                geometry.width(),
                geometry.height(),
            ),
            device_pixel_ratio=scale,
            screen_primary=self._screen == primary,
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
            if self.selected_roi() is not None:
                self.accept()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
