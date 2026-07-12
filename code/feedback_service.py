# -*- coding: UTF-8 -*-
"""反馈服务 —— RGB 灯 + 蜂鸣器控制（对接真实硬件）。"""

from __future__ import annotations

import os


class FeedbackService:
    def __init__(self) -> None:
        self.mode_name = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.rgb_color = "blue"
        self.buzzer = "off"
        self._car = None

    def _get_car(self):
        """延迟初始化真实硬件。"""
        if self.mode_name != "real":
            return None
        if self._car is None:
            try:
                from raspbot_v2_lib import Raspbot
                self._car = Raspbot()
            except Exception:
                pass
        return self._car

    def _set_hw(self, rgb: str, buzzer_on: bool) -> None:
        car = self._get_car()
        if car is None:
            return
        colors = {
            "blue": (0, 0, 120), "yellow": (120, 120, 0),
            "red": (160, 0, 0), "green": (0, 120, 0),
            "off": (0, 0, 0),
        }
        r, g, b = colors.get(rgb, (0, 0, 80))
        try:
            car.Ctrl_WQ2812_brightness_ALL(r, g, b)
            car.Ctrl_BEEP_Switch(1 if buzzer_on else 0)
        except Exception:
            pass

    def idle(self) -> dict:
        self.rgb_color = "blue"
        self.buzzer = "off"
        self._set_hw("blue", False)
        return self.status()

    def running(self) -> dict:
        self.rgb_color = "yellow"
        self.buzzer = "off"
        self._set_hw("yellow", False)
        return self.status()

    def alarm(self) -> dict:
        self.rgb_color = "red"
        self.buzzer = "alarm"
        self._set_hw("red", True)
        return self.status()

    def done(self) -> dict:
        self.rgb_color = "green"
        self.buzzer = "beep"
        self._set_hw("green", True)
        return self.status()

    def status(self) -> dict:
        return {"rgb_color": self.rgb_color, "buzzer": self.buzzer}
