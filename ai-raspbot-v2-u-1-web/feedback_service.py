from __future__ import annotations

from car_client import get_car_client


class FeedbackService:
    def __init__(self) -> None:
        self.client = get_car_client()
        self.rgb_color = "blue"
        self.buzzer = "off"

    def _push(self, color: str, beep_on: bool) -> None:
        """remote 模式下把灯光/蜂鸣器同步到小车，失败静默。"""
        if not self.client.enabled:
            return
        self.client.post("/api/feedback/rgb", {"color": color})
        self.client.post("/api/feedback/beep", {"on": beep_on})

    def idle(self) -> dict:
        self.rgb_color = "blue"
        self.buzzer = "off"
        self._push("blue", False)
        return self.status()

    def running(self) -> dict:
        self.rgb_color = "yellow"
        self.buzzer = "off"
        self._push("yellow", False)
        return self.status()

    def alarm(self) -> dict:
        self.rgb_color = "red"
        self.buzzer = "alarm"
        self._push("red", True)
        return self.status()

    def done(self) -> dict:
        self.rgb_color = "green"
        self.buzzer = "beep"
        self._push("green", False)
        return self.status()

    def status(self) -> dict:
        return {
            "rgb_color": self.rgb_color,
            "buzzer": self.buzzer,
        }
