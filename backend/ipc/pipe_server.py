"""Named Pipe Server（规格文档第 12~14、16 节）。

- 方向：Python Backend = Pipe Server，MSIX 内 SageWidgetService = Pipe Client，单向输出。
- 名称：LOCAL\\ValorantTranslator。
- 安全：pywin32 设置 Security Descriptor；
  开发阶段使用 WORLD SID 以便调试，
  发布前应替换为「Package SID + WORLD」的精确 ACL（见 backend/config/package_identity.json）。

本地服务断开后 server 继续存活，等待下一次 ConnectNamedPipe；
broadcast() 在无客户端时丢弃消息（字幕类消息允许丢弃，因为过期很快）。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32pipe
import win32security
import winerror

log = logging.getLogger(__name__)

PIPE_NAME = r"\\.\pipe\LOCAL\ValorantTranslator"
PIPE_BUFFER_SIZE = 65536
PIPE_TIMEOUT_MS = 2000  # CreateNamedPipe 的系统超时；连接本身使用 overlapped
HEARTBEAT_INTERVAL_SECONDS = 2.0

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "package_identity.json"


def _build_security_attributes() -> "win32security.SECURITY_ATTRIBUTES":
    """构造 pipe 的安全属性（SECURITY_ATTRIBUTES，含 DACL）。

    开发阶段：Everyone (WORLD SID) 可读 —— 便于本地调试。
    正式阶段：从 config/package_identity.json 读取 Widget 的 Package SID，
    与 WORLD 一并加入 DACL（对应 Microsoft Game Bar Desktop Communication 指南）。
    """
    everyone = win32security.ConvertStringSidToSid("S-1-1-0")

    package_sid: Optional[win32security.PySID] = None
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            sid_str = data.get("package_sid")
            if sid_str:
                package_sid = win32security.ConvertStringSidToSid(sid_str)
                log.info("Pipe ACL: 使用 config 中的 Package SID %s", sid_str)
    except Exception:
        log.exception("读取 %s 失败，回退为 WORLD-only ACL", _CONFIG_PATH)

    # DACL：Everyone + (可选) Widget Package SID，均授予 GENERIC_READ
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_READ, everyone)
    if package_sid is not None:
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, win32con.GENERIC_READ, package_sid
        )

    sd = win32security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(1, dacl, 0)

    sa = win32security.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = sd
    return sa


class PipeServer(threading.Thread):
    """单客户端广播式 Pipe Server。

    用法：
        server = PipeServer()
        server.start()
        server.broadcast(protocol.subtitle_message(result))
        server.stop()
    """

    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
    ) -> None:
        super().__init__(name="PipeServer", daemon=True)
        self.pipe_name = pipe_name
        self._stop_event = threading.Event()
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._client_connected = threading.Event()

    # ---- 对外的控制接口 ----

    def broadcast(self, message: dict[str, Any]) -> None:
        """把一条协议消息写入发送队列（非阻塞）。"""
        from ipc import protocol

        try:
            payload = protocol.encode(message)
        except Exception:
            log.exception("消息序列化失败: %r", message)
            return

        # Named-pipe messages are ephemeral.  Do not accumulate stale subtitles
        # while no compatible pipe client is attached.
        if not self._client_connected.is_set():
            return
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            log.warning("发送队列已满，丢弃一条消息")

    @property
    def client_connected(self) -> bool:
        return self._client_connected.is_set()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self.join(timeout=timeout)
        if self.is_alive():
            log.warning("PipeServer 未能在 %.1f 秒内退出", timeout)

    # ---- 线程主体 ----

    def run(self) -> None:
        log.info("PipeServer 启动: %s", self.pipe_name)
        while not self._stop_event.is_set():
            handle = None
            try:
                handle = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    win32pipe.PIPE_ACCESS_OUTBOUND | win32file.FILE_FLAG_OVERLAPPED,
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    1,  # 单实例：V1 只有一个 Widget 客户端
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    PIPE_TIMEOUT_MS,
                    _build_security_attributes(),  # 每次重建，SECURITY_ATTRIBUTES 不可复用
                )
            except pywintypes.error as exc:
                log.error("CreateNamedPipe 失败: %s", exc)
                self._stop_event.wait(2.0)
                continue

            try:
                if not self._connect(handle):
                    continue
                log.info("Widget 已连接")
                self._client_connected.set()
                self._serve(handle)
            except pywintypes.error as exc:
                # 109/231 = pipe 被断开，121 = 超时，均为正常循环
                log.debug("ConnectNamedPipe 返回: %s", exc)
            finally:
                self._client_connected.clear()
                try:
                    handle.Close()
                except Exception:
                    pass

        log.info("PipeServer 已停止")

    def _connect(self, handle: Any) -> bool:
        """等待客户端连接，同时允许 stop() 在 100ms 内打断等待。

        ``ConnectNamedPipe(handle, None)`` 是同步调用。旧实现会让服务线程
        卡在这里，GUI 退出或后台停止时最多要等系统超时。使用 overlapped
        连接后，线程只等待一个事件，并定期检查自己的停止事件；取消操作
        也发生在发起 I/O 的同一线程，兼容 pywin32 只暴露 CancelIo 的情况。
        """
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        connected = False
        try:
            try:
                win32pipe.ConnectNamedPipe(handle, overlapped)
                # pywin32 may return None both for an immediate completion and
                # for an overlapped request that is still pending. Inspect the
                # event instead of assuming the former.
                connected = (
                    win32event.WaitForSingleObject(overlapped.hEvent, 0)
                    == win32event.WAIT_OBJECT_0
                )
            except pywintypes.error as exc:
                code = getattr(exc, "winerror", None)
                if code is None and exc.args:
                    code = exc.args[0]
                if code == winerror.ERROR_PIPE_CONNECTED:
                    connected = True
                elif code != winerror.ERROR_IO_PENDING:
                    raise

            while not connected and not self._stop_event.is_set():
                result = win32event.WaitForSingleObject(overlapped.hEvent, 100)
                if result == win32event.WAIT_OBJECT_0:
                    connected = True

            if self._stop_event.is_set() and not connected:
                try:
                    # CancelIo only cancels requests issued by this thread,
                    # which is exactly where ConnectNamedPipe was issued.
                    win32file.CancelIo(handle)
                except pywintypes.error:
                    pass
                win32event.WaitForSingleObject(overlapped.hEvent, 1000)
                return False
            return connected
        finally:
            try:
                win32file.CloseHandle(overlapped.hEvent)
            except Exception:
                pass

    def _serve(self, handle: Any) -> None:
        """连接存续期间，持续把队列里的字节写入 pipe。"""
        from ipc import protocol

        next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
        while not self._stop_event.is_set():
            remaining = max(0.0, next_heartbeat - time.monotonic())
            try:
                payload = self._queue.get(timeout=min(0.2, remaining))
            except queue.Empty:
                if time.monotonic() >= next_heartbeat:
                    payload = protocol.encode(protocol.heartbeat_message())
                    next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
                else:
                    continue
            try:
                win32file.WriteFile(handle, payload)
            except pywintypes.error as exc:
                log.info("写入失败（Widget 断开）: %s", exc)
                return

        # 停止前清空剩余消息，尽力投递
        while True:
            try:
                payload = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                win32file.WriteFile(handle, payload)
            except pywintypes.error:
                break
