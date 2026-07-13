# -*- coding: UTF-8 -*-
"""循迹 + 绕障 V2 —— 基于第16组参考算法的状态机循迹 + S型半圆绕障。

适配 RASPBOT-V2 I2C 底盘（Ctrl_Car(forward, lateral, turn)）。

核心改进：
    1. 状态机循迹：四路传感器直接映射动作，无 PID，响应更快
    2. S型半圆绕障：舵机扫描三向 → 半圆弧绕过障碍物 → 自动回线
    3. 十字路口检测：中间两路+任意外侧同时踩线 = 路口
"""

from __future__ import annotations

import time
import threading


def _read_sensors(car):
    """读取四路循迹传感器。返回 (L1, L2, R1, R2)，True=检测到黑线。"""
    raw = car.read_line_sensors()
    return (
        not raw.get("left_1", True),
        not raw.get("left_2", True),
        not raw.get("right_1", True),
        not raw.get("right_2", True),
    )


def _read_distance_cm(car, samples: int = 3) -> float:
    """超声波测距（厘米），多次采样取中位数。"""
    vals = []
    for _ in range(samples):
        try:
            mm = car.read_ultrasonic_mm()
            cm = mm / 10.0 if mm > 0 else 0.0
        except Exception:
            cm = 0.0
        if cm >= 1.0:
            vals.append(cm)
        time.sleep(0.005)
    if not vals:
        return 0.0
    vals.sort()
    return vals[len(vals) // 2]


# ======================== 状态机循迹 ========================

class LineFollower:
    """状态机循迹器 —— 基于四路传感器直接映射动作。

    使用方式：
        follower = LineFollower(car)
        follower.init()
        while running:
            follower.step(speed=30)
    """

    def __init__(self, car):
        self.car = car
        self.last_action = "stop"

    def init(self):
        """初始化传感器。"""
        self.car.Ctrl_IR_Switch(1)
        self.car.Ctrl_Ulatist_Switch(1)
        time.sleep(0.15)
        self.car.Ctrl_Servo(1, 90)

    def step(self, speed: int = 30) -> str:
        """执行一步循迹。返回当前动作名称。"""
        L1, L2, R1, R2 = _read_sensors(self.car)
        s = max(15, min(60, speed))

        # ---- 参考算法状态机 ----

        # (1) 左任意黑 + 右2白 → 右急转（右锐角/右直角）
        if (L1 or L2) and not R2:
            self.car.Ctrl_Car(0, 0, s)       # 原地右转
            self.last_action = "spin_right"
            return "spin_right"

        # (2) 左1黑 + (右1黑 or 右2黑) → 左急转（左锐角/左直角）
        if L1 and (R1 or R2):
            self.car.Ctrl_Car(0, 0, -s)      # 原地左转
            self.last_action = "spin_left"
            return "spin_left"

        # (3) 仅最左检测到 → 左转
        if L1:
            self.car.Ctrl_Car(0, 0, -max(18, s * 3 // 4))
            self.last_action = "left"
            return "left"

        # (4) 仅最右检测到 → 右转
        if R2:
            self.car.Ctrl_Car(0, 0, max(18, s * 3 // 4))
            self.last_action = "right"
            return "right"

        # (5) 左2黑 + 右1白 → 左小弯
        if L2 and not R1:
            self.car.Ctrl_Car(s, 0, -max(12, s // 3))
            self.last_action = "curve_left"
            return "curve_left"

        # (6) 左2白 + 右1黑 → 右小弯
        if not L2 and R1:
            self.car.Ctrl_Car(s, 0, max(12, s // 3))
            self.last_action = "curve_right"
            return "curve_right"

        # (7) 中间两路都在线上 → 直行
        if L2 and R1:
            self.car.Ctrl_Car(s, 0, 0)
            self.last_action = "forward"
            return "forward"

        # (8) 全白 = 脱线，按上次方向搜索
        if self.last_action in ("spin_right", "right", "curve_right"):
            self.car.Ctrl_Car(0, 0, s)
            return "search_right"
        else:
            self.car.Ctrl_Car(0, 0, -s)
            return "search_left"

    def is_cross(self) -> bool:
        """检测十字路口：中间两路都在线上 + 至少一个外侧也在线上。"""
        L1, L2, R1, R2 = _read_sensors(self.car)
        return L2 and R1 and (L1 or R2)

    def stop(self):
        self.car.Ctrl_Car(0, 0, 0)
        self.last_action = "stop"


# ======================== S型半圆绕障 ========================

class ObstacleAvoider:
    """S型半圆绕障器 —— 舵机扫描 + 交替方向半圆绕行。

    使用方式：
        avoider = ObstacleAvoider(car)
        avoider.avoid(speed=30)
    """

    def __init__(self, car):
        self.car = car
        self._alternate = True  # 交替左右绕行

    def avoid(self, speed: int = 30,
              obs_distance_cm: float = 20.0,
              clear_distance_cm: float = 25.0) -> bool:
        """执行绕障。返回 True=完成，False=无法绕开。"""
        s = max(18, min(50, speed))

        # 1. 停车 + 后退
        self.car.Ctrl_Car(0, 0, 0)
        time.sleep(0.05)
        self.car.Ctrl_Car(-20, 0, 0)
        time.sleep(0.08)
        self.car.Ctrl_Car(0, 0, 0)
        time.sleep(0.05)

        # 2. 舵机扫描三向
        self.car.Ctrl_Servo(1, 0)          # 右侧
        time.sleep(0.6)
        right_d = _read_distance_cm(self.car)

        self.car.Ctrl_Servo(1, 180)        # 左侧
        time.sleep(0.6)
        left_d = _read_distance_cm(self.car)

        self.car.Ctrl_Servo(1, 90)         # 前方
        time.sleep(0.6)
        front_d = _read_distance_cm(self.car)

        print(f"[绕障V2] 左={left_d:.1f} 前={front_d:.1f} 右={right_d:.1f}")

        left_ok = left_d == 0 or left_d >= clear_distance_cm
        right_ok = right_d == 0 or right_d >= clear_distance_cm
        front_blocked = 0 < front_d <= obs_distance_cm

        # 3. 三面都堵 → 掉头
        if not left_ok and not right_ok and front_blocked:
            print("[绕障V2] 三面阻塞，掉头")
            self.car.Ctrl_Car(0, 0, 50)
            time.sleep(0.65)
            self.car.Ctrl_Car(0, 0, 0)
            return True

        # 4. 交替半圆绕行
        if self._alternate and right_ok:
            self._semi_circle("right", s)
        elif not self._alternate and left_ok:
            self._semi_circle("left", s)
        elif right_ok:
            self._semi_circle("right", s)
        elif left_ok:
            self._semi_circle("left", s)
        else:
            print("[绕障V2] 无法绕行，微调")
            self.car.Ctrl_Car(0, 0, 30)
            time.sleep(0.35)
            self.car.Ctrl_Car(0, 0, 0)

        self._alternate = not self._alternate
        self.car.Ctrl_Servo(1, 90)
        return True

    def _semi_circle(self, direction: str, speed: int):
        """执行半圆弧绕行。"""
        if direction == "right":
            print("[绕障V2] → 右半圆绕行")
            # 右转45°
            self.car.Ctrl_Car(0, 0, speed)
            time.sleep(0.35)
            # 前进
            self.car.Ctrl_Car(speed, 0, 0)
            time.sleep(0.25)
            # 左转回正
            self.car.Ctrl_Car(0, 0, -speed)
            time.sleep(0.50)
            # 前进
            self.car.Ctrl_Car(speed, 0, 0)
            time.sleep(0.15)
        else:
            print("[绕障V2] ← 左半圆绕行")
            self.car.Ctrl_Car(0, 0, -speed)
            time.sleep(0.35)
            self.car.Ctrl_Car(speed, 0, 0)
            time.sleep(0.25)
            self.car.Ctrl_Car(0, 0, speed)
            time.sleep(0.50)
            self.car.Ctrl_Car(speed, 0, 0)
            time.sleep(0.15)

        self.car.Ctrl_Car(0, 0, 0)
        time.sleep(0.05)


# ======================== 集成循迹+绕障主循环 ========================

def run_line_follow_v2(car, target: str = "B",
                       speed: int = 30,
                       obs_cm: float = 20.0,
                       stop_event: threading.Event = None,
                       pause_event: threading.Event = None,
                       path_nodes: list = None,
                       directions: list = None,
                       on_node: callable = None,
                       on_done: callable = None):
    """V2 循迹主循环：状态机循迹 + S型绕障。

    Args:
        car: Raspbot 底盘实例
        target: 目标点名称
        speed: 基础速度 15-60
        obs_cm: 触发避障的距离阈值（厘米）
        stop_event: 外部停止信号
        pause_event: 外部暂停信号
        path_nodes: 路径节点列表（用于 A* 导航）
        directions: 路口导航指令列表
        on_node: 到达节点时的回调 on_node(node_name, index)
        on_done: 到达终点时的回调 on_done(target)
    """
    if stop_event is None:
        stop_event = threading.Event()
    if pause_event is None:
        pause_event = threading.Event()

    follower = LineFollower(car)
    avoider = ObstacleAvoider(car)

    follower.init()

    # 路口防抖
    cross_timer = 0.0
    cross_handled = False
    CROSS_THRESHOLD = 0.12  # 持续 120ms 确认为路口
    path_index = 0
    path_done = False

    if directions is None:
        directions = [{"node": target, "action": "stop"}]

    print(f"[循迹V2] 开始 → {target}，速度 {speed}")
    if path_nodes:
        print(f"[循迹V2] 路径: {' → '.join(path_nodes)}")

    cycle = 0
    try:
        while not stop_event.is_set():
            # 暂停处理
            if pause_event.is_set():
                car.Ctrl_Car(0, 0, 0)
                while pause_event.is_set() and not stop_event.is_set():
                    time.sleep(0.1)
                if stop_event.is_set():
                    break
                cross_timer = 0.0
                cross_handled = False
                cycle = 0

            # 超声波避障检测（每 3 轮一次）
            if cycle % 3 == 0:
                dist = _read_distance_cm(car)
                if 0 < dist <= obs_cm:
                    print(f"[循迹V2] 障碍物 {dist:.0f}cm ≤ {obs_cm}cm，绕障")
                    car.Ctrl_Car(0, 0, 0)
                    time.sleep(0.05)
                    avoider.avoid(speed=speed, obs_distance_cm=obs_cm)
                    follower.init()  # 重新初始化传感器
                    cross_timer = 0.0
                    cross_handled = False
                    cycle = 0
                    continue

            # 红外避障检测
            try:
                ir = car.read_ir_obstacle()
                if 0 < ir < 0x50:
                    print(f"[循迹V2] 红外避障触发 (IR={ir})")
                    car.Ctrl_Car(0, 0, 0)
                    time.sleep(0.05)
                    avoider.avoid(speed=speed)
                    follower.init()
                    cross_timer = 0.0
                    cross_handled = False
                    cycle = 0
                    continue
            except Exception:
                pass

            # 碰撞检测
            try:
                if car.read_collision():
                    car.emergency_stop(repeats=5, interval=0.02)
                    car.Ctrl_Car(0, 0, 0)
                    print("[循迹V2] ⚠ 碰撞！紧急停车")
                    break
            except Exception:
                pass

            # 路口检测
            if follower.is_cross():
                cross_timer += 0.02
                if cross_timer >= CROSS_THRESHOLD and not cross_handled:
                    cross_handled = True
                    cross_timer = 0.0
                    path_index = min(path_index, len(directions) - 1)
                    action = directions[path_index]["action"]
                    node = directions[path_index]["node"]
                    print(f"[循迹V2] 🚦 路口 #{path_index}: {node}, 动作={action}")

                    if action == "stop":
                        path_done = True
                        car.Ctrl_Car(0, 0, 0)
                        print(f"[循迹V2] 🏁 到达 {node}！")
                        if on_done:
                            on_done(node)
                        break

                    # 执行路口转向
                    _cross_turn_v2(car, action, speed)
                    if on_node:
                        on_node(node, path_index)
                    path_index += 1
                    continue
            else:
                cross_timer = 0.0
                cross_handled = False

            # 正常循迹一步
            follower.step(speed)
            cycle += 1
            time.sleep(0.02)

    except Exception as exc:
        import traceback
        print(f"[循迹V2] 异常：{exc}")
        traceback.print_exc()
    finally:
        car.Ctrl_Car(0, 0, 0)
        print("[循迹V2] 任务结束")


def _cross_turn_v2(car, action: str, speed: int):
    """路口转向动作。"""
    s = max(18, min(50, speed))
    d = 0.45  # 转弯持续

    if action == "straight":
        car.Ctrl_Car(s, 0, 0)
        time.sleep(0.25)
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.05)
        print("[路口V2] 直行通过")
    elif action == "left":
        car.Ctrl_Car(0, 0, -s)
        time.sleep(d)
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.1)
        print("[路口V2] ← 左转")
    elif action == "right":
        car.Ctrl_Car(0, 0, s)
        time.sleep(d)
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.1)
        print("[路口V2] → 右转")
    else:
        car.Ctrl_Car(s, 0, 0)
        time.sleep(0.2)
        car.Ctrl_Car(0, 0, 0)
