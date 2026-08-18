"""VALORANT 进程发现（规格文档第 19 节）。

目标进程：VALORANT-Win64-Shipping.exe
使用 psutil，每 1 秒轮询；记录 pid + create_time，检测重启。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import psutil

log = logging.getLogger(__name__)

TARGET_PROCESS = "VALORANT-Win64-Shipping.exe"
POLL_INTERVAL = 1.0


@dataclass
class ProcessInfo:
    pid: int
    create_time: float


class ProcessFinder(threading.Thread):
    """后台线程轮询 VALORANT 进程，状态变化时回调。

    回调签名：on_change(info: ProcessInfo | None)
    - info 非 None：进程在线（含 pid / create_time）
    - info None：进程退出
    """

    def __init__(
        self,
        on_change: Optional[Callable[[Optional[ProcessInfo]], None]] = None,
        *,
        target: str = TARGET_PROCESS,
        interval: float = POLL_INTERVAL,
        finder: Callable[[str], Optional[ProcessInfo]] | None = None,
    ) -> None:
        super().__init__(name="ProcessFinder", daemon=True)
        self._on_change = on_change
        self._target = target
        self._interval = interval
        self._finder = finder or self._psutil_find
        self._stop_event = threading.Event()
        self._current: Optional[ProcessInfo] = None

    @property
    def current(self) -> Optional[ProcessInfo]:
        return self._current

    @property
    def running(self) -> bool:
        return self._current is not None

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self.join(timeout=timeout)

    def run(self) -> None:
        while not self._stop_event.is_set():
            info = self._find()
            if _changed(self._current, info):
                self._current = info
                if info is None:
                    log.info("VALORANT 进程已退出")
                else:
                    log.info(
                        "发现 VALORANT 进程: pid=%d create_time=%.3f",
                        info.pid,
                        info.create_time,
                    )
                if self._on_change is not None:
                    try:
                        self._on_change(info)
                    except Exception:
                        log.exception("ProcessFinder 回调异常")
            self._stop_event.wait(self._interval)

    def find_now(self) -> Optional[ProcessInfo]:
        """同步查找一次（供测试/手动调用）。"""
        info = self._find()
        self._current = info
        return info

    # ---- 内部 ----

    def _find(self) -> Optional[ProcessInfo]:
        return self._finder(self._target)

    @staticmethod
    def _psutil_find(target: str) -> Optional[ProcessInfo]:
        for proc in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                if proc.info["name"] == target:
                    return ProcessInfo(pid=proc.info["pid"], create_time=proc.info["create_time"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None


def _changed(a: Optional[ProcessInfo], b: Optional[ProcessInfo]) -> bool:
    """进程状态是否发生变化（上线 / 退出 / 重启）。"""
    if a is None or b is None:
        return a is not b
    # 都用 create_time 鉴定「同一进程实例」，pid 或 create_time 不同视为重启
    return a.pid != b.pid or a.create_time != b.create_time
