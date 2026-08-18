"""AudioCaptureReader 测试（规格第 20、21 节）。

验证：
- assert_available 缺失时报错
- 默认 exe 路径指向已编译产物
- 读循环能从子进程 stdout 读到 PCM 字节并经 on_pcm 回调
"""

import subprocess
import sys
import threading
from pathlib import Path

from audio.capture_reader import AudioCaptureReader


def test_assert_available_missing(tmp_path):
    reader = AudioCaptureReader(exe_path=tmp_path / "nonexistent.exe")
    try:
        reader.assert_available()
        assert False, "应抛 FileNotFoundError"
    except FileNotFoundError:
        pass


def test_default_exe_path_points_to_built_binary():
    reader = AudioCaptureReader()
    p = Path(reader.exe_path)
    assert p.exists(), f"默认 native helper 不存在: {p}"


def test_read_loop_receives_subprocess_stdout():
    """端到端：用真实子进程输出 100 字节，验证 on_pcm 累计收到 100 字节。"""
    reader = AudioCaptureReader()
    received = []
    reader.on_pcm = received.append

    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; sys.stdout.buffer.write(b'A'*100); sys.stdout.buffer.flush()"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    reader._proc = proc
    reader._stop_event = threading.Event()  # 不 set，读到一个 EOF 即退出

    reader._read_loop()  # 读到 EOF（子进程退出）后返回
    proc.wait()

    total = sum(len(c) for c in received)
    assert total == 100


def test_stop_terminates_proc():
    """stop() 能终止一个仍在运行的子进程。"""
    reader = AudioCaptureReader()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    reader._proc = proc
    reader._stop_event = threading.Event()
    reader._reader_thread = threading.Thread(target=lambda: None)
    reader._reader_thread.start()

    reader.stop()
    assert proc.poll() is not None  # 已终止
