from __future__ import annotations

import os
from typing import Optional

from car_client import get_car_client


# 手动方向 -> car_server /api/control/move 的 action
_MOVE_ACTIONS = {
    "forward", "backward", "left", "right",
    "rotate_left", "rotate_right", "stop",
}


class RobotController:
    """底盘适配层。

    - simulated：PC 纯模拟（默认），普通笔记本即可运行控制台。
    - remote：通过 car_client 把命令 HTTP 转发到树莓派上的 car_server.py。
    - real：预留本机直连官方驱动（此处仍为占位）。
    """

    def __init__(self) -> None:
        self.client = get_car_client()
        self.mode = self.client.mode
        self.last_action: Optional[str] = None
        self.speed = 40
        self._notes = {
            "simulated": "SIMULATED 模式：仅在 PC 上模拟小车动作，未连接真实硬件。",
            "remote": f"REMOTE 模式：命令转发到小车 {self.client.base_url or '(未配置)'}。",
            "real": "REAL 模式：预留本机直连官方驱动，尚未绑定。",
        }

    # ---- 状态 ----
    @property
    def connected(self) -> bool:
        if self.mode == "remote":
            return self.client.online
        return True

    @property
    def hardware_note(self) -> str:
        note = self._notes.get(self.mode, self._notes["simulated"])
        if self.mode == "remote" and not self.client.online:
            note += f"（当前离线；{self.client.last_error or '无'}）"
        return note

    def _sim(self, action: str, speed: int, message: str) -> dict:
        return {"success": True, "action": action, "speed": speed, "message": message}

    # ---- 手动控制 / 停车 ----
    def command(self, action: str, speed: int = 40) -> dict:
        self.speed = speed
        self.last_action = action

        if self.mode == "remote" and self.client.enabled:
            if action == "stop":
                data = self.client.post("/api/control/stop")
            elif action in _MOVE_ACTIONS:
                data = self.client.post("/api/control/move", {"action": action, "speed": speed})
            else:
                # 未知动作走停车，避免误动
                data = self.client.post("/api/control/stop")
            if data is not None:
                msg = data.get("message") or f"小车执行：{action}"
                return {**data, "success": data.get("success", True), "action": action, "speed": speed, "message": msg}
            return {"success": False, "action": action, "speed": speed,
                    "message": f"小车离线，命令未送达：{action}"}

        if self.mode == "real":
            return self._call_real_driver(action, speed)

        return self._sim(action, speed, f"模拟小车动作：{action}，速度 {speed}")

    def stop(self) -> dict:
        return self.command("stop", 0)

    def emergency_stop(self) -> dict:
        self.last_action = "emergency_stop"
        if self.mode == "remote" and self.client.enabled:
            data = self.client.post("/api/control/emergency_stop")
            if data is not None:
                return {**data, "action": "emergency_stop", "speed": 0,
                        "message": data.get("message", "急停已下发")}
            return {"success": False, "action": "emergency_stop", "speed": 0,
                    "message": "小车离线，急停未送达"}
        if self.mode == "real":
            return self._call_real_driver("emergency_stop", 0)
        return self._sim("emergency_stop", 0, "模拟急停：电机停止，任务进入 ERROR_STOP")

    # ---- 循迹任务生命周期（remote 转发到 car_server）----
    def start_line_follow(self, target: str = "B", speed: int = 45) -> dict:
        self.last_action = "line_follow"
        if self.mode == "remote" and self.client.enabled:
            data = self.client.post("/api/task/line_follow", {"target": target, "speed": speed})
            if data is not None:
                return data
            return {"success": False, "message": "小车离线，循迹未启动"}
        return self._sim("line_follow", speed, f"模拟循迹启动 → {target}")

    def pause_line_follow(self) -> dict:
        if self.mode == "remote" and self.client.enabled:
            data = self.client.post("/api/task/pause")
            if data is not None:
                return data
            return {"success": False, "message": "小车离线，暂停未送达"}
        return self._sim("pause", 0, "模拟循迹暂停")

    def resume_line_follow(self) -> dict:
        if self.mode == "remote" and self.client.enabled:
            data = self.client.post("/api/task/resume")
            if data is not None:
                return data
            return {"success": False, "message": "小车离线，继续未送达"}
        return self._sim("resume", 0, "模拟循迹继续")

    def stop_task(self) -> dict:
        if self.mode == "remote" and self.client.enabled:
            data = self.client.post("/api/task/stop")
            if data is not None:
                return data
            return {"success": False, "message": "小车离线，停止未送达"}
        return self._sim("task_stop", 0, "模拟任务停止")

    def _call_real_driver(self, action: str, speed: int) -> dict:
        return {
            "success": False,
            "action": action,
            "speed": speed,
            "message": "REAL 模式尚未绑定官方驱动，请接入 Yahboom Python 控制类后再启用。",
        }
