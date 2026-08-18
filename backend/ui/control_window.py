"""面向普通用户的 PySide6 控制界面。

界面分成四个标签页：实时状态、语音聊天、文字聊天和调试日志。后端仍
使用英文 section key 传递状态，显示层在这里统一翻译成用户能看懂的中文。
"""

from __future__ import annotations

import logging
import time

from PySide6 import QtCore, QtGui, QtWidgets

from config.preferences import AppPreferences, save_preferences
from config.roi import RoiConfig, save_roi_config
from gamebar_manager import (
    get_widget_status,
    install_widget,
    remove_widget_certificate,
    uninstall_widget,
)
from paths import resources_dir
from ui.log_bridge import GuiLogBridge

log = logging.getLogger("ui")


STATUS_TITLES = {
    "Game": "游戏状态",
    "Game Bar": "游戏栏连接",
    "Voice": "语音聊天",
    "Chat": "文字聊天",
    "Local Translation": "本地翻译",
}


class _TaskSignals(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(object)


class _BackgroundTask(QtCore.QRunnable):
    """Run one blocking Windows operation without freezing the Qt event loop."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self.signals = _TaskSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self._callback()
        except Exception as exc:
            self.signals.failed.emit(exc)
        else:
            self.signals.succeeded.emit(result)


class ControlWindow(QtWidgets.QMainWindow):
    """主控制窗口。"""

    STATUS_CHANGED = QtCore.Signal(str, str)

    def __init__(
        self,
        orchestrator=None,
        pipe=None,
        roi_config: RoiConfig | None = None,
        preferences: AppPreferences | None = None,
        log_bridge: GuiLogBridge | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("ControlWindow")
        self._orchestrator = orchestrator
        self._pipe = pipe
        self._roi_config = roi_config
        self._preferences = preferences or AppPreferences.defaults()
        self._log_bridge = log_bridge
        self._certificate_remove_task: _BackgroundTask | None = None
        self._widget_package_task: _BackgroundTask | None = None
        self._last_widget_status = None
        self._widget_operation_started_at: float | None = None
        self._widget_operation_text = ""
        self.status_labels: dict[str, QtWidgets.QLabel] = {}

        icon_path = resources_dir() / "Sage.ico"
        if icon_path.is_file():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.setWindowTitle("Sage · 完整功能版")
        self.setMinimumSize(900, 620)
        self.resize(1040, 720)
        self._apply_theme()
        self._build_ui()
        self._widget_operation_timer = QtCore.QTimer(self)
        self._widget_operation_timer.setInterval(500)
        self._widget_operation_timer.timeout.connect(
            self._update_widget_operation_indicator
        )
        self.STATUS_CHANGED.connect(self._update_status)
        self._connect_log_bridge()
        self._refresh_model_summary()

        self._pipe_status_timer = QtCore.QTimer(self)
        self._pipe_status_timer.timeout.connect(self._refresh_pipe_status)
        self._pipe_status_timer.start(500)
        self._refresh_pipe_status()
        self._refresh_widget_status()

        if self._roi_config is not None:
            self.set_status("Chat", "OCR: ROI configured")

    # ---- 界面 ----

    def _apply_theme(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        app_font = QtGui.QFont("Microsoft YaHei UI", 10)
        app_font.setStyleHint(QtGui.QFont.StyleHint.SansSerif)
        app.setFont(app_font)
        palette = app.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#0d1117"))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#e6edf3"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#111822"))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#151e2b"))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#1a2432"))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#f8fafc"))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#e6edf3"))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#1b2533"))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#e6edf3"))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#5967f2"))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.ColorRole.Link, QtGui.QColor("#8ea2ff"))
        app.setPalette(palette)
        app.setStyleSheet(
            """
            QMainWindow, QDialog { background: #0d1117; color: #e6edf3; }
            QMainWindow#ControlWindow { background: #0d1117; }
            QWidget#RootSurface { background: #0d1117; color: #e6edf3; }
            QWidget#PageSurface { background: #111822; color: #e6edf3; }
            QLabel { background: transparent; }
            QTabWidget::pane { border: 1px solid #253247; border-radius: 12px; background: #111822; top: -1px; }
            QTabBar { qproperty-drawBase: 0; }
            QTabBar::tab { min-width: 112px; padding: 11px 18px; margin-right: 4px; color: #8593a8; background: #111822; border: 1px solid transparent; border-radius: 9px 9px 0 0; }
            QTabBar::tab:hover { color: #d9e2ef; background: #172131; }
            QTabBar::tab:selected { color: #ffffff; background: #1b2638; border: 1px solid #2d3c55; border-bottom: 2px solid #7684ff; }
            QGroupBox { border: 1px solid #263448; border-radius: 12px; margin-top: 14px; padding: 20px 16px 14px; background: #151d29; }
            QGroupBox:hover { border-color: #344865; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 8px; color: #cbd5e1; background: #151d29; font-weight: 600; }
            QFrame#HeroCard { background: #151d29; border: 1px solid #2a3a52; border-radius: 16px; }
            QFrame#StatusCard { background: #151d29; border: 1px solid #263448; border-radius: 12px; }
            QFrame#StatusCard:hover { border-color: #344865; }
            QFrame#ActionBar { background: #121a26; border: 1px solid #253247; border-radius: 12px; }
            QLabel#AppMark { color: #ffffff; background: #5967f2; border-radius: 14px; font-size: 22px; font-weight: 800; padding: 0; }
            QLabel#PageTitle { font-size: 21px; font-weight: 700; color: #f8fafc; }
            QLabel#PageHint { color: #8d9bb0; font-size: 13px; }
            QLabel#ModeBadge { color: #dbe4ff; background: #252e63; border: 1px solid #4b59c8; border-radius: 12px; padding: 6px 12px; font-weight: 600; }
            QLabel#OfflineBadge { color: #a7f3d0; background: #12362d; border: 1px solid #1f7357; border-radius: 12px; padding: 6px 12px; font-weight: 600; }
            QLabel#HeroTitle { color: #f8fafc; font-size: 17px; font-weight: 700; }
            QLabel#HeroSubtitle { color: #8d9bb0; font-size: 13px; }
            QLabel#HeroState { color: #fde68a; background: #3b2f14; border: 1px solid #7a5b1f; border-radius: 10px; padding: 7px 12px; font-weight: 600; }
            QLabel#HeroState[state="ok"] { color: #a7f3d0; background: #12362d; border-color: #1f7357; }
            QLabel#HeroState[state="bad"] { color: #ffb8bf; background: #3b1d28; border-color: #813c4b; }
            QLabel#InfoStrip { color: #a9b7ca; background: #111a27; border: 1px solid #25364e; border-radius: 9px; padding: 10px 12px; }
            QLabel#StatusValue { color: #dbe7f5; font-size: 14px; font-weight: 600; padding: 5px 0; }
            QLabel#StatusValue[state="ok"] { color: #63e6a8; }
            QLabel#StatusValue[state="warn"] { color: #f6c969; }
            QLabel#StatusValue[state="bad"] { color: #ff8f9a; }
            QLabel#SectionLabel { color: #9aa8bb; font-size: 12px; font-weight: 600; }
            QPushButton { min-height: 34px; padding: 0 16px; border: 1px solid #33445b; border-radius: 8px; background: #1b2635; color: #e6edf3; }
            QPushButton:hover { background: #25364b; border-color: #657da3; }
            QPushButton:pressed { background: #162333; }
            QPushButton:disabled { color: #627086; background: #151c27; border-color: #222c3b; }
            QPushButton#PrimaryButton { color: #ffffff; background: #5967f2; border-color: #5967f2; font-weight: 700; }
            QPushButton#PrimaryButton:hover { background: #6c78ff; border-color: #6c78ff; }
            QPushButton#AccentButton { color: #071a13; background: #55d99b; border-color: #55d99b; font-weight: 700; }
            QPushButton#AccentButton:hover { background: #72e7b1; border-color: #72e7b1; }
            QPushButton#DangerButton { color: #ffb8bf; border-color: #713743; background: #2a1b24; }
            QPushButton#DangerButton:hover { color: #ffffff; background: #753543; border-color: #a34c5e; }
            QPlainTextEdit { background: #0b1119; color: #b6f7d8; border: 1px solid #29394e; border-radius: 10px; padding: 10px; selection-background-color: #354777; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 30px; padding: 0 8px; border: 1px solid #33445b; border-radius: 7px; background: #101824; color: #e6edf3; selection-background-color: #5967f2; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #7180ff; }
            QComboBox::drop-down { width: 24px; border: 0; }
            QProgressBar { min-height: 18px; border: 1px solid #33445b; border-radius: 6px; background: #101824; color: #e6edf3; text-align: center; }
            QProgressBar::chunk { background: #5967f2; border-radius: 5px; }
            QCheckBox { color: #a9b7ca; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4a5d79; border-radius: 4px; background: #111a27; }
            QCheckBox::indicator:checked { background: #5967f2; border-color: #7180ff; }
            QHeaderView::section { background: #1b2635; color: #b9c7d8; border: 0; border-bottom: 1px solid #33445b; padding: 8px; }
            QTableWidget, QListWidget { background: #111822; alternate-background-color: #151f2d; color: #e6edf3; border: 1px solid #29394e; border-radius: 8px; gridline-color: #253247; }
            QTableWidget::item:selected, QListWidget::item:selected { background: #354777; color: #ffffff; }
            QScrollBar:vertical { width: 10px; background: #0d1117; margin: 2px; }
            QScrollBar::handle:vertical { min-height: 28px; background: #33445b; border-radius: 5px; }
            QScrollBar::handle:vertical:hover { background: #596b8a; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QToolTip { color: #f8fafc; background: #1b2635; border: 1px solid #4b5d78; padding: 6px; }
            """
        )

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("RootSurface")
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(16)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        app_mark = QtWidgets.QLabel()
        app_mark.setObjectName("AppMark")
        app_mark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        app_mark.setFixedSize(56, 56)
        icon_path = resources_dir() / "Sage.ico"
        icon_pixmap = QtGui.QPixmap(str(icon_path)) if icon_path.is_file() else QtGui.QPixmap()
        if icon_pixmap.isNull():
            app_mark.setText("S")
        else:
            app_mark.setPixmap(
                icon_pixmap.scaled(
                    56,
                    56,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            )
        header.addWidget(app_mark, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        title = QtWidgets.QLabel("Sage")
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel("游戏内日语语音和聊天翻译  ·  本地运行，不上传内容")
        subtitle.setObjectName("PageHint")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        offline_badge = QtWidgets.QLabel("● 本地运行")
        offline_badge.setObjectName("OfflineBadge")
        header.addWidget(offline_badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        badge = QtWidgets.QLabel("完整功能版")
        badge.setObjectName("ModeBadge")
        header.addWidget(badge, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("MainTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.addTab(self._build_status_page(), "实时状态")
        self.tabs.addTab(self._build_voice_page(), "语音聊天")
        self.tabs.addTab(self._build_text_page(), "文字聊天")
        self.tabs.addTab(self._build_log_page(), "调试日志")
        root.addWidget(self.tabs, 1)

    def _build_status_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("PageSurface")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)

        intro = QtWidgets.QLabel(
            "这里可以查看程序是否准备好。启动后程序会自动等待 VALORANT，"
            "不需要手动打开命令行。字幕会显示在 Game Bar 小组件中；"
            "第一次使用请先安装小组件，然后按 Win+G 打开。"
        )
        intro.setObjectName("PageHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        hero = QtWidgets.QFrame()
        hero.setObjectName("HeroCard")
        hero_layout = QtWidgets.QHBoxLayout(hero)
        hero_layout.setContentsMargins(18, 16, 18, 16)
        hero_layout.setSpacing(14)
        hero_text = QtWidgets.QVBoxLayout()
        hero_text.setSpacing(3)
        hero_title = QtWidgets.QLabel("运行概览")
        hero_title.setObjectName("HeroTitle")
        hero_subtitle = QtWidgets.QLabel("后台服务会自动等待游戏；准备好后，字幕会出现在 Game Bar 小组件中。")
        hero_subtitle.setObjectName("HeroSubtitle")
        hero_subtitle.setWordWrap(True)
        hero_text.addWidget(hero_title)
        hero_text.addWidget(hero_subtitle)
        hero_layout.addLayout(hero_text, 1)
        self.hero_state = QtWidgets.QLabel("等待游戏启动")
        self.hero_state.setObjectName("HeroState")
        self.hero_state.setProperty("state", "warn")
        self.hero_state.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.hero_state, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(hero)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        initial = {
            "Game": "尚未检测到 VALORANT",
            "Game Bar": "等待 Game Bar 小组件",
            "Voice": "尚未启动",
            "Chat": "尚未启动",
            "Local Translation": "翻译模型未就绪",
        }
        for index, key in enumerate(STATUS_TITLES):
            card = QtWidgets.QFrame()
            card.setObjectName("StatusCard")
            box = QtWidgets.QVBoxLayout(card)
            box.setContentsMargins(14, 10, 14, 10)
            box.setSpacing(3)
            title = QtWidgets.QLabel(STATUS_TITLES[key])
            title.setObjectName("SectionLabel")
            box.addWidget(title)
            label = QtWidgets.QLabel(initial[key])
            label.setObjectName("StatusValue")
            label.setWordWrap(True)
            label.setMinimumHeight(24)
            box.addWidget(label)
            self.status_labels[key] = label
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)

        self.model_summary = QtWidgets.QLabel("")
        self.model_summary.setWordWrap(True)
        self.model_summary.setObjectName("InfoStrip")
        layout.addWidget(self.model_summary)

        widget_box = QtWidgets.QGroupBox("Game Bar 小组件")
        widget_box.setObjectName("WidgetCard")
        widget_layout = QtWidgets.QVBoxLayout(widget_box)
        self.widget_status_label = QtWidgets.QLabel("检查中……")
        self.widget_status_label.setWordWrap(True)
        widget_layout.addWidget(self.widget_status_label)

        cert_hint = QtWidgets.QLabel(
            "安装程序会自动准备证书和小组件。若小组件损坏，点击“修复小组件”"
            "即可重新完成证书、依赖、安装和连接设置。"
        )
        cert_hint.setObjectName("PageHint")
        cert_hint.setWordWrap(True)
        widget_layout.addWidget(cert_hint)

        cert_row = QtWidgets.QHBoxLayout()
        self.widget_cert_status_label = QtWidgets.QLabel("证书：检查中……")
        self.widget_cert_status_label.setWordWrap(True)
        cert_row.addWidget(self.widget_cert_status_label, 1)
        self.widget_cert_remove_btn = QtWidgets.QPushButton("删除证书")
        self.widget_cert_remove_btn.setObjectName("DangerButton")
        cert_row.addWidget(self.widget_cert_remove_btn)
        widget_layout.addLayout(cert_row)

        widget_row = QtWidgets.QHBoxLayout()
        self.widget_init_btn = QtWidgets.QPushButton("修复小组件")
        self.widget_init_btn.setObjectName("PrimaryButton")
        self.widget_open_btn = QtWidgets.QPushButton("打开游戏栏")
        self.widget_uninstall_btn = QtWidgets.QPushButton("卸载小组件")
        self.widget_uninstall_btn.setObjectName("DangerButton")
        widget_row.addWidget(self.widget_init_btn)
        widget_row.addWidget(self.widget_open_btn)
        widget_row.addWidget(self.widget_uninstall_btn)
        widget_row.addStretch()
        widget_layout.addLayout(widget_row)

        self.widget_operation_hint = QtWidgets.QLabel("")
        self.widget_operation_hint.setObjectName("PageHint")
        self.widget_operation_hint.setWordWrap(True)
        self.widget_operation_hint.hide()
        widget_layout.addWidget(self.widget_operation_hint)
        self.widget_operation_progress = QtWidgets.QProgressBar()
        self.widget_operation_progress.setRange(0, 0)
        self.widget_operation_progress.setTextVisible(False)
        self.widget_operation_progress.setAccessibleName("Game Bar 操作进行中")
        self.widget_operation_progress.hide()
        widget_layout.addWidget(self.widget_operation_progress)
        layout.addWidget(widget_box)

        self.widget_cert_remove_btn.clicked.connect(self._on_remove_widget_certificate)
        self.widget_init_btn.clicked.connect(self._on_initialize_widget)
        self.widget_open_btn.clicked.connect(self._on_open_gamebar)
        self.widget_uninstall_btn.clicked.connect(self._on_uninstall_widget)

        action_bar = QtWidgets.QFrame()
        action_bar.setObjectName("ActionBar")
        actions = QtWidgets.QHBoxLayout(action_bar)
        actions.setContentsMargins(10, 10, 10, 10)
        actions.setSpacing(8)
        self.start_btn = QtWidgets.QPushButton("启动全部功能")
        self.start_btn.setObjectName("PrimaryButton")
        self.stop_btn = QtWidgets.QPushButton("停止全部功能")
        self.model_btn = QtWidgets.QPushButton("打开模型中心")
        self.guide_btn = QtWidgets.QPushButton("使用说明")
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addStretch()
        actions.addWidget(self.model_btn)
        actions.addWidget(self.guide_btn)
        layout.addWidget(action_bar)

        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)
        self.model_btn.clicked.connect(self._on_model_manager)
        self.guide_btn.clicked.connect(self._on_open_guide)
        return page

    def _build_voice_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("PageSurface")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)

        intro = QtWidgets.QLabel(
            "语音聊天会自动捕获 VALORANT 的游戏声音，识别日语后翻译成中文。"
            "程序不会录制或上传音频。"
        )
        intro.setObjectName("PageHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        box = QtWidgets.QGroupBox("语音聊天设置")
        form = QtWidgets.QFormLayout(box)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.voice_source_label = QtWidgets.QLabel("VALORANT 游戏声音（自动）")
        form.addRow("声音来源：", self.voice_source_label)
        self.voice_language_label = QtWidgets.QLabel("日语 → 简体中文")
        form.addRow("翻译方向：", self.voice_language_label)

        self.voice_model_label = QtWidgets.QLabel("检查中……")
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(self.voice_model_label, 1)
        voice_model_btn = QtWidgets.QPushButton("管理语音模型")
        voice_model_btn.clicked.connect(self._on_model_manager)
        model_row.addWidget(voice_model_btn)
        form.addRow("语音模型：", model_row)

        self.voice_threshold = QtWidgets.QDoubleSpinBox()
        self.voice_threshold.setRange(0.10, 0.90)
        self.voice_threshold.setSingleStep(0.05)
        self.voice_threshold.setDecimals(2)
        self.voice_threshold.setValue(self._preferences.voice.vad_threshold)
        self.voice_threshold.setToolTip("数值越低越容易听到小声说话，但也可能更容易误触发")
        form.addRow("说话敏感度：", self.voice_threshold)

        self.voice_silence = QtWidgets.QSpinBox()
        self.voice_silence.setRange(300, 2000)
        self.voice_silence.setSingleStep(100)
        self.voice_silence.setSuffix(" 毫秒")
        self.voice_silence.setValue(self._preferences.voice.min_silence_ms)
        self.voice_silence.setToolTip("停顿多久后把这一句话送去识别")
        form.addRow("一句话结束等待：", self.voice_silence)

        self.voice_apply_btn = QtWidgets.QPushButton("保存并应用语音设置")
        self.voice_apply_btn.setObjectName("PrimaryButton")
        self.voice_apply_btn.clicked.connect(self._on_apply_voice_settings)
        form.addRow("", self.voice_apply_btn)
        layout.addWidget(box)

        self.voice_hint = QtWidgets.QLabel("")
        self.voice_hint.setWordWrap(True)
        self.voice_hint.setObjectName("PageHint")
        layout.addWidget(self.voice_hint)
        layout.addStretch()

        self.voice_hint.setText("提示：首次使用语音翻译前，请在模型中心下载约 1.53 GB 的语音识别模型。")
        return page

    def _build_text_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("PageSurface")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)

        intro = QtWidgets.QLabel(
            "文字聊天会读取 VALORANT 左下角的聊天框。第一次使用时，"
            "请点击下面的按钮框选聊天区域。"
        )
        intro.setObjectName("PageHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        region_box = QtWidgets.QGroupBox("聊天区域")
        region_layout = QtWidgets.QVBoxLayout(region_box)
        self.roi_status_label = QtWidgets.QLabel(self._roi_description())
        self.roi_status_label.setWordWrap(True)
        region_layout.addWidget(self.roi_status_label)
        region_buttons = QtWidgets.QHBoxLayout()
        self.roi_btn = QtWidgets.QPushButton("选择或重新选择聊天区域")
        self.roi_btn.setObjectName("PrimaryButton")
        self.roi_btn.clicked.connect(self._on_select_roi)
        region_buttons.addWidget(self.roi_btn)
        region_buttons.addStretch()
        region_layout.addLayout(region_buttons)
        layout.addWidget(region_box)

        settings_box = QtWidgets.QGroupBox("文字识别设置")
        form = QtWidgets.QFormLayout(settings_box)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.text_model_label = QtWidgets.QLabel("内置 OCR 模型")
        form.addRow("识别模型：", self.text_model_label)

        self.text_poll_hz = QtWidgets.QDoubleSpinBox()
        self.text_poll_hz.setRange(1.0, 10.0)
        self.text_poll_hz.setSingleStep(0.5)
        self.text_poll_hz.setDecimals(1)
        self.text_poll_hz.setSuffix(" 次/秒")
        self.text_poll_hz.setValue(self._preferences.text.poll_hz)
        self.text_poll_hz.setToolTip("数值越高反应越快，也会多占用一点资源")
        form.addRow("扫描频率：", self.text_poll_hz)

        self.text_min_score = QtWidgets.QDoubleSpinBox()
        self.text_min_score.setRange(0.10, 0.95)
        self.text_min_score.setSingleStep(0.05)
        self.text_min_score.setDecimals(2)
        self.text_min_score.setValue(self._preferences.text.min_score)
        self.text_min_score.setToolTip("识别可信度低于此值的文字会被忽略")
        form.addRow("识别可信度：", self.text_min_score)

        self.text_change_threshold = QtWidgets.QDoubleSpinBox()
        self.text_change_threshold.setRange(0.0, 10.0)
        self.text_change_threshold.setSingleStep(0.5)
        self.text_change_threshold.setDecimals(1)
        self.text_change_threshold.setValue(self._preferences.text.change_threshold)
        self.text_change_threshold.setToolTip("画面变化小于此值时不重复识别")
        form.addRow("画面变化阈值：", self.text_change_threshold)

        self.text_apply_btn = QtWidgets.QPushButton("保存并应用文字设置")
        self.text_apply_btn.setObjectName("PrimaryButton")
        self.text_apply_btn.clicked.connect(self._on_apply_text_settings)
        form.addRow("", self.text_apply_btn)
        layout.addWidget(settings_box)

        self.text_hint = QtWidgets.QLabel("")
        self.text_hint.setWordWrap(True)
        self.text_hint.setObjectName("PageHint")
        layout.addWidget(self.text_hint)
        layout.addStretch()

        self.text_hint.setText("OCR 模型已随安装包内置，不需要再次下载。")
        return page

    def _build_log_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("PageSurface")
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)

        intro = QtWidgets.QLabel(
            "这里显示程序的详细运行记录。平时不需要查看；如果遇到问题，"
            "可以复制全部内容发给开发者。"
        )
        intro.setObjectName("PageHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(QtGui.QFont("Consolas", 9))
        layout.addWidget(self.log_view, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.auto_scroll = QtWidgets.QCheckBox("自动滚动到底部")
        self.auto_scroll.setChecked(True)
        clear_btn = QtWidgets.QPushButton("清空日志")
        copy_btn = QtWidgets.QPushButton("复制全部日志")
        clear_btn.clicked.connect(self.log_view.clear)
        copy_btn.clicked.connect(self._copy_logs)
        buttons.addWidget(self.auto_scroll)
        buttons.addStretch()
        buttons.addWidget(clear_btn)
        buttons.addWidget(copy_btn)
        layout.addLayout(buttons)
        return page

    # ---- 状态和日志 ----

    def _connect_log_bridge(self) -> None:
        if self._log_bridge is None:
            return
        for message in self._log_bridge.history():
            self._append_log(message)
        self._log_bridge.message_ready.connect(self._append_log)

    @QtCore.Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        if self.auto_scroll.isChecked():
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _copy_logs(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.log_view.toPlainText())
        log.info("调试日志已复制到剪贴板")

    def set_status(self, section: str, text: str) -> None:
        self.STATUS_CHANGED.emit(section, text)

    @QtCore.Slot(str, str)
    def _update_status(self, section: str, text: str) -> None:
        label = self.status_labels.get(section)
        if label is None:
            return
        friendly = self._friendly_status(text)
        label.setText(friendly)
        label.setProperty("raw_status", text)
        label.setProperty("state", self._status_state(text))
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _status_state(text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ("error", "unavailable", "not installed", "stopped", "disabled")):
            return "bad"
        if any(word in lowered for word in ("not configured", "starting", "warning", "waiting")):
            return "warn"
        return "ok"

    @staticmethod
    def _friendly_status(text: str) -> str:
        exact = {
            "VALORANT: stopped": "未检测到 VALORANT",
            "Widget: connected": "Game Bar 小组件已连接",
            "Widget: waiting": "等待 Game Bar 小组件连接",
            "Widget: stopped": "后端已停止",
            "ASR: ready": "语音识别模型已就绪",
            "ASR model: not installed": "语音识别模型未安装",
            "ASR: disabled in this edition": "此版本未包含语音聊天",
            "OCR: running": "正在识别聊天",
            "OCR: starting": "正在启动文字识别",
            "OCR: stopped": "文字识别已停止",
            "OCR: ROI not configured": "尚未选择聊天区域",
            "OCR: disabled in this edition": "此版本未包含文字聊天",
            "Hy-MT2: loaded": "翻译模型已就绪",
            "Hy-MT2: unavailable": "翻译模型未安装",
        }
        if text in exact:
            return exact[text]
        if text.startswith("VALORANT: running"):
            return "已检测到 VALORANT（正在工作）"
        if text.startswith("OCR: new line"):
            return "已识别到新的聊天内容"
        if text.startswith("OCR: error"):
            return "文字识别出错：" + text.partition("(")[2].rstrip(")")
        if text.startswith("OCR: unavailable"):
            return "文字识别不可用：" + text.partition("(")[2].rstrip(")")
        if text.startswith("ASR error"):
            return "语音识别出错：" + text.partition(":")[2].strip()
        return text

    # ---- 操作 ----

    def _refresh_pipe_status(self) -> None:
        """Reflect the real Game Bar pipe state instead of assuming it is connected."""

        if self._pipe is None:
            status = "Widget: stopped"
        elif self._pipe.client_connected:
            status = "Widget: connected"
        else:
            status = "Widget: waiting"
        if hasattr(self, "hero_state"):
            hero_text = {
                "Widget: connected": "已连接 · 正在显示字幕",
                "Widget: waiting": "等待小组件连接",
                "Widget: stopped": "后台服务已停止",
            }.get(status, "等待游戏启动")
            hero_state = {
                "Widget: connected": "ok",
                "Widget: waiting": "warn",
                "Widget: stopped": "bad",
            }.get(status, "warn")
            self.hero_state.setText(hero_text)
            self.hero_state.setProperty("state", hero_state)
            self.hero_state.style().unpolish(self.hero_state)
            self.hero_state.style().polish(self.hero_state)
        current = self.status_labels.get("Game Bar")
        if current is not None and current.property("raw_status") == status:
            return
        self.set_status("Game Bar", status)

    def _on_open_gamebar(self) -> None:
        """Open Xbox Game Bar without creating a console window."""

        log.info("用户点击：打开游戏栏")
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl("ms-gamebar:")):
            QtWidgets.QMessageBox.information(
                self,
                "打开失败",
                "Windows 没有找到 Xbox Game Bar。请先在 Microsoft Store 安装或启用它。",
            )

    def _begin_widget_operation(self, text: str) -> None:
        """Show an indeterminate indicator for a Windows operation."""

        self._widget_operation_text = text
        self._widget_operation_started_at = time.monotonic()
        self.widget_operation_progress.show()
        self.widget_operation_hint.show()
        self._update_widget_operation_indicator()
        self._widget_operation_timer.start()

    def _update_widget_operation_indicator(self) -> None:
        if self._widget_operation_started_at is None:
            return
        elapsed = max(0, int(time.monotonic() - self._widget_operation_started_at))
        minutes, seconds = divmod(elapsed, 60)
        elapsed_text = f"{minutes} 分 {seconds:02d} 秒" if minutes else f"{seconds} 秒"
        self.widget_operation_hint.setText(
            f"{self._widget_operation_text} · 已等待 {elapsed_text}。"
            "程序仍在运行，可以继续查看其他页面。"
        )

    def _end_widget_operation(self) -> None:
        self._widget_operation_timer.stop()
        self._widget_operation_started_at = None
        self._widget_operation_text = ""
        self.widget_operation_hint.hide()
        self.widget_operation_progress.hide()

    def _refresh_widget_status(self) -> None:
        """Refresh package availability and enable only valid widget actions."""

        try:
            status = get_widget_status()
        except Exception as exc:
            self._last_widget_status = None
            log.exception("检查 Game Bar 小组件失败")
            self.widget_status_label.setText(f"检查失败：{exc}")
            self.widget_cert_status_label.setText("证书：检查失败")
            self.widget_cert_remove_btn.setEnabled(False)
            self.widget_init_btn.setEnabled(False)
            self.widget_uninstall_btn.setEnabled(False)
            return

        self._last_widget_status = status
        self.widget_status_label.setText(status.detail)
        certificate = status.certificate
        certificate_detail = certificate.detail
        if not certificate_detail.startswith("证书"):
            certificate_detail = f"证书：{certificate_detail}"
        if not certificate.available:
            self.widget_cert_status_label.setText("证书：未找到，请重新安装完整安装包")
        elif certificate.detail.startswith("证书无法"):
            self.widget_cert_status_label.setText(certificate_detail)
        elif certificate.trusted:
            self.widget_cert_status_label.setText("证书：已导入并受信任")
        else:
            self.widget_cert_status_label.setText(certificate_detail)
        operation_busy = (
            self._certificate_remove_task is not None
            or self._widget_package_task is not None
        )
        self.widget_cert_remove_btn.setEnabled(
            certificate.available
            and certificate.trusted
            and not certificate.detail.startswith("证书无法")
            and not operation_busy
        )
        self.widget_init_btn.setEnabled(
            status.payload is not None
            and not operation_busy
        )
        self.widget_uninstall_btn.setEnabled(status.installed and not operation_busy)

    def _on_remove_widget_certificate(self) -> None:
        if (
            self._certificate_remove_task is not None
            or self._widget_package_task is not None
        ):
            return

        status = self._last_widget_status
        if status is None:
            try:
                status = get_widget_status()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(
                    self, "检查失败", f"无法检查小组件状态：\n{exc}"
                )
                return

        prompt = "确定要删除 Sage 的小组件证书吗？"
        if status.installed:
            prompt += "\n\n为避免留下无法重新验证的小组件，程序会先卸载 Game Bar 小组件。"
        prompt += "\n这不会删除翻译模型或其他配置。"
        answer = QtWidgets.QMessageBox.question(
            self,
            "删除小组件证书",
            prompt,
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        log.info("用户点击：删除 Game Bar 小组件证书")
        self.widget_cert_remove_btn.setEnabled(False)
        self.widget_init_btn.setEnabled(False)
        self.widget_uninstall_btn.setEnabled(False)
        self.widget_cert_remove_btn.setText("正在删除…")
        self.widget_cert_status_label.setText(
            "证书：正在删除，请在 Windows 管理员确认中选择“是”…"
        )
        self._begin_widget_operation(
            "正在删除小组件证书，请在 Windows 管理员确认中选择“是”"
        )

        def remove_certificate_and_widget():
            current = get_widget_status()
            if current.installed:
                uninstall_widget()
            return remove_widget_certificate()

        task = _BackgroundTask(remove_certificate_and_widget)
        task.signals.succeeded.connect(self._on_remove_widget_certificate_succeeded)
        task.signals.failed.connect(self._on_remove_widget_certificate_failed)
        self._certificate_remove_task = task
        QtCore.QThreadPool.globalInstance().start(task)

    def _finish_certificate_remove(self) -> None:
        self._certificate_remove_task = None
        self.widget_cert_remove_btn.setText("删除证书")
        self._end_widget_operation()
        self._refresh_widget_status()

    @QtCore.Slot(object)
    def _on_remove_widget_certificate_failed(self, exc: Exception) -> None:
        log.error(
            "删除 Game Bar 小组件证书失败",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        self._finish_certificate_remove()
        QtWidgets.QMessageBox.warning(
            self,
            "证书删除失败",
            "没有完成证书删除。请在 Windows 管理员确认中选择“是”后重试。"
            f"\n\n详细信息：\n{exc}",
        )

    @QtCore.Slot(object)
    def _on_remove_widget_certificate_succeeded(self, _status) -> None:
        self._finish_certificate_remove()
        QtWidgets.QMessageBox.information(
            self,
            "证书已删除",
            "小组件证书已从 Windows 信任列表中删除。\n"
            "翻译模型和其他配置没有受到影响。",
        )

    def _on_initialize_widget(self) -> None:
        if (
            self._certificate_remove_task is not None
            or self._widget_package_task is not None
        ):
            return

        log.info("用户点击：修复 Game Bar 小组件")
        self.widget_cert_remove_btn.setEnabled(False)
        self.widget_init_btn.setEnabled(False)
        self.widget_uninstall_btn.setEnabled(False)
        self.widget_init_btn.setText("正在修复…")
        self.widget_status_label.setText("正在准备证书并安装 Game Bar 小组件，请稍候…")
        self._begin_widget_operation("正在修复 Game Bar 小组件，可能会出现管理员确认")

        task = _BackgroundTask(install_widget)
        task.signals.succeeded.connect(self._on_initialize_widget_succeeded)
        task.signals.failed.connect(self._on_initialize_widget_failed)
        self._widget_package_task = task
        QtCore.QThreadPool.globalInstance().start(task)

    def _finish_widget_package_task(self) -> None:
        self._widget_package_task = None
        self.widget_init_btn.setText("修复小组件")
        self.widget_uninstall_btn.setText("卸载小组件")
        self._end_widget_operation()
        self._refresh_widget_status()

    @QtCore.Slot(object)
    def _on_initialize_widget_failed(self, exc: Exception) -> None:
        log.error(
            "修复 Game Bar 小组件失败",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        self._finish_widget_package_task()
        QtWidgets.QMessageBox.warning(
            self,
            "小组件修复失败",
            f"无法完成证书和小组件安装：\n{exc}",
        )

    @QtCore.Slot(object)
    def _on_initialize_widget_succeeded(self, status) -> None:
        self._finish_widget_package_task()
        QtWidgets.QMessageBox.information(
            self,
            "小组件已准备好",
            f"{status.detail}\n\n请按 Win+G，在小组件列表中打开 Sage。",
        )

    def _on_uninstall_widget(self) -> None:
        if (
            self._certificate_remove_task is not None
            or self._widget_package_task is not None
        ):
            return

        answer = QtWidgets.QMessageBox.question(
            self,
            "卸载小组件",
            "确定要卸载 Sage 的 Game Bar 小组件吗？\n"
            "这不会删除翻译模型或其他配置。",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        log.info("用户点击：卸载 Game Bar 小组件")
        self.widget_cert_remove_btn.setEnabled(False)
        self.widget_init_btn.setEnabled(False)
        self.widget_uninstall_btn.setEnabled(False)
        self.widget_uninstall_btn.setText("正在卸载…")
        self.widget_status_label.setText("正在卸载 Game Bar 小组件，请稍候…")
        self._begin_widget_operation("正在卸载 Game Bar 小组件")

        task = _BackgroundTask(uninstall_widget)
        task.signals.succeeded.connect(self._on_uninstall_widget_succeeded)
        task.signals.failed.connect(self._on_uninstall_widget_failed)
        self._widget_package_task = task
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object)
    def _on_uninstall_widget_failed(self, exc: Exception) -> None:
        log.error(
            "卸载 Game Bar 小组件失败",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        self._finish_widget_package_task()
        QtWidgets.QMessageBox.warning(
            self,
            "小组件卸载失败",
            f"无法卸载 Game Bar 小组件：\n{exc}",
        )

    @QtCore.Slot(object)
    def _on_uninstall_widget_succeeded(self, status) -> None:
        self._finish_widget_package_task()
        QtWidgets.QMessageBox.information(self, "小组件已卸载", status.detail)

    def _on_start(self) -> None:
        log.info("用户点击：启动全部功能")
        if self._orchestrator is not None:
            self._orchestrator.start_all(self._roi_config)
        self._refresh_pipe_status()

    def _on_stop(self) -> None:
        log.info("用户点击：停止全部功能")
        if self._orchestrator is not None:
            self._orchestrator.stop()
        self.set_status("Game Bar", "Pipe: stopped")
        self.set_status(
            "Voice",
            "ASR: stopped",
        )
        self.set_status(
            "Chat",
            "OCR: stopped",
        )

    def _on_apply_voice_settings(self) -> None:
        self._preferences.voice.vad_threshold = self.voice_threshold.value()
        self._preferences.voice.min_silence_ms = self.voice_silence.value()
        self._preferences.normalize()
        try:
            path = save_preferences(self._preferences)
            if self._orchestrator is not None:
                self._orchestrator.configure_voice_settings(
                    vad_threshold=self._preferences.voice.vad_threshold,
                    min_silence_ms=self._preferences.voice.min_silence_ms,
                )
            self.voice_hint.setText(f"语音设置已保存：{path}")
            log.info("语音设置已保存到 %s", path)
        except Exception as exc:
            log.exception("保存语音设置失败")
            QtWidgets.QMessageBox.critical(self, "保存失败", f"语音设置保存失败：{exc}")

    def _on_apply_text_settings(self) -> None:
        self._preferences.text.poll_hz = self.text_poll_hz.value()
        self._preferences.text.min_score = self.text_min_score.value()
        self._preferences.text.change_threshold = self.text_change_threshold.value()
        self._preferences.normalize()
        try:
            path = save_preferences(self._preferences)
            if self._orchestrator is not None:
                self._orchestrator.configure_text_settings(
                    poll_hz=self._preferences.text.poll_hz,
                    min_score=self._preferences.text.min_score,
                    change_threshold=self._preferences.text.change_threshold,
                )
            self.text_hint.setText(f"文字设置已保存：{path}")
            log.info("文字设置已保存到 %s", path)
        except Exception as exc:
            log.exception("保存文字设置失败")
            QtWidgets.QMessageBox.critical(self, "保存失败", f"文字设置保存失败：{exc}")

    def _on_select_roi(self) -> None:
        log.info("用户点击：选择聊天区域")
        try:
            from importlib import import_module

            selector_type = import_module("ui.roi_selector").RegionSelectorDialog
            selector = selector_type(self)
            if selector.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            roi = selector.selected_roi()
            if roi is None:
                return
            path = save_roi_config(roi)
            self._roi_config = roi
            self.roi_status_label.setText(self._roi_description())
            started = True
            if self._orchestrator is not None:
                started = self._orchestrator.configure_chat_roi(roi)
            self.set_status("Chat", "OCR: running" if started else "OCR: unavailable")
            log.info("聊天区域已保存到 %s：%s", path, roi.region)
        except Exception as exc:
            log.exception("选择聊天区域失败")
            QtWidgets.QMessageBox.critical(self, "选择失败", f"无法保存聊天区域：{exc}")

    def _on_open_guide(self) -> None:
        log.info("用户打开使用说明")
        roi_text = "已配置" if self._roi_config is not None else "尚未配置"
        QtWidgets.QMessageBox.information(
            self,
            "使用说明",
            "第一次使用建议按这个顺序操作：\n\n"
            "1. 打开“模型中心”，按需下载翻译模型和语音识别模型。\n"
            "2. 在“文字聊天”页选择 VALORANT 左下角的聊天区域（当前：%s）。\n"
            "3. 保持程序运行，打开 VALORANT。\n"
            "4. 回到“实时状态”确认游戏已检测到。\n\n"
            "OCR 模型已经内置；缺少大模型时不会自动联网下载。" % roi_text,
        )

    def _on_model_manager(self) -> None:
        from ui.model_manager import ModelManagerDialog

        dialog = ModelManagerDialog(self, mode="full")
        dialog.exec()
        self._refresh_model_summary()

    # ---- 辅助 ----

    def _roi_description(self) -> str:
        if self._roi_config is None:
            return "当前还没有选择聊天区域。建议在 VALORANT 开启后框选左下角聊天框。"
        return "已选择聊天区域：显示器 %d，坐标 %s" % (
            self._roi_config.output_idx + 1,
            self._roi_config.region,
        )

    def _refresh_model_summary(self) -> None:
        try:
            from model_store import model_specs_for_mode, model_status

            status_text = {
                "embedded": "内置",
                "installed": "已安装",
                "missing": "未安装",
                "incomplete": "未完成",
                "invalid": "需校验",
            }
            values: dict[str, str] = {}
            for spec in model_specs_for_mode("full"):
                values[spec.key] = status_text.get(model_status(spec), "未知")

            self.voice_model_label.setText(
                f"{values.get('whisper-medium', '未安装')}（约 1.53 GB）"
            )
            self.text_model_label.setText(
                "内置（检测：%s；识别：%s）"
                % (values.get("ocr-det", "未知"), values.get("ocr-rec", "未知"))
            )

            parts = []
            if "hy-mt2" in values:
                parts.append(f"翻译模型：{values['hy-mt2']}")
            if "whisper-medium" in values:
                parts.append(f"语音识别：{values['whisper-medium']}")
            if "ocr-det" in values and "ocr-rec" in values:
                parts.append("OCR：内置")
            self.model_summary.setText("模型状态：" + " · ".join(parts))
        except Exception as exc:
            log.warning("刷新模型状态失败：%s", exc)
            self.model_summary.setText("模型状态：打开模型中心查看")
