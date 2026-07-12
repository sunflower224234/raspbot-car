# -*- coding: UTF-8 -*-
"""RASPBOT-V2 连续绕桩避障（基于 raspbot_v2_lib）。

实现麦克纳姆轮横移绕障、传感器回线和自动回正。
不再依赖 McLumk_Wheel_Sports 官方库，统一使用 raspbot_v2_lib。

运行方式：
    python3 obstacle_avoidance.py

环境变量：
    RASPBOT_AVOID_SIDE             绕桩方向 right/left/alternate（默认 alternate）
    RASPBOT_OBSTACLE_TRIGGER_MM    触发距离 mm（默认 260）
    RASPBOT_AVOID_SIDE_OUT_TIME    侧移时间 s（默认 0.90）
    RASPBOT_AVOID_PASS_TIME        越障前进时间 s（默认 1.25）
    RASPBOT_RETURN_MODE            回线模式 line/time（默认 line）
    RASPBOT_USE_AUTO_RECENTER      是否自动回正（默认 1）
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Optional, Tuple

from raspbot_v2_lib import Raspbot
from runtime_guard import RaspbotTaskLock
from safety_control import (
    bind_car, clear_stop_request, install_signal_handlers,
    stop_requested, unbind_car,
)


# ==================== 参数配置 ====================
def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


LINE_SPEED = env_int("RASPBOT_LINE_SPEED", 20)
SIDE_SPEED = env_int("RASPBOT_AVOID_SIDE_SPEED", 24)
FORWARD_SPEED = env_int("RASPBOT_AVOID_FORWARD_SPEED", 22)
BACK_SPEED = env_int("RASPBOT_BACK_SPEED", 18)
RETURN_SIDE_SPEED = env_int("RASPBOT_RETURN_SIDE_SPEED", 15)
RETURN_FORWARD_SPEED = env_int("RASPBOT_RETURN_FORWARD_SPEED", 4)

OBSTACLE_TRIGGER_MM = env_int("RASPBOT_OBSTACLE_TRIGGER_MM", 260)
TOO_CLOSE_MM = env_int("RASPBOT_TOO_CLOSE_MM", 140)
CLEAR_MM = env_int("RASPBOT_CLEAR_MM", 420)

CONFIRM_COUNT = env_int("RASPBOT_OBSTACLE_CONFIRM_COUNT", 2)
COOLDOWN_TIME = env_float("RASPBOT_AVOID_COOLDOWN", 1.0)

BACK_TIME = env_float("RASPBOT_AVOID_BACK_TIME", 0.15)
SIDE_OUT_TIME = env_float("RASPBOT_AVOID_SIDE_OUT_TIME", 0.90)
PASS_TIME = env_float("RASPBOT_AVOID_PASS_TIME", 1.25)
SIDE_BACK_TIME = env_float("RASPBOT_AVOID_SIDE_BACK_TIME", 1.25)

AVOID_SIDE = os.environ.get("RASPBOT_AVOID_SIDE", "alternate").strip().lower()
RETURN_MODE = os.environ.get("RASPBOT_RETURN_MODE", "line").strip().lower()

RETURN_TIMEOUT = env_float("RASPBOT_AVOID_RETURN_TIMEOUT", 3.20)
LINE_CONFIRM_COUNT = env_int("RASPBOT_LINE_CONFIRM_COUNT", 2)
LINE_ALIGN_TIMEOUT = env_float("RASPBOT_LINE_ALIGN_TIMEOUT", 0.80)

USE_AUTO_RECENTER = os.environ.get("RASPBOT_USE_AUTO_RECENTER", "1") == "1"
RECENTER_TIMEOUT = env_float("RASPBOT_RECENTER_TIMEOUT", 1.60)
RECENTER_SIDE_MAX = env_int("RASPBOT_RECENTER_SIDE_MAX", 12)
RECENTER_SIDE_KP = env_float("RASPBOT_RECENTER_SIDE_KP", 4.0)
RECENTER_TURN_MAX = env_int("RASPBOT_RECENTER_TURN_MAX", 7)
RECENTER_TURN_KP = env_float("RASPBOT_RECENTER_TURN_KP", 2.0)
RECENTER_STABLE_COUNT = env_int("RASPBOT_RECENTER_STABLE_COUNT", 6)

DISTANCE_SAMPLES = env_int("RASPBOT_DISTANCE_SAMPLES", 3)
LOOP_DT = env_float("RASPBOT_AVOID_LOOP_DT", 0.03)
DEBUG_DISTANCE = os.environ.get("RASPBOT_DEBUG_DISTANCE", "0") == "1"
DEBUG_LINE = os.environ.get("RASPBOT_DEBUG_LINE", "0") == "1"
# =================================================


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ==================== 传感器辅助 ====================
def read_distance_mm(car: Raspbot, samples: int = DISTANCE_SAMPLES) -> int:
    """多次读取超声波距离取中位数（mm）。"""
    values = []
    for _ in range(max(1, samples)):
        d = car.read_ultrasonic_mm()
        if d > 0:
            values.append(d)
        time.sleep(0.015)
    return int(statistics.median(values)) if values else 0


def read_line_safe(car: Raspbot) -> Optional[Tuple[bool, bool, bool, bool]]:
    """安全读取四路循迹传感器。False=黑线。"""
    try:
        line = car.read_line_sensors()
        return (line["left_1"], line["left_2"], line["right_1"], line["right_2"])
    except Exception:
        return None


def is_black(v: bool) -> bool:
    return v is False


def center_on_line(line: Optional[Tuple[bool, bool, bool, bool]]) -> bool:
    if line is None:
        return False
    return is_black(line[1]) or is_black(line[2])


def any_black(line: Optional[Tuple[bool, bool, bool, bool]]) -> bool:
    if line is None:
        return False
    return any(is_black(v) for v in line)


def left_black(line: Optional[Tuple[bool, bool, bool, bool]]) -> bool:
    if line is None:
        return False
    return is_black(line[0]) or is_black(line[1])


def right_black(line: Optional[Tuple[bool, bool, bool, bool]]) -> bool:
    if line is None:
        return False
    return is_black(line[2]) or is_black(line[3])


# ==================== 回线逻辑 ====================
def return_to_line_by_sensor(car: Raspbot, back_side: str) -> bool:
    """使用循迹传感器闭环回线。"""
    print(f"向{back_side}慢速回线，直到中间循迹探头检测到黑线")
    start = time.time()
    stable = 0
    lateral = RETURN_SIDE_SPEED if back_side == "right" else -RETURN_SIDE_SPEED

    while time.time() - start < RETURN_TIMEOUT:
        line = read_line_safe(car)
        if DEBUG_LINE:
            pattern = "".join("1" if is_black(v) else "0" for v in line) if line else "None"
            print(f"回线检测：{pattern}")

        if center_on_line(line):
            stable += 1
            if stable >= LINE_CONFIRM_COUNT:
                car.stop()
                print("已回到原线")
                return True
        else:
            stable = 0

        car.Ctrl_Car(RETURN_FORWARD_SPEED, lateral, 0)
        time.sleep(LOOP_DT)

    car.stop()
    return False


def return_to_line_by_time(car: Raspbot, back_side: str) -> None:
    """无循迹传感器时的退化方案：按时间回位。"""
    print(f"按时间补偿向{back_side}平移：{SIDE_BACK_TIME:.2f}s")
    lateral = SIDE_SPEED if back_side == "right" else -SIDE_SPEED
    car.Ctrl_Car(0, lateral, 0)
    time.sleep(SIDE_BACK_TIME)
    car.stop()


# ==================== 自动回正 ====================
def get_line_error(line: Optional[Tuple[bool, bool, bool, bool]]) -> Optional[float]:
    """计算黑线偏移误差。负=线偏左，正=线偏右，None=看不到线。"""
    if line is None:
        return None
    weights = (-3.0, -1.0, 1.0, 3.0)
    positions = [weights[i] for i, v in enumerate(line) if is_black(v)]
    if not positions:
        return None
    return sum(positions) / len(positions)


def auto_recenter(car: Raspbot, back_side: str) -> None:
    """偏移后自动回正：根据黑线位置误差闭环修正。"""
    if not USE_AUTO_RECENTER:
        return

    print("启动偏移后自动回正模块")
    start = time.time()
    stable = 0
    back_lateral = RETURN_SIDE_SPEED if back_side == "right" else -RETURN_SIDE_SPEED

    while time.time() - start < RECENTER_TIMEOUT:
        line = read_line_safe(car)
        err = get_line_error(line)

        if err is None:
            stable = 0
            car.Ctrl_Car(0, back_lateral, 0)
            time.sleep(LOOP_DT)
            continue

        lateral = int(clamp(err * RECENTER_SIDE_KP, -RECENTER_SIDE_MAX, RECENTER_SIDE_MAX))
        turn = int(clamp(err * RECENTER_TURN_KP, -RECENTER_TURN_MAX, RECENTER_TURN_MAX))

        if center_on_line(line) and abs(err) <= 1.05:
            stable += 1
            car.Ctrl_Car(10, 0, 0)
            if stable >= RECENTER_STABLE_COUNT:
                car.stop()
                print("偏移后自动回正完成")
                return
        else:
            stable = 0
            car.Ctrl_Car(10, lateral, turn)

        time.sleep(LOOP_DT)

    car.stop()
    print("自动回正结束")


# ==================== 绕桩动作 ====================
def opposite_side(side: str) -> str:
    return "left" if side == "right" else "right"


def choose_avoid_side(avoid_count: int) -> str:
    if AVOID_SIDE == "left":
        return "left"
    if AVOID_SIDE == "alternate":
        return "right" if avoid_count % 2 == 0 else "left"
    return "right"


def avoid_pole(car: Raspbot, side: str) -> None:
    """执行一次完整绕桩避障。"""
    back_side = opposite_side(side)
    print(f"开始绕桩：向{side}侧绕开")

    # 1. 停车
    car.stop()
    time.sleep(0.08)

    # 2. 如果太近，先后退
    d = read_distance_mm(car)
    if 0 < d < TOO_CLOSE_MM:
        print(f"距离太近 {d}mm，先后退")
        car.Ctrl_Car(-BACK_SPEED, 0, 0)
        time.sleep(BACK_TIME)
        car.stop()

    # 3. 侧移离开原路线
    print(f"向{side}平移绕开障碍")
    lateral = SIDE_SPEED if side == "right" else -SIDE_SPEED
    car.Ctrl_Car(0, lateral, 0)
    time.sleep(SIDE_OUT_TIME)
    car.stop()

    # 4. 前进越过障碍
    print("前进越过障碍")
    car.Ctrl_Car(FORWARD_SPEED, 0, 0)
    time.sleep(PASS_TIME)
    car.stop()

    # 5. 回到原路线
    returned = False
    if RETURN_MODE == "line":
        returned = return_to_line_by_sensor(car, back_side)
    if not returned:
        return_to_line_by_time(car, back_side)

    # 6. 自动回正
    auto_recenter(car, back_side)

    print("绕桩完成")


# ==================== 简单循迹前进 ====================
def line_follow_forward(car: Raspbot, speed: int = LINE_SPEED) -> None:
    """简单循迹前进（不打开 PID，用于绕桩间隙）。"""
    line = read_line_safe(car)
    if line is None:
        car.Ctrl_Car(speed, 0, 0)
        return

    b0, b1, b2, b3 = [is_black(v) for v in line]
    if b1 and b2:
        car.Ctrl_Car(speed, 0, 0)
    elif b1:
        car.Ctrl_Car(speed, -9, -3)
    elif b2:
        car.Ctrl_Car(speed, 9, 3)
    elif b0:
        car.Ctrl_Car(max(10, speed - 4), -9, -6)
    elif b3:
        car.Ctrl_Car(max(10, speed - 4), 9, 6)
    else:
        car.Ctrl_Car(max(10, speed - 5), 0, 0)


# ==================== 主入口 ====================
def run_obstacle_avoidance() -> None:
    """启动连续绕桩避障模式。"""
    with RaspbotTaskLock("obstacle_avoidance"):
        car = Raspbot()
        bind_car(car)
        install_signal_handlers()
        clear_stop_request()

        try:
            car.require_chassis()
            print("底盘 I2C 正常：", car.backend_name())
            car.Ctrl_IR_Switch(1)
            car.Ctrl_Ulatist_Switch(1)
            car.Ctrl_BEEP_Switch(0)
            car.Ctrl_WQ2812_brightness_ALL(0, 0, 80)
            print("进入连续绕桩避障模式。按 Ctrl+C 停止。")
            print(f"触发距离：{OBSTACLE_TRIGGER_MM}mm, 绕桩方向：{AVOID_SIDE}")

            hit_count = 0
            avoid_count = 0
            cooldown_until = 0.0

            while True:
                if stop_requested():
                    raise KeyboardInterrupt("收到停止请求。")

                d = read_distance_mm(car)
                now = time.time()

                if DEBUG_DISTANCE:
                    print(f"距离：{d}mm")

                if now < cooldown_until:
                    line_follow_forward(car)
                    time.sleep(LOOP_DT)
                    continue

                if 0 < d <= OBSTACLE_TRIGGER_MM:
                    hit_count += 1
                else:
                    hit_count = max(0, hit_count - 1)

                if hit_count >= CONFIRM_COUNT:
                    print(f"检测到障碍物，距离：{d}mm")
                    hit_count = 0
                    side = choose_avoid_side(avoid_count)
                    avoid_pole(car, side)
                    avoid_count += 1
                    cooldown_until = time.time() + COOLDOWN_TIME
                else:
                    line_follow_forward(car)

                time.sleep(LOOP_DT)

        except KeyboardInterrupt:
            print("用户中断。")
        finally:
            car.emergency_stop(repeats=5, interval=0.02)
            car.Ctrl_WQ2812_brightness_ALL(0, 0, 0)
            unbind_car(car)
            car.close()


if __name__ == "__main__":
    run_obstacle_avoidance()
