"""模型中心：显式下载、校验和清理用户模型。

OCR 模型随程序内置；Hy-MT2 与 faster-whisper 由用户在这里明确下载。
运行流水线不会因为缺少模型而偷偷访问网络。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from model_store import (
    DownloadCancelled,
    ModelSpec,
    delete_model,
    download_model,
    import_model,
    model_location,
    model_size,
    model_specs_for_mode,
    model_status,
    verify_model,
)

log = logging.getLogger("ui.models")


@dataclass
class ModelRow:
    spec: ModelSpec
    status: str
    installed_size: int


STATUS_TEXT = {
    "embedded": "内置",
    "installed": "已安装",
    "missing": "未安装",
    "incomplete": "下载未完成",
    "invalid": "校验失败",
}


def _format_size(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    return f"{size / 1024**2:.0f} MB"


class ModelStore:
    """聚合模型描述和本地状态。"""

    def __init__(self, mode: str = "full") -> None:
        self._specs = model_specs_for_mode(mode)

    def refresh(self) -> list[ModelRow]:
        return [
            ModelRow(
                spec=spec,
                status=model_status(spec),
                installed_size=model_size(spec),
            )
            for spec in self._specs
        ]


class ModelManagerDialog(QtWidgets.QDialog):
    PROGRESS_CHANGED = QtCore.Signal(str, int, int)
    ACTION_FINISHED = QtCore.Signal(bool, str)

    def __init__(self, parent=None, mode: str = "full") -> None:
        super().__init__(parent)
        self._mode = mode
        self.setWindowTitle("模型中心")
        self.resize(820, 440)
        self._store = ModelStore(mode)
        self._rows: list[ModelRow] = []
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._build_ui()
        self.PROGRESS_CHANGED.connect(self._on_progress)
        self.ACTION_FINISHED.connect(self._on_action_finished)
        self.refresh()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            "当前版本所需的 OCR 模型已随程序内置；Hy-MT2 和语音识别模型可在此下载，"
            "也可导入另一台机器准备好的本地模型目录。运行时只使用本地文件。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["模型", "功能", "大小（已装/总计）", "状态", "位置"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_action_state)
        layout.addWidget(self.table)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_lbl = QtWidgets.QLabel("")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        btns = QtWidgets.QHBoxLayout()
        self.download_btn = QtWidgets.QPushButton("下载选中")
        self.import_btn = QtWidgets.QPushButton("导入本地")
        self.verify_btn = QtWidgets.QPushButton("校验选中")
        self.delete_btn = QtWidgets.QPushButton("删除选中")
        self.cancel_btn = QtWidgets.QPushButton("取消")
        self.refresh_btn = QtWidgets.QPushButton("刷新")
        self.close_btn = QtWidgets.QPushButton("关闭")
        for button in (self.download_btn, self.import_btn, self.verify_btn, self.delete_btn, self.cancel_btn):
            btns.addWidget(button)
        btns.addStretch()
        btns.addWidget(self.refresh_btn)
        btns.addWidget(self.close_btn)
        layout.addLayout(btns)

        self.download_btn.clicked.connect(self._on_download)
        self.import_btn.clicked.connect(self._on_import)
        self.verify_btn.clicked.connect(self._on_verify)
        self.delete_btn.clicked.connect(self._on_delete)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.refresh_btn.clicked.connect(self.refresh)
        self.close_btn.clicked.connect(self.close)
        self._set_busy(False)

    def refresh(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._rows = self._store.refresh()
        selected_key = self._selected_key()
        self.table.setRowCount(len(self._rows))
        for row, item in enumerate(self._rows):
            spec = item.spec
            values = (
                spec.name,
                spec.kind,
                f"{_format_size(item.installed_size)} / {_format_size(spec.expected_size)}",
                STATUS_TEXT.get(item.status, item.status),
                model_location(spec),
            )
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if column == 4:
                    cell.setToolTip(value)
                if column in (0, 1):
                    cell.setToolTip(spec.description)
                self.table.setItem(row, column, cell)
        self.table.resizeRowsToContents()
        if selected_key:
            for row, item in enumerate(self._rows):
                if item.spec.key == selected_key:
                    self.table.selectRow(row)
                    break
        if self.table.currentRow() < 0 and self._rows:
            self.table.selectRow(0)
        self._update_action_state()

    def _selected_key(self) -> str | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row].spec.key
        return None

    def _selected_spec(self) -> ModelSpec | None:
        key = self._selected_key()
        if key is None:
            return None
        return next((item.spec for item in self._rows if item.spec.key == key), None)

    def _update_action_state(self) -> None:
        spec = self._selected_spec()
        busy = self._worker is not None and self._worker.is_alive()
        available = spec is not None and not spec.embedded
        self.download_btn.setEnabled(bool(available and not busy))
        self.import_btn.setEnabled(bool(available and not busy))
        self.verify_btn.setEnabled(bool(available and not busy))
        self.delete_btn.setEnabled(bool(available and not busy))
        self.cancel_btn.setEnabled(bool(busy))

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.table.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        self._update_action_state()

    def _start_worker(
        self,
        action: str,
        spec: ModelSpec,
        source_dir: Path | None = None,
    ) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._cancel_event.clear()
        self._set_busy(True)
        self.progress.setValue(0)
        self.status_lbl.setText(f"正在{action}：{spec.name}")

        def run() -> None:
            try:
                if action == "下载":
                    download_model(
                        spec.key,
                        progress=lambda label, done, total: self.PROGRESS_CHANGED.emit(
                            label, done, total
                        ),
                        cancel_event=self._cancel_event,
                    )
                    message = f"{spec.name} 下载完成"
                elif action == "导入":
                    if source_dir is None:
                        raise ValueError("未选择模型目录")
                    import_model(
                        spec.key,
                        source_dir,
                        progress=lambda label, done, total: self.PROGRESS_CHANGED.emit(
                            label, done, total
                        ),
                    )
                    message = f"{spec.name} 导入完成"
                elif action == "校验":
                    message = f"{spec.name} 校验通过" if verify_model(spec.key) else f"{spec.name} 校验失败"
                else:
                    delete_model(spec.key)
                    message = f"{spec.name} 已删除"
                self.ACTION_FINISHED.emit(True, message)
            except DownloadCancelled:
                self.ACTION_FINISHED.emit(False, "已取消，已保留可续传文件")
            except Exception as exc:
                log.exception("模型操作失败: %s", spec.key)
                self.ACTION_FINISHED.emit(False, f"{spec.name} 操作失败：{exc}")

        self._worker = threading.Thread(target=run, name=f"Model-{action}", daemon=True)
        self._worker.start()

    def _on_download(self) -> None:
        spec = self._selected_spec()
        if spec is not None:
            self._start_worker("下载", spec)

    def _on_verify(self) -> None:
        spec = self._selected_spec()
        if spec is not None:
            self._start_worker("校验", spec)

    def _on_import(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        source = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择本地模型目录",
            str(Path.home()),
        )
        if source:
            self._start_worker("导入", spec, Path(source))

    def _on_delete(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "删除模型",
            f"确定删除 {spec.name}？下次使用前需要重新下载。",
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._start_worker("删除", spec)

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        self.status_lbl.setText("正在取消当前操作……")

    @QtCore.Slot(str, int, int)
    def _on_progress(self, label: str, done: int, total: int) -> None:
        if total <= 0:
            return
        self.progress.setValue(max(0, min(100, int(done * 100 / total))))
        self.status_lbl.setText(f"正在下载 {label}：{_format_size(done)} / {_format_size(total)}")

    @QtCore.Slot(bool, str)
    def _on_action_finished(self, success: bool, message: str) -> None:
        self._worker = None
        self._set_busy(False)
        self.status_lbl.setText(message)
        self.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self._worker is not None and self._worker.is_alive():
            self._cancel_event.set()
            event.ignore()
            self.status_lbl.setText("请等待当前模型操作取消完成")
            return
        super().closeEvent(event)
