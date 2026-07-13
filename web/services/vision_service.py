# -*- coding: UTF-8 -*-
"""视觉服务 —— 人脸识别 + 二维码扫描 + 手势识别。

real 模式对接真实摄像头和识别模块。
simulated 模式返回模拟结果。
"""

from __future__ import annotations

import os
from typing import Optional


class VisionService:
    def __init__(self) -> None:
        self.mode_name = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.vision_mode = "人脸识别"
        self.result = "等待识别"

    def face_auth(self, force_fail: bool = False) -> dict:
        """人脸识别认证。"""
        self.vision_mode = "人脸识别"

        if force_fail:
            self.result = "未授权用户"
            return {"success": False, "user": None, "message": "未授权用户"}

        if self.mode_name == "real":
            try:
                from face_recognition_only import run_face_recognition_only
                ok = run_face_recognition_only(timeout_seconds=15)
                if ok:
                    self.result = "authorized_user 认证成功"
                    return {"success": True, "user": "authorized_user",
                            "message": "人脸识别成功"}
                else:
                    self.result = "未授权用户"
                    return {"success": False, "user": None,
                            "message": "人脸识别失败：未匹配到授权用户"}
            except Exception as exc:
                self.result = f"识别异常：{exc}"
                return {"success": False, "user": None,
                        "message": f"人脸识别模块异常：{exc}"}

        # simulated
        self.result = "authorized_user 认证成功"
        return {"success": True, "user": "authorized_user",
                "message": "人脸识别成功（模拟）"}

    def qr_scan(self, target: Optional[str] = None) -> dict:
        """二维码扫描 —— 已迁移至车载摄像头（robot_controller.qr_scan_car）。
        此方法保留作为 simulated 模式的兜底。"""
        self.vision_mode = "二维码识别"
        # simulated 兜底
        qr_value = target or "B"
        self.result = f"识别到二维码 {qr_value}"
        return {"success": True, "qr_value": qr_value,
                "message": f"识别到目标点 {qr_value}（模拟）"}

    def gesture_start(self) -> dict:
        """手势识别（预留接口）。"""
        self.vision_mode = "手势识别"
        self.result = "识别到张开手掌：继续"
        return {"success": True, "gesture": "open_palm", "action": "resume",
                "message": "识别到张开手掌，继续任务"}

    def status(self) -> dict:
        return {"vision_mode": self.vision_mode, "vision_result": self.result}
