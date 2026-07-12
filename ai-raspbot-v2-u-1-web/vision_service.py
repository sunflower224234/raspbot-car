from __future__ import annotations


class VisionService:
    def __init__(self) -> None:
        self.mode = "人脸识别"
        self.result = "等待识别"

    def face_auth(self, force_fail: bool = False) -> dict:
        self.mode = "人脸识别"
        if force_fail:
            self.result = "未授权用户"
            return {"success": False, "user": None, "message": "未授权用户"}
        self.result = "authorized_user 认证成功"
        return {"success": True, "user": "authorized_user", "message": "人脸识别成功"}

    def qr_scan(self, target: str | None) -> dict:
        self.mode = "二维码识别"
        qr_value = target or "B"
        self.result = f"识别到二维码 {qr_value}"
        return {"success": True, "qr_value": qr_value, "message": f"识别到目标点 {qr_value}"}

    def gesture_start(self) -> dict:
        self.mode = "手势识别"
        self.result = "识别到张开手掌：继续"
        return {"success": True, "gesture": "open_palm", "action": "resume", "message": "识别到张开手掌，继续任务"}

    def status(self) -> dict:
        return {
            "vision_mode": self.mode,
            "vision_result": self.result,
        }
