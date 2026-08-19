"""AudioCaptureReader unit tests, including helper crash recovery."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from audio.capture_reader import AudioCaptureReader


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_assert_available_missing(tmp_path):
    reader = AudioCaptureReader(exe_path=tmp_path / "nonexistent.exe")
    with pytest.raises(FileNotFoundError):
        reader.assert_available()


@pytest.mark.windows_integration
def test_default_exe_path_points_to_built_binary():
    """Release/Windows integration check; CI unit jobs do not require the artifact."""
    reader = AudioCaptureReader()
    p = Path(reader.exe_path)
    assert p.exists(), f"默认 native helper 不存在: {p}"


def test_read_loop_receives_subprocess_stdout():
    reader = AudioCaptureReader()
    received = []
    reader.on_pcm = received.append

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'A'*100); sys.stdout.buffer.flush()",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reader._proc = proc
    reader._stop_event = threading.Event()

    reader._read_loop()
    proc.wait()
    assert sum(len(c) for c in received) == 100


def test_stop_terminates_proc_without_restart():
    reader = AudioCaptureReader()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reader._proc = proc
    reader._stop_event = threading.Event()
    reader._reader_thread = threading.Thread(target=lambda: None)
    reader._reader_thread.start()

    reader.stop()
    assert proc.poll() is not None


class _RestartingReader(AudioCaptureReader):
    """First helper crashes; second helper emits PCM and stays alive."""

    def __init__(self, **kwargs):
        super().__init__(exe_path=sys.executable, **kwargs)
        self.launches = 0

    def _popen(self, cmd):
        self.launches += 1
        if self.launches == 1:
            code = (
                "import sys; "
                "sys.stderr.write('simulated WASAPI failure\\n'); sys.stderr.flush(); "
                "raise SystemExit(23)"
            )
        else:
            code = (
                "import sys,time; "
                "sys.stdout.buffer.write(b'P'*128); sys.stdout.buffer.flush(); "
                "time.sleep(30)"
            )
        return subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )


def test_unexpected_exit_restarts_and_preserves_stderr_diagnostics():
    received: list[bytes] = []
    failures: list[tuple[int | None, str, int]] = []
    reader = _RestartingReader(
        on_pcm=received.append,
        on_failure=lambda code, stderr, attempt: failures.append(
            (code, stderr, attempt)
        ),
        restart_initial_delay=0.05,
        restart_max_delay=0.05,
        restart_stable_seconds=60.0,
    )

    reader.start(4242)
    try:
        assert _wait_until(lambda: reader.launches >= 2)
        assert _wait_until(lambda: any(chunk == b"P" * 128 for chunk in received))
        assert failures
        assert failures[0][0] == 23
        assert "simulated WASAPI failure" in failures[0][1]
        assert failures[0][2] == 1
        # b"" is the explicit stream boundary passed before the replacement
        # helper starts, allowing AudioPipeline to reset all streaming state.
        assert b"" in received
    finally:
        reader.stop()

    launches_after_stop = reader.launches
    time.sleep(0.1)
    assert reader.launches == launches_after_stop


def test_pcm_callback_exception_does_not_kill_stdout_reader():
    calls = 0

    def flaky_callback(chunk: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("consumer failure")

    reader = AudioCaptureReader(on_pcm=flaky_callback)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; "
            "sys.stdout.buffer.write(b'A'*10); sys.stdout.buffer.flush(); "
            "time.sleep(0.05); "
            "sys.stdout.buffer.write(b'B'*10); sys.stdout.buffer.flush()",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    reader._proc = proc
    reader._stop_event.clear()
    reader._read_loop(proc)
    proc.wait()
    assert calls >= 2


class _IdleSupervisorReader(AudioCaptureReader):
    """Test double that makes each supervisor wait only on its own stop event."""

    def __init__(self, **kwargs):
        super().__init__(exe_path=sys.executable, **kwargs)

    def _supervise_loop(self, target_pid, stop_event):  # noqa: ARG002
        stop_event.wait()


def test_new_start_never_clears_old_generation_stop_event():
    reader = _IdleSupervisorReader(supervisor_join_timeout=0.01)
    old_stop = threading.Event()
    old_gate = threading.Event()
    old_observed: list[bool] = []

    def blocked_old_generation() -> None:
        old_gate.wait()
        old_observed.append(old_stop.is_set())

    old_thread = threading.Thread(target=blocked_old_generation, daemon=True)
    reader._stop_event = old_stop
    reader._reader_thread = old_thread
    old_thread.start()

    # start() must cancel the old generation, tolerate its join timeout, and
    # create a brand-new event for PID B instead of clearing old_stop.
    reader.start(2222)
    try:
        assert old_stop.is_set()
        assert reader._stop_event is not old_stop
        assert old_thread.is_alive()

        old_gate.set()
        old_thread.join(timeout=1.0)
        assert old_observed == [True]
    finally:
        reader.stop()
