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
PIPE_WRITE_TIMEOUT_MS = 1500
PIPE_IO_POLL_MS = 50
HEARTBEAT_INTERVAL_SECONDS = 2.0

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "package_identity.json"


def _build_security_attributes() -> "win32security.SECURITY_ATTRIBUTES":
    """构造 pipe 的安全属性（SECURITY_ATTRIBUTES，含 DACL）。"""
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
    """单客户端广播式 Pipe Server。"""

    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
        *,
        write_timeout_ms: int = PIPE_WRITE_TIMEOUT_MS,
    ) -> None:
        super().__init__(name="PipeServer", daemon=True)
        self.pipe_name = pipe_name
        self.write_timeout_ms = max(100, int(write_timeout_ms))
        self._stop_event = threading.Event()
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._client_connected = threading.Event()

    def broadcast(self, message: dict[str, Any]) -> None:
        """把一条协议消息写入发送队列（非阻塞）。"""
        from ipc import protocol

        try:
            payload = protocol.encode(message)
        except Exception:
            log.exception("消息序列化失败: %r", message)
            return

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
                    1,
                    PIPE_BUFFER_SIZE,
                    PIPE_BUFFER_SIZE,
                    PIPE_TIMEOUT_MS,
                    _build_security_attributes(),
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
                log.debug("Named Pipe 返回: %s", exc)
            finally:
                self._client_connected.clear()
                if handle is not None:
                    try:
                        handle.Close()
                    except Exception:
                        pass

        log.info("PipeServer 已停止")

    def _connect(self, handle: Any) -> bool:
        """等待客户端连接，同时允许 stop() 在约 100ms 内打断等待。"""
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        connected = False
        try:
            try:
                win32pipe.ConnectNamedPipe(handle, overlapped)
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
                self._cancel_pending_io(handle, overlapped)
                return False
            return connected
        finally:
            try:
                win32file.CloseHandle(overlapped.hEvent)
            except Exception:
                pass

    @staticmethod
    def _error_code(exc: BaseException) -> int | None:
        code = getattr(exc, "winerror", None)
        if code is None and getattr(exc, "args", None):
            try:
                code = int(exc.args[0])
            except (TypeError, ValueError):
                return None
        return code

    def _cancel_pending_io(self, handle: Any, overlapped: Any) -> None:
        """Cancel and reap one overlapped operation issued by this thread."""
        try:
            # CancelIo cancels operations issued by the calling thread. Both
            # ConnectNamedPipe and WriteFile are issued by PipeServer.run().
            win32file.CancelIo(handle)
        except pywintypes.error:
            pass
        try:
            win32event.WaitForSingleObject(overlapped.hEvent, 1000)
        except Exception:
            pass
        try:
            win32file.GetOverlappedResult(handle, overlapped, False)
        except pywintypes.error:
            # ERROR_OPERATION_ABORTED / broken pipe are expected after cancel.
            pass

    def _write_with_timeout(self, handle: Any, payload: bytes) -> bool:
        """Write one complete message without allowing a hung client to block us."""
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        # pywin32 requires the write buffer to remain alive until overlapped I/O
        # completes. Attach it to the OVERLAPPED object for that lifetime.
        overlapped.object = payload
        try:
            try:
                err, written = win32file.WriteFile(handle, payload, overlapped)
            except pywintypes.error as exc:
                log.info("写入失败（Widget 断开）: %s", exc)
                return False

            if err == 0:
                return int(written) == len(payload)
            if err != winerror.ERROR_IO_PENDING:
                log.info("写入失败（err=%s）", err)
                return False

            deadline = time.monotonic() + self.write_timeout_ms / 1000.0
            while not self._stop_event.is_set():
                remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    log.warning(
                        "Widget pipe 写入超过 %dms，断开停滞客户端",
                        self.write_timeout_ms,
                    )
                    self._cancel_pending_io(handle, overlapped)
                    return False
                result = win32event.WaitForSingleObject(
                    overlapped.hEvent,
                    min(PIPE_IO_POLL_MS, remaining_ms),
                )
                if result == win32event.WAIT_OBJECT_0:
                    try:
                        transferred = win32file.GetOverlappedResult(
                            handle, overlapped, False
                        )
                    except pywintypes.error as exc:
                        log.info("异步写入失败（Widget 断开）: %s", exc)
                        return False
                    return int(transferred) == len(payload)

            self._cancel_pending_io(handle, overlapped)
            return False
        finally:
            overlapped.object = None
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
            if not self._write_with_timeout(handle, payload):
                return

        # Do not drain the queue synchronously on shutdown. Subtitle messages
        # are ephemeral, and bounded shutdown is more important than delivery
        # to a client that may already be hung/stopping.
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
