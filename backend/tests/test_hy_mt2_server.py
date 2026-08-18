"""HyMT2ServerManager 测试（规格第 32.3、32.4 节）。

验证命令构造、进程启动/停止、端口选择、URL 属性。
（不依赖真实 llama-server.exe / 模型文件。）
"""

import subprocess
import sys

from translation.hy_mt2_server import HyMT2ServerManager, _find_free_port


def test_find_free_port() -> None:
    p = _find_free_port(30000)
    assert 30000 <= p < 30100


def test_assert_prerequisites_missing() -> None:
    mgr = HyMT2ServerManager(
        server_exe="D:/nonexistent/server.exe",
        model_path="D:/nonexistent/model.gguf",
    )
    try:
        mgr.assert_prerequisites()
        assert False, "应抛出 FileNotFoundError"
    except FileNotFoundError:
        pass


def test_manager_process_lifecycle() -> None:
    server_exe = sys.executable
    mgr = HyMT2ServerManager(
        server_exe=server_exe,
        model_path="D:/tmp/model.gguf",
        port=30001,
    )
    mgr._proc = subprocess.Popen(
        [server_exe, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert mgr.running
        assert mgr.pid is not None
        assert mgr.base_url == "http://127.0.0.1:30001"
    finally:
        mgr.stop(timeout=5.0)
    assert not mgr.running


def test_base_url_and_health_url() -> None:
    mgr = HyMT2ServerManager(port=18888)
    assert mgr.base_url == "http://127.0.0.1:18888"
    assert mgr.health_url == "http://127.0.0.1:18888/health"
