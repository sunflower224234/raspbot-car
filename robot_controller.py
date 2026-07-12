# -*- coding: UTF-8 -*-
"""小车硬件控制适配器 —— Web 控制台与真实底盘之间的桥梁。

环境变量：
    RASPBOT_HARDWARE_MODE   real=真实硬件  simulated=模拟  remote=远程小车
    RASPBOT_CAR_URL         remote 模式下的小车服务地址（如 http://192.168.1.100:5001）
"""

from __future__ import annotations

import os
from typing import Optional

import requests


class RobotController:
    """硬件适配层。

    simulated → 纯模拟，方便 PC 调试 Web 界面。
    real      → 本机直连 I2C 硬件（PC 上不可用）。
    remote    → HTTP 转发到树莓派 car_server.py（✅ 当前架构）。
    """

    def __init__(self) -> None:
        self.mode = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.car_url = os.getenv("RASPBOT_CAR_URL", "").rstrip("/")
        self.last_action: Optional[str] = None
        self.speed = 40
        self.connected = False
        self.hardware_note = ""
        self._car = None

        if self.mode == "real":
            self._init_real()
        elif self.mode == "remote":
            self._init_remote()
        else:
            self.connected = True
            self.hardware_note = (
                "SIMULATED 模式：当前仅模拟小车动作。"
                "设置 RASPBOT_HARDWARE_MODE=remote 并配置 RASPBOT_CAR_URL 连接真实小车。"
            )

    # ---- 远程模式 ----
    def _init_remote(self) -> None:
        if not self.car_url:
            self.connected = False
            self.hardware_note = "REMOTE 模式未配置 RASPBOT_CAR_URL"
            return
        try:
            resp = requests.get(f"{self.car_url}/api/status", timeout=3)
            if resp.ok:
                data = resp.json()
                self.connected = data.get("car_ok", False)
                self.hardware_note = f"REMOTE 模式 → {self.car_url} | 底盘={'✓' if self.connected else '✗'}"
            else:
                self.connected = False
                self.hardware_note = f"REMOTE 模式 → {self.car_url} 无响应"
        except Exception as exc:
            self.connected = False
            self.hardware_note = f"REMOTE 模式连接失败：{exc}"

    def _remote_post(self, path: str, data: dict = None, timeout: float = 5) -> dict:
        """向小车发送 HTTP 请求。"""
        if not self.car_url:
            return {"success": False, "message": "未配置小车地址"}
        try:
            resp = requests.post(
                f"{self.car_url}{path}",
                json=data or {},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            return {"success": False, "message": f"小车通信超时：{path}"}
        except requests.exceptions.ConnectionError:
            self.connected = False
            return {"success": False, "message": f"无法连接小车：{self.car_url}"}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    def _refresh_connection(self) -> None:
        """刷新远程连接状态。"""
        if self.mode != "remote":
            return
        try:
            resp = requests.get(f"{self.car_url}/api/status", timeout=2)
            self.connected = resp.ok and resp.json().get("car_ok", False)
        except Exception:
            self.connected = False

    # ---- 真实硬件模式 ----
    def _init_real(self) -> None:
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
        if self.mode != "real":
            return None
        if self._car is None:
            self._init_real()
        return self._car

    # ---- 统一命令接口 ----
    def command(self, action: str, speed: int = 40) -> dict:
        """执行小车动作。"""
        self.speed = speed
        self.last_action = action

        # remote 模式 → HTTP 转发
        if self.mode == "remote":
            if action == "stop":
                result = self._remote_post("/api/control/stop")
            elif action == "emergency_stop":
                return self.emergency_stop()
            elif action == "line_follow":
                target = getattr(self, "_task_target", "B")
                result = self._remote_post("/api/task/line_follow",
                                           {"target": target, "speed": speed})
            else:
                result = self._remote_post("/api/control/move",
                                           {"action": action, "speed": speed})
            # 确保有 message 字段
            if "message" not in result:
                result["message"] = result.get("success") and \
                    f"小车执行：{action}" or f"小车执行失败：{action}"
            return result

        # real 模式 → 本地硬件
        car = self._get_car()
        if car is not None:
            try:
                s = max(0, min(80, int(speed)))
                mapping = {
                    "forward": (s, 0, 0), "backward": (-s, 0, 0),
                    "left": (0, -s, 0), "right": (0, s, 0),
                    "rotate_left": (0, 0, -max(18, s)),
                    "rotate_right": (0, 0, max(18, s)),
                    "stop": (0, 0, 0),
                }
                if action in mapping:
                    f, l, t = mapping[action]
                    car.Ctrl_Car(f, l, t)
                elif action in ("line_follow",):
                    pass
                else:
                    car.stop()
                return {"success": True, "action": action, "speed": speed,
                        "message": f"执行动作：{action}，速度 {speed}"}
            except Exception as exc:
                return {"success": False, "action": action, "speed": speed,
                        "message": f"硬件命令失败：{exc}"}

        # simulated 模式
        return {
            "success": True, "action": action, "speed": speed,
            "message": f"模拟小车动作：{action}，速度 {speed}",
        }

    def stop(self) -> dict:
        return self.command("stop", 0)

    def emergency_stop(self) -> dict:
        self.last_action = "emergency_stop"

        if self.mode == "remote":
            return self._remote_post("/api/control/emergency_stop", timeout=10)

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
        if self.mode == "remote":
            self._remote_post("/api/feedback/rgb", {"color": color}, timeout=2)
            return

        car = self._get_car()
        if car is None:
            return
        colors = {
            "blue": (0, 0, 120), "yellow": (120, 120, 0),
            "red": (160, 0, 0), "green": (0, 120, 0), "off": (0, 0, 0),
        }
        r, g, b = colors.get(color, (0, 0, 80))
        try:
            car.Ctrl_WQ2812_brightness_ALL(r, g, b)
        except Exception:
            pass

    def beep(self, on: bool) -> None:
        if self.mode == "remote":
            self._remote_post("/api/feedback/beep", {"on": on}, timeout=2)
            return

        car = self._get_car()
        if car is not None:
            try:
                car.Ctrl_BEEP_Switch(1 if on else 0)
            except Exception:
                pass

    def set_task_target(self, target: str) -> None:
        """设置远程循迹目标点。"""
        self._task_target = target

    def send_speak(self, text: str) -> dict:
        """让小车播报语音。"""
        if self.mode == "remote":
            return self._remote_post("/api/speak", {"text": text}, timeout=5)
        return {"success": False, "message": "仅 remote 模式支持远程播报"}

    def get_car_status(self) -> dict:
        """获取远程小车状态（remote 模式）。"""
        if self.mode != "remote" or not self.car_url:
            return {}
        try:
            resp = requests.get(f"{self.car_url}/api/status", timeout=2)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return {}
