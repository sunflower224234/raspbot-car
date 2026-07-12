from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from feedback_service import FeedbackService
from log_service import LogService
from path_planner import astar
from robot_controller import RobotController
from sensor_service import SensorService
from vision_service import VisionService
from voice_service import VoiceService


MODE_TEXT = {
    "AUTH_REQUIRED": "等待人脸认证",
    "AUTH_SUCCESS": "认证成功",
    "IDLE": "待机",
    "MANUAL": "手动控制",
    "PLANNING": "路径规划中",
    "READY": "路径规划完成，等待启动",
    "LINE_FOLLOW": "自动巡线中",
    "OBSTACLE_STOP": "遇障停车",
    "ARRIVAL_CHECK": "到达校验中",
    "DONE": "任务完成",
    "ERROR_STOP": "急停或异常停止",
    "GESTURE_MODE": "手势控制中",
    "VOICE_MODE": "语音控制中",
}


@dataclass
class SystemState:
    auth: bool = False
    user: str = "未认证"
    mode: str = "AUTH_REQUIRED"
    target: Optional[str] = None
    task_type: str = "delivery"
    path: List[str] = field(default_factory=list)
    cost: Optional[int] = None
    blocked: List[str] = field(default_factory=list)
    path_status: str = "未规划"
    task_status: str = "waiting"
    message: str = "请先完成人脸识别"


