"""Supervised native audio-capture process reader.

The helper writes raw PCM (s16le, 44.1 kHz, stereo) to stdout.  This class
keeps the stdout reader lightweight, drains stderr for diagnostics, and
restarts the helper with bounded exponential backoff after an unexpected exit.
Intentional ``stop()`` never triggers a restart.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from paths import native_exe_path
from windows_capabilities import require_process_loopback_support

log = logging.getLogger(__name__)

_DEFAULT_EXE = native_exe_path()
_CHUNK = 65536
_STDERR_CHUNK = 4096

FailureCallback = Callable[[int | None, str, int], None]
RestartCallback = Callable[[int, int], None]


class AudioCaptureReader:
    """Launch and supervise ``valorant_audio_capture.exe`` for one target PID."""

    def __init__(
        self,
        exe_path: Path | str | None = None,
        on_pcm: Callable[[bytes], None] | None = None,
        *,
        on_failure: FailureCallback | None = None,
        on_before_restart: RestartCallback | None = None,
        restart_initial_delay: float = 0.5,
        restart_max_delay: float = 8.0,
        restart_stable_seconds: float = 30.0,
        stderr_tail_bytes: int = 16 * 1024,
        enforce_platform_support: bool | None = None,
    ) -> None:
        self.exe_path = Path(exe_path) if exe_path else _DEFAULT_EXE
        self.on_pcm = on_pcm
        self.on_failure = on_failure
        self.on_before_restart = on_before_restart
        self.restart_initial_delay = max(0.05, float(restart_initial_delay))
        self.restart_max_delay = max(self.restart_initial_delay, float(restart_max_delay))
        self.restart_stable_seconds = max(0.0, float(restart_stable_seconds))
        self.stderr_tail_bytes = max(1024, int(stderr_tail_bytes))
        if enforce_platform_support is None:
            enforce_platform_support = self.exe_path.name.lower() == "valorant_audio_capture.exe"
        self.enforce_platform_support = bool(enforce_platform_support)

        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._proc_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._stderr_chunks: deque[bytes] = deque()
        self._stderr_size = 0
        self._target_pid: int | None = None

    @property
    def running(self) -> bool:
        with self._proc_lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        with self._proc_lock:
            return self._proc.pid if self._proc else None

    @property
    def stderr_tail(self) -> str:
        with self._stderr_lock:
            data = b"".join(self._stderr_chunks)
        return data.decode("utf-8", errors="replace").strip()

    def assert_available(self) -> None:
        if self.enforce_platform_support:
            require_process_loopback_support()
        if not self.exe_path.exists():
            raise FileNotFoundError(f"native helper 不存在: {self.exe_path}")

    def _build_command(self, target_pid: int) -> list[str]:
        return [str(self.exe_path), "--mode", "process", "--pid", str(target_pid)]

    def _popen(self, cmd: list[str]) -> subprocess.Popen:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = None
        if creationflags and hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )

    def start(self, target_pid: int) -> None:
        """Start supervision for ``target_pid``; replacing any prior target."""
        self.assert_available()
        self.stop()
        self._target_pid = int(target_pid)
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._supervise_loop,
            args=(self._target_pid,),
            name="AudioCaptureSupervisor",
            daemon=True,
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Stop the helper and supervisor without scheduling a restart."""
        self._stop_event.set()
        with self._proc_lock:
            proc = self._proc
        self._terminate_process(proc)

        thread = self._reader_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=4.0)
        stderr_thread = self._stderr_thread
        if stderr_thread is not None and stderr_thread is not threading.current_thread():
            stderr_thread.join(timeout=1.0)

        with self._proc_lock:
            self._proc = None
        self._reader_thread = None
        self._stderr_thread = None
        self._target_pid = None

    @staticmethod
    def _terminate_process(proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception:
            log.exception("终止 audio-capture helper 失败")

    def _clear_stderr(self) -> None:
        with self._stderr_lock:
            self._stderr_chunks.clear()
            self._stderr_size = 0

    def _append_stderr(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._stderr_lock:
            self._stderr_chunks.append(chunk)
            self._stderr_size += len(chunk)
            while self._stderr_size > self.stderr_tail_bytes and self._stderr_chunks:
                removed = self._stderr_chunks.popleft()
                self._stderr_size -= len(removed)

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        try:
            while not self._stop_event.is_set():
                chunk = proc.stderr.read(_STDERR_CHUNK)
                if not chunk:
                    break
                self._append_stderr(chunk)
        except Exception:
            log.exception("读取 audio-capture stderr 失败")

    def _launch_once(self, target_pid: int) -> subprocess.Popen:
        cmd = self._build_command(target_pid)
        log.info("启动 audio-capture: %s", " ".join(cmd))
        self._clear_stderr()
        proc = self._popen(cmd)
        with self._proc_lock:
            self._proc = proc
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(proc,),
            name="AudioCaptureStderr",
            daemon=True,
        )
        self._stderr_thread.start()
        return proc

    def _supervise_loop(self, target_pid: int) -> None:
        failure_count = 0
        while not self._stop_event.is_set():
            started_at = time.monotonic()
            proc: subprocess.Popen | None = None
            launch_error: Exception | None = None
            try:
                proc = self._launch_once(target_pid)
                self._read_loop(proc)
            except Exception as exc:
                launch_error = exc
                log.exception("audio-capture 启动/读取失败")

            if proc is not None:
                try:
                    exit_code = proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    exit_code = proc.poll()
                stderr_thread = self._stderr_thread
                if (
                    stderr_thread is not None
                    and stderr_thread is not threading.current_thread()
                ):
                    stderr_thread.join(timeout=0.5)
            else:
                exit_code = None

            if self._stop_event.is_set():
                break

            runtime = time.monotonic() - started_at
            if runtime >= self.restart_stable_seconds:
                failure_count = 0
            failure_count += 1
            diagnostics = self.stderr_tail
            if launch_error is not None:
                diagnostics = f"{type(launch_error).__name__}: {launch_error}\n{diagnostics}".strip()

            log.warning(
                "audio-capture 意外退出 code=%s，准备第 %d 次恢复；stderr=%s",
                exit_code,
                failure_count,
                diagnostics[-2000:] if diagnostics else "<empty>",
            )
            if self.on_failure is not None:
                try:
                    self.on_failure(exit_code, diagnostics, failure_count)
                except Exception:
                    log.exception("audio-capture failure 回调失败")

            delay = min(
                self.restart_max_delay,
                self.restart_initial_delay * (2 ** max(0, failure_count - 1)),
            )
            if self._stop_event.wait(delay):
                break

            try:
                if self.on_before_restart is not None:
                    self.on_before_restart(target_pid, failure_count)
                elif self.on_pcm is not None:
                    self.on_pcm(b"")
            except Exception:
                log.exception("audio-capture restart/stream-reset 回调失败")

        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None
        log.info("audio-capture supervisor 结束 target_pid=%s", target_pid)

    def _read_loop(self, proc: subprocess.Popen | None = None) -> None:
        """Drain PCM from one helper process until EOF or intentional stop."""
        if proc is None:
            with self._proc_lock:
                proc = self._proc
        assert proc is not None and proc.stdout is not None
        while not self._stop_event.is_set():
            chunk = proc.stdout.read(_CHUNK)
            if not chunk:
                break
            if self.on_pcm is not None:
                try:
                    self.on_pcm(chunk)
                except Exception:
                    log.exception("PCM 回调失败，继续读取 helper stdout")
