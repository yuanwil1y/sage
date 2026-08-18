"""Audio Capture Reader（规格文档第 20、21 节）。

负责启动并监控 native helper `valorant_audio_capture.exe`，
持续读取其 stdout 输出的 PCM 字节（s16le 44.1k stereo），喂给回调。

生命周期：
- start(pid)：启动 helper 子进程，开启读线程
- 回调 on_pcm(bytes)：每个 PCM chunk
- stop()：终止子进程

进程退出后由上层（orchestrator）决定是否按新 pid 重启。
"""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from paths import native_exe_path

log = logging.getLogger(__name__)

_DEFAULT_EXE = native_exe_path()

_CHUNK = 65536  # 每次读 64KB


class AudioCaptureReader:
    """启动 native loopback helper 并流式读取 PCM。"""

    def __init__(
        self,
        exe_path: Path | str | None = None,
        on_pcm: Callable[[bytes], None] | None = None,
    ) -> None:
        self.exe_path = Path(exe_path) if exe_path else _DEFAULT_EXE
        self.on_pcm = on_pcm
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def assert_available(self) -> None:
        if not self.exe_path.exists():
            raise FileNotFoundError(f"native helper 不存在: {self.exe_path}")

    def start(self, target_pid: int) -> None:
        """针对 target_pid 启动捕获。若已在运行则先停止。"""
        self.assert_available()
        if self.running:
            self.stop()

        cmd = [str(self.exe_path), "--mode", "process", "--pid", str(target_pid)]
        log.info("启动 audio-capture: %s", " ".join(cmd))
        self._stop_event.clear()

        # audio-capture is a console-subsystem helper because it streams raw
        # PCM through stdout.  Keep that pipe, but suppress the helper's
        # console window in the installed GUI on Windows.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = None
        if creationflags and hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            # 二进制 stdout，不做换行转换
            bufsize=0,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        self._reader_thread = threading.Thread(
            target=self._read_loop, name="AudioCaptureReader", daemon=True
        )
        self._reader_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            except Exception:
                pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        self._proc = None
        self._reader_thread = None

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while not self._stop_event.is_set():
                chunk = self._proc.stdout.read(_CHUNK)
                if not chunk:
                    break  # helper 退出，stdout 关闭
                if self.on_pcm is not None:
                    self.on_pcm(chunk)
        except Exception as exc:
            log.warning("读取 PCM 结束/异常: %s", exc)
        finally:
            log.info("audio-capture 读线程结束（进程 pid=%s）", self._proc.pid if self._proc else None)
