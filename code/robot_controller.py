# -*- coding: UTF-8 -*-
"""小车硬件控制适配器 —— Web 控制台与真实底盘之间的桥梁。

环境变量：
    RASPBOT_HARDWARE_MODE   real=真实硬件  simulated=模拟（默认 simulated）
"""

from __future__ import annotations

import os
from typing import Optional


class RobotController:
    """硬件适配层。

    默认 SIMULATED 模式方便在普通电脑上调试 Web 界面。
    切换到 REAL 模式后，所有命令通过 raspbot_v2_lib 发到底盘。
    """

    def __init__(self) -> None:
        self.mode = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.last_action: Optional[str] = None
        self.speed = 40
        self.connected = False
        self.hardware_note = ""
        self._car = None  # Raspbot 实例（real 模式时持有）

        if self.mode == "real":
            self._init_real()
        else:
            self.connected = True
            self.hardware_note = (
                "SIMULATED 模式：当前仅模拟小车动作。"
                "设置 RASPBOT_HARDWARE_MODE=real 切换真实控制。"
            )

    def _init_real(self) -> None:
        """初始化真实硬件连接。"""
        try:
            from raspbot_v2_lib import Raspbot
            self._car = Raspbot()
            self._car.require_chassis()
            self.connected = True
            self.hardware_note = f"REAL 模式：{self._car.backend_name()}"
        except Exception as exc:
            self.connected = False
            self.hardware_note = f"REAL 模式硬件初始化失败：{exc}"

    def _get_car(self):
        """延迟初始化：real 模式下首次调用命令时才连接。"""
        if self.mode != "real":
            return None
        if self._car is None:
            self._init_real()
        return self._car

    # ---- 方向映射 ----
    def command(self, action: str, speed: int = 40) -> dict:
        """执行小车动作。"""
        self.speed = speed
        self.last_action = action
        car = self._get_car()

        if car is None:
            return {
                "success": True,
                "action": action,
                "speed": speed,
                "message": f"模拟小车动作：{action}，速度 {speed}",
            }

        try:
            s = max(0, min(80, int(speed)))
            mapping = {
                "forward":       (s, 0, 0),
                "backward":      (-s, 0, 0),
                "left":          (0, -s, 0),
                "right":         (0, s, 0),
                "rotate_left":   (0, 0, -max(18, s)),
                "rotate_right":  (0, 0, max(18, s)),
                "stop":          (0, 0, 0),
            }
            if action in mapping:
                f, l, t = mapping[action]
                car.Ctrl_Car(f, l, t)
            elif action in ("line_follow",):
                # 循迹由上层 task_manager 驱动，这里只设置状态
                pass
            else:
                car.stop()
            return {"success": True, "action": action, "speed": speed,
                    "message": f"执行动作：{action}，速度 {speed}"}
        except Exception as exc:
            return {"success": False, "action": action, "speed": speed,
                    "message": f"硬件命令失败：{exc}"}

    def stop(self) -> dict:
        return self.command("stop", 0)

    def emergency_stop(self) -> dict:
        """紧急停车：连续多次写 0 + 蜂鸣报警。"""
        self.last_action = "emergency_stop"
        car = self._get_car()
        if car is not None:
            try:
                car.emergency_stop(repeats=10, interval=0.03)
                car.Ctrl_BEEP_Switch(1)
                car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
                return {"success": True, "action": "emergency_stop",
                        "message": "急停已执行：电机停止 + 红灯 + 蜂鸣"}
            except Exception as exc:
                return {"success": False, "action": "emergency_stop",
                        "message": f"急停失败：{exc}"}
        return {"success": True, "action": "emergency_stop", "speed": 0,
                "message": "模拟急停：电机停止，任务进入 ERROR_STOP"}

    def set_rgb(self, color: str) -> None:
        """设置 RGB 灯颜色。"""
        car = self._get_car()
        if car is None:
            return
        colors = {
            "blue":   (0, 0, 120),
            "yellow": (120, 120, 0),
            "red":    (160, 0, 0),
            "green":  (0, 120, 0),
            "off":    (0, 0, 0),
        }
        r, g, b = colors.get(color, (0, 0, 80))
        try:
            car.Ctrl_WQ2812_brightness_ALL(r, g, b)
        except Exception:
            pass

    def beep(self, on: bool) -> None:
        """蜂鸣器开关。"""
        car = self._get_car()
        if car is not None:
            try:
                car.Ctrl_BEEP_Switch(1 if on else 0)
            except Exception:
                pass
