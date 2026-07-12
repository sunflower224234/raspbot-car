# -*- coding: UTF-8 -*-
"""运行互斥锁：防止多个功能同时抢占摄像头、I2C、电机和循迹模块。"""

from __future__ import annotations

import os
import fcntl


class RaspbotTaskLock:
    """Linux 文件锁。同一时刻只允许一个小车功能运行。"""

    def __init__(
        self,
        task_name: str = "raspbot_task",
        lock_path: str = "/tmp/raspbot_v2_task.lock",
    ):
        self.task_name = task_name
        self.lock_path = lock_path
        self._fd = None

    def __enter__(self):
        self._fd = open(self.lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "已有另一个小车功能正在运行。请先停止之前的程序，再启动新功能；"
                "否则会同时抢占摄像头、I2C、电机和循迹模块。"
            ) from exc
        self._fd.seek(0)
        self._fd.truncate()
        self._fd.write(f"{self.task_name}\npid={os.getpid()}\n")
        self._fd.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            finally:
                self._fd.close()
                self._fd = None
        return False
