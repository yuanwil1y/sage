"""Sage runtime entry point.

源码默认以 headless 模式运行，打包版的安装快捷方式会传入 ``--ui``。
桌面模式会把启动过程和后台线程日志接入 GUI 的“调试日志”标签页，用户
不需要同时面对一个命令行窗口。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from config.preferences import load_preferences
from config.roi import load_roi_config
from ipc import protocol
from ipc.pipe_server import PipeServer
from pipeline.orchestrator import TranslatorOrchestrator
from translation.hy_mt2_server import HyMT2ServerManager
from translation.hy_mt2_translator import HyMT2LocalTranslator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("main")

def packaged_ui_requested() -> bool:
    """Use the GUI by default for frozen desktop builds.

    Source runs keep their historical headless default.  ``--headless`` remains
    available for troubleshooting a packaged build without a visible window.
    """

    return "--headless" not in sys.argv and (
        "--ui" in sys.argv or bool(getattr(sys, "frozen", False))
    )


def cleanup_gamebar() -> int:
    """Remove the Game Bar package and its bundled certificate for uninstall."""

    from gamebar_manager import remove_widget_certificate, uninstall_widget

    cleanup_errors: list[str] = []
    try:
        uninstall_widget()
    except Exception as exc:
        cleanup_errors.append(f"小组件卸载失败：{exc}")
    try:
        remove_widget_certificate()
    except Exception as exc:
        cleanup_errors.append(f"证书删除失败：{exc}")
    if cleanup_errors:
        log.error("；".join(cleanup_errors))
        return 1
    return 0


def initialize_gamebar() -> int:
    """Install certificate, dependencies, widget package and loopback access."""

    from gamebar_manager import install_widget

    try:
        status = install_widget()
    except Exception:
        log.exception("Game Bar 小组件自动安装失败")
        return 1
    if not status.installed:
        log.error("Game Bar 小组件安装命令完成，但没有检测到已注册的软件包")
        return 1
    log.info("Game Bar 小组件自动安装完成：%s", status.detail)
    return 0


def run(use_ui: bool = False) -> None:
    app = None
    gui_log_handler = None
    gui_log_bridge = None
    if use_ui:
        from PySide6 import QtWidgets
        from ui.log_bridge import install_gui_logging

        app = QtWidgets.QApplication(sys.argv)
        app.setApplicationName("Sage")
        app.setOrganizationName("Williamyuan132")
        gui_log_bridge, gui_log_handler = install_gui_logging()

    pipe: PipeServer | None = None
    mt2: HyMT2ServerManager | None = None
    orchestrator: TranslatorOrchestrator | None = None
    try:
        # 1. Named Pipe Server
        # 生产版 Game Bar 通信由 MSIX 内的 SageWidgetService 接管：
        # Python 通过 Named Pipe 把字幕交给包内本地服务；Game Bar 小组件
        # 再按参考项目的方式通过 127.0.0.1 HTTP 长轮询读取事件。
        pipe = PipeServer()
        pipe.start()
        log.info("Named Pipe Server 已启动: %s", pipe.pipe_name)

        # 2. 本地 Hy-MT2 翻译引擎
        mt2 = HyMT2ServerManager()
        translator = None
        try:
            mt2.start()
            mt2.wait_ready(timeout=60.0)
            log.info("本地 Hy-MT2 翻译引擎就绪: %s", mt2.base_url)
            translator = HyMT2LocalTranslator(mt2.base_url)
        except FileNotFoundError as exc:
            log.warning("Hy-MT2 未启动（缺少运行时/模型）：%s", exc)
        except Exception as exc:
            log.warning("Hy-MT2 启动失败，后端继续运行（无翻译）：%s", exc)

        if translator is None:
            # 缺少大模型时仍让用户打开 GUI、配置 ROI 和模型；链路测试时透传原文。
            class _Passthrough:
                def translate(self, text, source_lang="日语", target_lang="简体中文"):
                    return text

            translator = _Passthrough()

        preferences = load_preferences()
        orchestrator = TranslatorOrchestrator(pipe, translator, mode="full")
        if orchestrator.voice_enabled:
            orchestrator.configure_voice_settings(
                vad_threshold=preferences.voice.vad_threshold,
                min_silence_ms=preferences.voice.min_silence_ms,
            )
        if orchestrator.chat_enabled:
            orchestrator.configure_text_settings(
                poll_hz=preferences.text.poll_hz,
                min_score=preferences.text.min_score,
                change_threshold=preferences.text.change_threshold,
            )

        roi_config = load_roi_config()
        orchestrator.start_all(roi_config)
        if orchestrator.voice_enabled:
            log.info("ProcessFinder 已启动，等待 VALORANT-Win64-Shipping.exe……")
        if orchestrator.chat_enabled and roi_config is None:
            log.info("尚未配置聊天区域；请打开“文字聊天”页进行选择")

        if use_ui:
            from ui.control_window import ControlWindow

            window = ControlWindow(
                orchestrator,
                pipe=pipe,
                roi_config=roi_config,
                preferences=preferences,
                log_bridge=gui_log_bridge,
            )
            orchestrator.set_status_callback(window.set_status)
            window.show()
            window.set_status(
                "Local Translation",
                "Hy-MT2: loaded" if mt2.running else "Hy-MT2: unavailable",
            )
            window.set_status(
                "Voice",
                (
                    "ASR: ready"
                    if getattr(orchestrator.transcriber, "model_available", False)
                    else "ASR model: not installed"
                )
                if orchestrator.voice_enabled
                else "ASR: disabled in this edition",
            )
            window.set_status(
                "Chat",
                (
                    "OCR: ROI configured"
                    if roi_config is not None
                    else "OCR: ROI not configured"
                )
                if orchestrator.chat_enabled
                else "OCR: disabled in this edition",
            )
            log.info("桌面控制界面已启动")
            app.exec()
            return

        # 5. headless 模式（开发和自动化测试使用）
        log.info("headless 模式运行中（Ctrl+C 退出）")
        try:
            while True:
                pipe.broadcast(protocol.heartbeat_message())
                time.sleep(2.0)
        except KeyboardInterrupt:
            log.info("收到中断，正在停止……")
    finally:
        if orchestrator is not None:
            orchestrator.stop()
        if mt2 is not None:
            mt2.stop()
        if pipe is not None:
            pipe.stop()
        log.info("后端已退出")
        if gui_log_handler is not None:
            from ui.log_bridge import uninstall_gui_logging

            uninstall_gui_logging(gui_log_handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sage runtime")
    parser.add_argument("--ui", action="store_true", help="启动桌面控制界面")
    parser.add_argument(
        "--cleanup-gamebar",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--initialize-gamebar",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if args.cleanup_gamebar:
        raise SystemExit(cleanup_gamebar())
    elif args.initialize_gamebar:
        raise SystemExit(initialize_gamebar())
    else:
        run(use_ui=args.ui or packaged_ui_requested())
