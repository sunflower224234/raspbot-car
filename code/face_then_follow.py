# -*- coding: UTF-8 -*-
"""人脸识别通过后自动进入循迹/避障。

流程：
1. 人脸识别（face_recognition_only）
2. 识别通过 → 自动启动循迹避障（line_follow_obstacle）

运行方式：
    python3 face_then_follow.py

环境变量：
    RASPBOT_FACE_THEN_FOLLOW_TIMEOUT 人脸识别超时秒数（默认 60，0=不限制）
    BAIDU_FACE_SCORE_THRESHOLD       人脸识别阈值
"""

from __future__ import annotations

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
    return None if value <= 0 else value


def run_face_then_follow(timeout_seconds: Optional[float] = None) -> bool:
    """人脸识别 → 循迹避障。

    Returns:
        True = 人脸识别通过，循迹结束正常返回。
        False = 人脸识别失败/超时/收到停止请求。
    """
    if timeout_seconds is None:
        timeout_seconds = _face_timeout_from_env()

    clear_stop_request()
    print("【人脸识别 → 自动循迹/避障】流程启动。")
    print("第 1 步：人脸识别...")

    face_ok = run_face_recognition_only(timeout_seconds=timeout_seconds)
    if not face_ok:
        print("人脸识别未通过，已取消循迹。")
        return False

    if stop_requested():
        print("检测到停止请求，已取消循迹。")
        return False

    print("第 2 步：人脸识别通过！1 秒后启动循迹避障。")
    time.sleep(1.0)
    print("第 3 步：启动循迹避障。停止请用 Ctrl+C。")
    run_line_follow_obstacle()
    return True


if __name__ == "__main__":
    run_face_then_follow()
