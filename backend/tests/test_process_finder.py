"""ProcessFinder 测试（规格第 19 节）。"""

import time

from process.process_finder import ProcessFinder, ProcessInfo, _changed


def test_changed_detection() -> None:
    a = ProcessInfo(pid=1, create_time=100.0)
    b = ProcessInfo(pid=1, create_time=100.0)
    assert _changed(a, b) is False  # 同一实例
    assert _changed(a, ProcessInfo(pid=2, create_time=100.0)) is True  # pid 不同
    assert _changed(a, ProcessInfo(pid=1, create_time=200.0)) is True  # create_time 不同
    assert _changed(a, None) is True  # 退出
    assert _changed(None, a) is True  # 上线
    assert _changed(None, None) is False


def test_finder_calls_callback_on_change() -> None:
    events = []
    fake_proc = ProcessInfo(pid=12345, create_time=111.0)

    def fake_find(target):
        return fake_proc if target == "VALORANT-Win64-Shipping.exe" else None

    finder = ProcessFinder(
        on_change=lambda info: events.append(info),
        finder=fake_find,
        interval=0.05,
    )
    finder.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not events:
            time.sleep(0.02)
        assert events and events[0] == fake_proc
        assert finder.running
        assert finder.current == fake_proc
    finally:
        finder.stop()


def test_finder_reports_exit() -> None:
    events = []
    proc = ProcessInfo(pid=9, create_time=1.0)

    def fake_find(target):
        return proc if proc is not None else None

    # 用一个可变容器模拟进程上线后退出
    state = {"alive": True}

    def finder_fn(target):
        return proc if state["alive"] else None

    finder = ProcessFinder(on_change=lambda i: events.append(i), finder=finder_fn, interval=0.02)
    finder.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and not events:
            time.sleep(0.02)
        assert any(e is not None for e in events)  # 至少上线一次

        state["alive"] = False
        deadline = time.time() + 2.0
        while time.time() < deadline and events[-1] is not None:
            time.sleep(0.02)
        assert events[-1] is None  # 退出通知
    finally:
        finder.stop()
