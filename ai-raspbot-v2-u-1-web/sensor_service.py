from __future__ import annotations

import random

from car_client import get_car_client


class SensorService:
    def __init__(self) -> None:
        self.client = get_car_client()
        self.distance_cm = 42
        self.obstacle = False
        self.line_bits = [0, 1, 1, 0]
        self.line_state = "CENTER"
        self.strategy = "正常行驶"

    def snapshot(self) -> dict:
        return {
            "distance_cm": self.distance_cm,
            "obstacle": self.obstacle,
            "line_bits": self.line_bits,
            "line_state": self.line_state,
            "strategy": self.strategy,
        }

    def _obstacle_threshold(self) -> float:
        return 20.0

    def set_obstacle(self, active: bool) -> dict:
        self.obstacle = active
        if active:
            self.distance_cm = 14
            self.line_bits = [0, 1, 1, 0]
            self.line_state = "CENTER"
            self.strategy = "遇障停车"
        else:
            self.distance_cm = 36
            self.line_bits = [0, 1, 1, 0]
            self.line_state = "CENTER"
            self.strategy = "正常行驶"
        return self.snapshot()

    def _tick_remote(self) -> dict:
        """从 car_server /api/status 拉真实传感器数据；失败时保留上一帧。"""
        data = self.client.get("/api/status")
        if data:
            d = data.get("distance_cm", self.distance_cm)
            self.distance_cm = d
            self.line_bits = data.get("line_bits", self.line_bits)
            self.line_state = data.get("line_state", self.line_state)
            self.obstacle = bool(d) and 0 < float(d) < self._obstacle_threshold()
            self.strategy = "遇障停车" if self.obstacle else "正常行驶"
        return self.snapshot()

    def tick(self, running: bool) -> dict:
        # remote：无论是否在跑任务，都回传小车真实读数
        if self.client.enabled:
            return self._tick_remote()

        # simulated 回退
        if running and not self.obstacle:
            self.distance_cm = random.choice([28, 31, 34, 39, 43])
            self.line_bits = random.choice([[0, 1, 1, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
            self.line_state = {
                (0, 1, 1, 0): "CENTER",
                (0, 1, 0, 0): "LEFT偏移",
                (0, 0, 1, 0): "RIGHT偏移",
            }[tuple(self.line_bits)]
            self.strategy = "正常行驶"
        return self.snapshot()
