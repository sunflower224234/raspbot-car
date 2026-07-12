# -*- coding: UTF-8 -*-
"""传感器服务 —— 读取真实超声波距离和循迹传感器状态。

环境变量：
    RASPBOT_HARDWARE_MODE   real/simulated
"""

from __future__ import annotations

import os
import random


class SensorService:
    """传感器数据提供者。

    real 模式：通过 raspbot_v2_lib 读取真实传感器。
    simulated 模式：返回随机模拟数据，便于调试 Web 界面。
    """

    def __init__(self) -> None:
        self.mode = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.distance_cm = 42
        self.obstacle = False
        self.line_bits = [0, 1, 1, 0]   # [L1, L2, R1, R2], 0=黑线, 1=白底
        self.line_state = "CENTER"
        self.strategy = "正常行驶"
        self._car = None

        if self.mode == "real":
            self._init_real()

    def _init_real(self) -> None:
        try:
            from raspbot_v2_lib import Raspbot
            self._car = Raspbot()
            self._car.Ctrl_IR_Switch(1)
            self._car.Ctrl_Ulatist_Switch(1)
        except Exception:
            self._car = None

    def _read_real(self) -> None:
        if self._car is None:
            return
        try:
            # 超声波距离
            d = self._car.read_ultrasonic_cm()
            self.distance_cm = round(d, 1) if d > 0 else 0
            self.obstacle = 0 < d <= 20  # <=20cm 视为障碍

            # 循迹传感器（False=黑线，True=白底 → 转为 0/1）
            line = self._car.read_line_sensors()
            self.line_bits = [
                0 if line["left_1"] is False else 1,
                0 if line["left_2"] is False else 1,
                0 if line["right_1"] is False else 1,
                0 if line["right_2"] is False else 1,
            ]
            bits = tuple(self.line_bits)
            if bits == (0, 0, 0, 0):
                self.line_state = "CROSS"
            elif bits == (1, 1, 1, 1):
                self.line_state = "LOST"
            elif bits in ((0, 1, 1, 0), (0, 0, 1, 0), (0, 0, 0, 0)):
                self.line_state = "CENTER"
            elif bits[0] == 0 or bits[1] == 0:
                self.line_state = "LEFT偏移"
            elif bits[2] == 0 or bits[3] == 0:
                self.line_state = "RIGHT偏移"
            else:
                self.line_state = "LOST"

            self.strategy = "遇障停车" if self.obstacle else "正常行驶"
        except Exception:
            pass

    def snapshot(self) -> dict:
        return {
            "distance_cm": self.distance_cm,
            "obstacle": self.obstacle,
            "line_bits": self.line_bits,
            "line_state": self.line_state,
            "strategy": self.strategy,
        }

    def set_obstacle(self, active: bool) -> dict:
        """手动设置障碍（模拟模式用）。"""
        self.obstacle = active
        if active:
            self.distance_cm = 14
            self.strategy = "遇障停车"
        else:
            self.distance_cm = 36
            self.strategy = "正常行驶"
        return self.snapshot()

    def tick(self, running: bool) -> dict:
        """每轮状态更新。running=True 时刷新传感器。"""
        if self.mode == "real":
            self._read_real()
        elif running and not self.obstacle:
            self.distance_cm = random.choice([28, 31, 34, 39, 43])
            self.line_bits = random.choice([
                [0, 1, 1, 0], [0, 1, 0, 0], [0, 0, 1, 0],
            ])
            state_map = {
                (0, 1, 1, 0): "CENTER",
                (0, 1, 0, 0): "LEFT偏移",
                (0, 0, 1, 0): "RIGHT偏移",
            }
            self.line_state = state_map.get(tuple(self.line_bits), "CENTER")
            self.strategy = "正常行驶"
        return self.snapshot()
