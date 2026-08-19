"""PipeServer 集成测试：模拟 Game Bar Widget 客户端连接并读取 NDJSON。"""

import threading
import time
import uuid

import pywintypes
import win32file
import win32pipe

from ipc import protocol
from ipc.pipe_server import PipeServer
from models.messages import TranslationResult

_READ_TIMEOUT_MS = 3000


def _read_line(handle, timeout_ms: int = _READ_TIMEOUT_MS) -> bytes:
    """从 message-mode pipe 读一条完整消息。"""
    _, data = win32file.ReadFile(handle, 65536)
    return data


def _test_pipe_name() -> str:
    return rf"\\.\pipe\LOCAL\SagePipeTest_{uuid.uuid4().hex}"


def _connect_client(pipe_name: str) -> object:
    """模拟 Widget：打开 pipe 客户端（带重试）。"""
    last_exc: Exception | None = None
    for _ in range(50):  # 最多约 5 秒
        try:
            return win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error as exc:
            last_exc = exc
            time.sleep(0.1)
    raise AssertionError(f"无法连接 pipe: {last_exc}")


def _wait_connected(server: PipeServer, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.client_connected:
            return
        time.sleep(0.02)
    raise AssertionError("server 未在超时内感知到客户端连接")


def test_subtitle_broadcast_delivery() -> None:
    pipe_name = _test_pipe_name()
    server = PipeServer(pipe_name=pipe_name)
    server.start()
    client = None
    try:
        client = _connect_client(pipe_name)
        _wait_connected(server)

        result = TranslationResult(
            source_type="voice", original="テスト", translated="测试", id="v-1"
        )
        server.broadcast(protocol.subtitle_message(result))

        raw = _read_line(client)
        msg = protocol.decode_line(raw)
        assert msg["type"] == "subtitle"
        assert msg["original"] == "テスト"
        assert msg["translated"] == "测试"
    finally:
        if client is not None:
            client.Close()
        server.stop()


def test_broadcast_before_client_is_dropped() -> None:
    """无客户端时 broadcast 不阻塞、不崩溃（消息丢弃）。"""
    server = PipeServer(pipe_name=_test_pipe_name())
    server.start()
    try:
        time.sleep(0.2)
        result = TranslationResult(
            source_type="chat", original="x", translated="y", id="c-2"
        )
        server.broadcast(protocol.subtitle_message(result))
        server.broadcast(protocol.heartbeat_message())
        assert not server.client_connected
    finally:
        server.stop()
        assert not server.is_alive()


def test_stop_without_client_is_cancellable() -> None:
    """没有客户端时，stop 不应卡在 ConnectNamedPipe。"""
    server = PipeServer(pipe_name=_test_pipe_name())
    server.start()
    try:
        time.sleep(0.2)
    finally:
        server.stop(timeout=1.5)
    assert not server.is_alive()


def test_client_reconnect_after_disconnect() -> None:
    """客户端断开后，server 继续等待并接受下一次连接（规格第 16 节）。"""
    pipe_name = _test_pipe_name()
    server = PipeServer(pipe_name=pipe_name)
    server.start()
    try:
        client1 = _connect_client(pipe_name)
        client1.Close()
        # The server sends its own periodic heartbeat. This must notice a
        # stale client even when the Python headless main is not running.
        deadline = time.monotonic() + 3.0
        while server.client_connected and time.monotonic() < deadline:
            time.sleep(0.02)

        client2 = _connect_client(pipe_name)
        _wait_connected(server)

        result = TranslationResult(
            source_type="chat", original="ミッド二人", translated="中路两个", id="c-3"
        )
        server.broadcast(protocol.subtitle_message(result))
        raw = _read_line(client2)
        assert protocol.decode_line(raw)["id"] == "c-3"
        client2.Close()
    finally:
        server.stop()
