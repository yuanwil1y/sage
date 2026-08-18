"""Hy-MT2 llama-server 生命周期管理（规格文档第 32.3、32.4 节）。

HyMT2ServerManager 负责：
- 检查 llama-server.exe 与 GGUF 模型文件
- 选择空闲 localhost port
- 启动 llama-server（CPU-only / low priority / offline）
- 等待 /health 或 API ready
- 记录 PID、监控、异常退出后重启（V1 先做启动+等待+退出清理）
- 程序退出时终止子进程
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from paths import model_dir, runtime_dir

log = logging.getLogger(__name__)

DEFAULT_SERVER_EXE = runtime_dir() / "llama-server.exe"
DEFAULT_MODEL = model_dir() / "Hy-MT2-1.8B-Q4_K_M.gguf"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18088
DEFAULT_HEALTH_PATH = "/health"


def _find_free_port(start: int = DEFAULT_PORT) -> int:
    """从 start 开始找一个空闲 localhost 端口。"""
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((DEFAULT_HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口")


class HyMT2ServerManager:
    def __init__(
        self,
        *,
        server_exe: Path | str | None = None,
        model_path: Path | str | None = None,
        host: str = DEFAULT_HOST,
        port: int | None = None,
        threads: int = 2,
        threads_batch: int = 2,
        ctx_size: int = 2048,
        n_predict: int = 128,
        priority: int = -1,
        poll: int = 0,
        device: str = "none",
        offline: bool = True,
    ) -> None:
        self.server_exe = Path(server_exe) if server_exe else DEFAULT_SERVER_EXE
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL
        self.host = host
        self.port = port if port is not None else _find_free_port()
        self.threads = threads
        self.threads_batch = threads_batch
        self.ctx_size = ctx_size
        self.n_predict = n_predict
        self.priority = priority
        self.poll = poll
        self.device = device
        self.offline = offline

        self._proc: Optional[subprocess.Popen] = None

    # ---- 属性 ----

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}{DEFAULT_HEALTH_PATH}"

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    # ---- 启动 / 就绪 / 停止 ----

    def assert_prerequisites(self) -> None:
        """启动前检查可执行文件与模型文件是否存在。"""
        if not self.server_exe.exists():
            raise FileNotFoundError(f"llama-server.exe 不存在: {self.server_exe}")
        if not self.model_path.exists():
            raise FileNotFoundError(f"GGUF 模型不存在: {self.model_path}")

    def start(self) -> None:
        self.assert_prerequisites()
        if self.running:
            log.info("llama-server 已在运行 (pid=%s)", self.pid)
            return

        cmd = [
            str(self.server_exe),
            "-m", str(self.model_path),
            "--host", self.host,
            "--port", str(self.port),
            "--threads", str(self.threads),
            "--threads-batch", str(self.threads_batch),
            "--ctx-size", str(self.ctx_size),
            "--n-predict", str(self.n_predict),
            "--prio", str(self.priority),
            "--poll", str(self.poll),
            "--device", self.device,
        ]
        if self.offline:
            cmd.append("--offline")

        log.info("启动 llama-server: %s", " ".join(cmd))
        # stdout/stderr 重定向到 DEVNULL，避免阻塞；日志由 llama-server 自身输出
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log.info("llama-server 已启动 (pid=%s)", self.pid)

    def wait_ready(self, timeout: float = 60.0) -> None:
        """轮询 /health 直到 ready 或超时。"""
        import httpx

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.running:
                raise RuntimeError("llama-server 进程已退出")
            try:
                resp = httpx.get(self.health_url, timeout=1.0)
                if resp.status_code == 200:
                    log.info("llama-server ready: %s", self.health_url)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"llama-server 在 {timeout}s 内未就绪: {self.health_url}")

    def stop(self, timeout: float = 10.0) -> None:
        """终止子进程（程序退出时调用）。"""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            log.info("停止 llama-server (pid=%s)", self.pid)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning("llama-server 未在 %ss 内退出，强制 kill", timeout)
                self._proc.kill()
                self._proc.wait()
        self._proc = None
