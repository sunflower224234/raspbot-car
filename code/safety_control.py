# -*- coding: UTF-8 -*-
"""RASPBOT-V2 运行安全控制。

用途：
1. Ctrl+C / kill 终止时尽量立即停车。
2. 提供 /tmp/raspbot_v2_stop.flag 软停止开关，便于远程终端暂停。
3. 提供 emergency_stop_once() 给网页紧急停车调用。
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Optional

STOP_FLAG_PATH = os.environ.get(
    "RASPBOT_STOP_FLAG", "/tmp/raspbot_v2_stop.flag"
)
_stop_event = threading.Event()
_current_car = None


def clear_stop_request() -> None:
    """清除停止标志。启动新任务前调用。"""
    _stop_event.clear()
    try:
        os.remove(STOP_FLAG_PATH)
    except FileNotFoundError:
        pass


def request_stop() -> None:
    """发出停止请求。会写入标志文件并尝试立即停车。"""
    _stop_event.set()
    try:
        with open(STOP_FLAG_PATH, "w", encoding="utf-8") as fp:
            fp.write("stop\n")
    except Exception:
        pass
    car = _current_car
    if car is not None:
        try:
            car.emergency_stop(repeats=8, interval=0.02)
        except Exception:
            pass


def stop_requested() -> bool:
    """检查是否已收到停止请求。功能脚本循环中反复调用。"""
    return _stop_event.is_set() or os.path.exists(STOP_FLAG_PATH)


def bind_car(car) -> None:
    """绑定当前使用的小车对象，供外部急停调用。"""
    global _current_car
    _current_car = car


def unbind_car(car=None) -> None:
    """解绑小车对象。"""
    global _current_car
    if car is None or _current_car is car:
        _current_car = None


def _signal_handler(signum, frame):  # noqa: ARG001
    request_stop()
    raise KeyboardInterrupt(f"收到停止信号：{signum}")


def install_signal_handlers() -> None:
    """注册 SIGINT/SIGTERM 处理，确保 Ctrl+C 时优先停车。"""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except Exception:
            pass


def emergency_stop_once() -> None:
    """独立创建小车对象并发送急停命令（供后端紧急停车接口调用）。"""
    from raspbot_v2_lib import Raspbot

    car = Raspbot()
    try:
        car.emergency_stop(repeats=10, interval=0.03)
        print("已发送紧急停车命令。")
    finally:
        car.close()


if __name__ == "__main__":
    request_stop()
    emergency_stop_once()