class TaskManager:
    def __init__(self) -> None:
        self.state = SystemState()
        self.robot = RobotController()
        self.sensor = SensorService()
        self.feedback = FeedbackService()
        self.vision = VisionService()
        self.voice = VoiceService()
        self.logs = LogService()
        self.logs.append("系统启动，等待人脸认证")

    def status(self) -> dict:
        running = self.state.mode == "LINE_FOLLOW"
        sensor_status = self.sensor.tick(running)
        return {
            **self.state.__dict__,
            "mode_text": MODE_TEXT.get(self.state.mode, self.state.mode),
            "car_connected": self.robot.connected,
            "hardware_mode": self.robot.mode,
            "hardware_note": self.robot.hardware_note,
            **sensor_status,
            **self.feedback.status(),
            **self.vision.status(),
            **self.voice.status(),
            "logs": self.logs.list()[-80:],
        }

    def require_auth(self) -> Optional[dict]:
        if not self.state.auth:
            return {"success": False, "message": "未认证用户不能启动小车任务"}
        return None

    def face_auth(self, force_fail: bool = False) -> dict:
        result = self.vision.face_auth(force_fail)
        if result["success"]:
            self.state.auth = True
            self.state.user = result["user"]
            self.state.mode = "IDLE"
            self.state.message = "人脸识别成功，已进入控制台"
            self.feedback.idle()
            self.logs.append("用户人脸识别成功，进入控制台", "success")
        else:
            self.state.auth = False
            self.state.user = "未认证"
            self.state.mode = "AUTH_REQUIRED"
            self.state.message = "未授权用户，禁止进入任务控制"
            self.feedback.alarm()
            self.logs.append("人脸识别失败：未授权用户，RGB 红灯报警，蜂鸣器报警", "danger")
        return {**result, "status": self.status()}

    def logout(self) -> dict:
        self.state = SystemState()
        self.feedback.idle()
        self.logs.append("用户退出登录，系统回到认证入口")
        return {"success": True, "status": self.status()}

    def manual(self, action: str, speed: int) -> dict:
        if action != "stop":
            auth_error = self.require_auth()
            if auth_error:
                return auth_error
            if self.state.mode == "LINE_FOLLOW":
                return {"success": False, "message": "自动任务执行中，普通手动控制已禁用"}
        result = self.robot.command(action, speed)
        self.state.mode = "IDLE" if action == "stop" else "MANUAL"
        self.state.message = result["message"]
        if action == "stop":
            self.feedback.idle()
            self.logs.append("手动停止小车")
        else:
            self.feedback.running()
            self.logs.append(f"手动控制：{action}，速度 {speed}")
        return {**result, "status": self.status()}

    def emergency_stop(self) -> dict:
        result = self.robot.emergency_stop()
        self.state.mode = "ERROR_STOP"
        self.state.task_status = "error_stop"
        self.state.message = "急停已触发，小车立即停止"
        self.feedback.alarm()
        self.logs.append("急停触发：任务中断，RGB 红灯报警，蜂鸣器报警", "danger")
        return {**result, "status": self.status()}

    def plan(self, target: str, blocked: Optional[List[str]] = None) -> dict:
        auth_error = self.require_auth()
        if auth_error:
            return auth_error
        self.state.mode = "PLANNING"
        self.state.target = target
        self.state.blocked = blocked or []
        result = astar("S", target, self.state.blocked)
        if result["success"]:
            self.state.path = result["path"]
            self.state.cost = result["cost"]
            self.state.path_status = "规划成功" if not self.state.blocked else "已重新规划"
            self.state.mode = "READY"
            self.state.task_status = "ready"
            self.state.message = f"A* 路径规划完成：{' → '.join(self.state.path)}"
            self.feedback.idle()
            self.logs.append(f"选择目标点：{target}")
            self.logs.append(f"A* 路径规划完成：{' → '.join(self.state.path)}，总代价 {self.state.cost}", "success")
        else:
            self.state.path = []
            self.state.cost = None
            self.state.path_status = "路径不可达"
            self.state.mode = "IDLE"
            self.state.message = result["message"]
            self.feedback.alarm()
            self.logs.append(result["message"], "danger")
        return {**result, "status": self.status()}

    def start_task(self, target: str, source: str = "web", task_type: str = "delivery") -> dict:
        auth_error = self.require_auth()
        if auth_error:
            return auth_error
        if not self.state.path or self.state.target != target:
            self.plan(target, self.state.blocked)
        if not self.state.path:
            return {"success": False, "message": "请先生成可用路径"}
        self.state.target = target
        self.state.task_type = task_type
        self.state.mode = "LINE_FOLLOW"
        self.state.task_status = "running"
        self.state.message = f"小车开始自动巡线，任务来源：{source}"
        self.feedback.running()
        self.robot.start_line_follow(target, 45)
        self.logs.append(f"小车开始自动巡线，目标点 {target}，来源 {source}", "success")
        return {"success": True, "message": self.state.message, "status": self.status()}

    def pause_task(self) -> dict:
        self.robot.pause_line_follow()
        self.state.task_status = "paused"
        self.state.mode = "READY"
        self.state.message = "任务已暂停"
        self.feedback.idle()
        self.logs.append("任务已暂停")
        return {"success": True, "status": self.status()}

    def resume_task(self) -> dict:
        auth_error = self.require_auth()
        if auth_error:
            return auth_error
        if self.state.mode == "OBSTACLE_STOP":
            self.sensor.set_obstacle(False)
            self.logs.append("障碍物移除，继续任务", "success")
        self.state.mode = "LINE_FOLLOW"
        self.state.task_status = "running"
        self.state.message = "继续任务，自动巡线中"
        self.feedback.running()
        self.robot.resume_line_follow()
        return {"success": True, "status": self.status()}

    def cancel_task(self) -> dict:
        self.robot.stop_task()
        self.state.mode = "IDLE"
        self.state.task_status = "cancelled"
        self.state.message = "任务已取消"
        self.feedback.idle()
        self.logs.append("任务已取消")
        return {"success": True, "status": self.status()}

    def simulate_obstacle(self) -> dict:
        self.sensor.set_obstacle(True)
        self.robot.stop()
        self.state.mode = "OBSTACLE_STOP"
        self.state.task_status = "obstacle_stop"
        self.state.message = "检测到障碍物，小车已安全停车"
        self.feedback.alarm()
        self.logs.append("前方距离 14 cm，检测到障碍物", "warning")
        self.logs.append("小车已停车，RGB 红灯报警，蜂鸣器报警", "danger")
        return {"success": True, "status": self.status()}

    def simulate_arrival(self) -> dict:
        self.robot.stop()
        self.state.mode = "ARRIVAL_CHECK"
        self.state.task_status = "arrival_check"
        self.state.message = "正在进行二维码到达校验"
        self.logs.append("到达目标点，开始二维码校验")
        return {"success": True, "status": self.status()}

    def qr_scan(self) -> dict:
        result = self.vision.qr_scan(self.state.target)
        if result["success"] and result["qr_value"] == self.state.target:
            self.state.mode = "DONE"
            self.state.task_status = "done"
            self.state.message = f"目标点 {self.state.target} 校验成功，任务完成"
            self.feedback.done()
            self.logs.append(f"识别到二维码 {self.state.target}，目标校验成功", "success")
            self.logs.append("任务完成", "success")
        else:
            self.state.mode = "ERROR_STOP"
            self.state.task_status = "qr_error"
            self.state.message = "目标点不一致，请人工确认"
            self.feedback.alarm()
            self.logs.append("二维码校验失败，目标点不一致", "danger")
        return {**result, "status": self.status()}

    def voice_start(self, target: str = "B") -> dict:
        auth_error = self.require_auth()
        if auth_error:
            return auth_error
        result = self.voice.start(target)
        self.state.mode = "VOICE_MODE"
        self.state.target = target
        self.logs.append(result["message"])
        plan_result = self.plan(target, self.state.blocked)
        return {**result, "plan": plan_result, "status": self.status()}

    def gesture_start(self) -> dict:
        auth_error = self.require_auth()
        if auth_error:
            return auth_error
        result = self.vision.gesture_start()
        self.state.mode = "GESTURE_MODE"
        self.state.message = result["message"]
        self.logs.append(result["message"])
        return {**result, "status": self.status()}
