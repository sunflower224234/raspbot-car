# -*- coding: UTF-8 -*-
"""RASPBOT-V2：人脸识别通过后自动进入循迹/避障。

流程：
1. 打开摄像头，调用百度人脸识别。
2. 人脸识别分数达到阈值后，释放摄像头。
3. 自动启动低速平滑循迹 + 超声波避障。

运行方式：
    python3 face_then_follow.py
或：
    python3 face_then_follow.py

可调环境变量：
    RASPBOT_FACE_THEN_FOLLOW_TIMEOUT   人脸识别最长等待秒数，默认 60；设为 0 表示不限制。
    BAIDU_FACE_SCORE_THRESHOLD         人脸识别通过阈值，默认沿用 face_recognition_only.py 的设置。
"""

from __future__ import annotations

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
_sys.path.insert(0, _HERE)
_sys.path.insert(0, _os.path.join(_ROOT, "web"))
_sys.path.insert(0, _os.path.join(_ROOT, "web", "services"))
_sys.path.insert(0, _os.path.join(_ROOT, "car"))

import os
import time
from typing import Optional

from face_recognition_only import run_face_recognition_only
from line_follow_obstacle import run_line_follow_obstacle
from safety_control import clear_stop_request, stop_requested


def _face_timeout_from_env() -> Optional[float]:
    raw = os.environ.get("RASPBOT_FACE_THEN_FOLLOW_TIMEOUT", "60").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 60.0
    if value <= 0:
        return None
    return value


def run_face_then_follow(timeout_seconds: Optional[float] = None) -> bool:
    """人脸识别成功后自动进入循迹/避障。

    返回：
        True  = 人脸识别通过，并已进入循迹流程；循迹结束后正常返回。
        False = 人脸识别失败、超时或启动前收到停止请求。
    """
    if timeout_seconds is None:
        timeout_seconds = _face_timeout_from_env()

    clear_stop_request()
    print("进入【人脸识别通过后自动循迹/避障】流程。")
    print("第 1 步：进行人脸识别。识别通过后会自动释放摄像头。")
    if timeout_seconds is None:
        print("人脸识别超时：不限制。")
    else:
        print(f"人脸识别超时：{timeout_seconds:.0f} 秒。")

    face_ok = run_face_recognition_only(timeout_seconds=timeout_seconds)
    if not face_ok:
        print("人脸识别未通过，已取消循迹/避障启动。")
        return False

    if stop_requested():
        print("检测到停止请求，已取消循迹/避障启动。")
        return False

    print("第 2 步：人脸识别通过。1 秒后自动进入低速平滑循迹 + 超声波避障。")
    time.sleep(1.0)
    print("第 3 步：启动循迹/避障。停止请使用网页“紧急停车”或终端 Ctrl+C。")
    run_line_follow_obstacle()
    return True


if __name__ == "__main__":
    run_face_then_follow()
