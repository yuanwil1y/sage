"""Black-box integration test for the packaged Game Bar local service."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

from ipc.pipe_server import PipeServer


SERVICE_EXE = (
    Path(__file__).resolve().parents[2]
    / "gamebar-widget"
    / "ServicePayload"
    / "SageWidgetService.exe"
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_health(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.05)
    raise AssertionError("SageWidgetService health endpoint did not become ready")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only Game Bar service")
@pytest.mark.skipif(not SERVICE_EXE.exists(), reason="build SageWidgetService first")
def test_named_pipe_to_http_event_chain() -> None:
    port = _free_port()
    suffix = "SageWidgetServiceTest_" + uuid.uuid4().hex
    pipe_name = rf"\\.\pipe\LOCAL\{suffix}"
    server = PipeServer(pipe_name=pipe_name)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            str(SERVICE_EXE),
            "--port",
            str(port),
            "--pipe-name",
            rf"LOCAL\{suffix}",
        ],
        creationflags=creation_flags,
    )
    server.start()
    try:
        _wait_health(port)
        assert server._client_connected.wait(timeout=5.0)

        expected = {
            "v": 1,
            "type": "subtitle",
            "source": "voice",
            "id": "integration-1",
            "original": "こんにちは",
            "translated": "你好",
            "ts": time.time(),
        }
        server.broadcast(expected)

        response = requests.get(
            f"http://127.0.0.1:{port}/events?after=0&wait_ms=3000",
            timeout=5.0,
        )
        response.raise_for_status()
        batch = response.json()
        assert isinstance(batch["session"], str) and batch["session"]
        assert batch["cursor"] >= 1
        assert batch["events"][-1]["payload"] == expected
    finally:
        server.stop()
        process.terminate()
        process.wait(timeout=5.0)
