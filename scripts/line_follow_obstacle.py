# -*- coding: UTF-8 -*-
"""RASPBOT-V2：独立普通循迹/避障功能。

只做四路红外循迹 + 超声波避障，不打开摄像头，不做人脸识别。
会占用：I2C、电机、循迹模块、超声波、舵机、RGB灯。

运行方式：
    python3 line_follow_obstacle.py

注意：
    RASPBOT-V2 循迹传感器通常是 LOW=压到黑线，HIGH=白底。
    在本项目封装中：False=黑线，True=白底。
"""

from __future__ import annotations

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
_sys.path.insert(0, _HERE)
_sys.path.insert(0, _os.path.join(_ROOT, "web"))
_sys.path.insert(0, _os.path.join(_ROOT, "web", "services"))
_sys.path.insert(0, _os.path.join(_ROOT, "car"))

import os
import time
from typing import Dict

from raspbot_v2_lib import Raspbot
from runtime_guard import RaspbotTaskLock
from safety_control import bind_car, clear_stop_request, install_signal_handlers, stop_requested, unbind_car


# ==================== 可调参数（推荐值） ====================
LINE_SPEED = int(os.environ.get("RASPBOT_LINE_SPEED", "32"))
SMALL_TURN_SPEED = int(os.environ.get("RASPBOT_SMALL_TURN_SPEED", "20"))   # 从28降到20，减少振荡
SPIN_SPEED = int(os.environ.get("RASPBOT_SPIN_SPEED", "36"))
BACK_SPEED = int(os.environ.get("RASPBOT_BACK_SPEED", "25"))
OBSTACLE_DISTANCE_CM = float(os.environ.get("RASPBOT_OBSTACLE_DISTANCE_CM", "20"))
CLEAR_DISTANCE_CM = float(os.environ.get("RASPBOT_CLEAR_DISTANCE_CM", "30"))

# PID 参数（循迹核心）
KP = float(os.environ.get("RASPBOT_LINE_KP", "0.45"))      # 比例系数
KI = float(os.environ.get("RASPBOT_LINE_KI", "0.02"))      # 积分系数（很小，防止累积过冲）
KD = float(os.environ.get("RASPBOT_LINE_KD", "0.08"))      # 微分系数（抑制振荡）
MAX_TURN = int(os.environ.get("RASPBOT_MAX_TURN", "35"))   # 最大转向幅度（防止转太猛）
# ============================================================


def _init_car(car: Raspbot) -> None:
    car.Ctrl_IR_Switch(1)
    car.Ctrl_Ulatist_Switch(1)
    car.Ctrl_BEEP_Switch(0)
    car.Ctrl_Servo(1, 90)
    car.Ctrl_WQ2812_brightness_ALL(0, 0, 80)


def _set_light(car: Raspbot, color: str) -> None:
    if color == "red":
        car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
    elif color == "blue":
        car.Ctrl_WQ2812_brightness_ALL(0, 0, 120)
    elif color == "green":
        car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
    elif color == "magenta":
        car.Ctrl_WQ2812_brightness_ALL(120, 0, 120)
    else:
        car.Ctrl_WQ2812_brightness_ALL(0, 0, 0)


def _run(car: Raspbot, speed: int = LINE_SPEED) -> None:
    car.Ctrl_Car(speed, 0, 0)


def _back(car: Raspbot, speed: int = BACK_SPEED) -> None:
    car.Ctrl_Car(-speed, 0, 0)


def _left(car: Raspbot, speed: int = SMALL_TURN_SPEED) -> None:
    car.Ctrl_Car(0, -speed, 0)


def _right(car: Raspbot, speed: int = SMALL_TURN_SPEED) -> None:
    car.Ctrl_Car(0, speed, 0)


def _curve(car: Raspbot, turn_amount: int, speed: int = LINE_SPEED) -> None:
    """通用转向：正数右转，负数左转，turn_amount 绝对值越大转得越猛。"""
    if turn_amount > 0:
        car.Ctrl_Car(max(10, int(speed * 0.7)), 0, min(turn_amount, MAX_TURN))
    else:
        car.Ctrl_Car(max(10, int(speed * 0.7)), 0, max(turn_amount, -MAX_TURN))


def _spin_left(car: Raspbot, speed: int = SPIN_SPEED) -> None:
    car.Ctrl_Car(0, 0, -speed)


def _spin_right(car: Raspbot, speed: int = SPIN_SPEED) -> None:
    car.Ctrl_Car(0, 0, speed)


def _brake(car: Raspbot, delay: float = 0.0) -> None:
    car.emergency_stop(repeats=5, interval=0.02)
    if delay > 0:
        time.sleep(delay)


def _distance_cm(car: Raspbot) -> float:
    try:
        return float(car.read_ultrasonic_cm())
    except Exception:
        return 0.0


# ==================== PID 循迹核心 ====================
# 全局变量，用于 PID 积分和微分计算
_pid_integral = 0.0
_pid_last_error = 0.0
_pid_lost_counter = 0          # 脱线计数器
_pid_lost_direction = 0        # 脱线前最后一次转向方向（-1左，1右）
_pid_last_turn = 0             # 上一次输出的转向量，用于脱线平滑


def _compute_error(line: Dict[str, bool]) -> float:
    """
    计算偏差值（连续量，范围 -1.0 ~ 1.0）
    负数 = 偏左（需要右转），正数 = 偏右（需要左转）
    0 = 在正中间
    """
    l1 = line["left_1"]
    l2 = line["left_2"]
    r1 = line["right_1"]
    r2 = line["right_2"]

    # 将 bool 转成数值：False(黑线)=1, True(白底)=0
    v_l1 = 1.0 if not l1 else 0.0
    v_l2 = 1.0 if not l2 else 0.0
    v_r1 = 1.0 if not r1 else 0.0
    v_r2 = 1.0 if not r2 else 0.0

    # 加权计算偏离位置（传感器从左到右权重为 -1.5, -0.5, 0.5, 1.5）
    weighted = -1.5 * v_l1 - 0.5 * v_l2 + 0.5 * v_r1 + 1.5 * v_r2
    total = v_l1 + v_l2 + v_r1 + v_r2

    if total == 0:
        # 全白（脱线）或全黑（可能十字路口）
        # 全黑：中间值，0
        # 全白：返回 None，由调用方处理
        if (v_l1 + v_l2 + v_r1 + v_r2) == 0:
            return None  # 全白脱线
        return 0.0       # 全黑（十字路口），视为居中

    # 归一化到 -1 ~ 1
    error = weighted / (total * 1.5)
    # 限幅，防止极端值
    return max(-1.0, min(1.0, error))


def _line_follow_step(car: Raspbot, line: Dict[str, bool]) -> None:
    """PID 循迹控制 - 平滑连续转向，大幅减少振荡。"""
    global _pid_integral, _pid_last_error, _pid_lost_counter
    global _pid_lost_direction, _pid_last_turn

    error = _compute_error(line)

    # ---------- 情况1：全白脱线 ----------
    if error is None:
        _pid_lost_counter += 1
        print(f"[脱线] 计数器={_pid_lost_counter}, 方向={_pid_lost_direction}")

        if _pid_lost_counter < 8:
            # 脱线初期：继续按原来的方向缓慢转向，争取重新抓线
            # 使用之前记忆的方向，如果没记忆则默认右转
            direction = _pid_lost_direction if _pid_lost_direction != 0 else 1
            turn = direction * 15  # 小幅度持续找线
            _curve(car, turn, int(LINE_SPEED * 0.6))
            print(f"[脱线找线] 转向={turn}")
        elif _pid_lost_counter < 20:
            # 脱线中期：加大转向力度，做更大范围的搜索
            direction = _pid_lost_direction if _pid_lost_direction != 0 else 1
            turn = direction * MAX_TURN
            _curve(car, turn, int(LINE_SPEED * 0.5))
            print(f"[脱线大范围搜索] 转向={turn}")
        else:
            # 脱线太久（超过20个周期 ≈ 0.3秒）：执行原地旋转找线
            print("[脱线超时] 执行原地旋转找线")
            if _pid_lost_direction >= 0:
                _spin_right(car, int(SPIN_SPEED * 0.6))
            else:
                _spin_left(car, int(SPIN_SPEED * 0.6))

        # 保存当前的 error 用于下一次积分（脱线时不累积积分）
        _pid_last_error = 0
        return

    # ---------- 情况2：成功抓到线，重置脱线计数器 ----------
    if _pid_lost_counter > 0:
        print("[重新抓线] 恢复正常循迹")
        _pid_lost_counter = 0
        _pid_integral = 0  # 重置积分，防止重新抓线时过冲

    # 更新记忆方向（用于脱线时参考）
    if error > 0.05:
        _pid_lost_direction = 1   # 偏右，脱线后应该右转找线
    elif error < -0.05:
        _pid_lost_direction = -1  # 偏左，脱线后应该左转找线
    # 如果 error 接近0，保持原方向不变

    # ---------- PID 计算 ----------
    # 比例项（P）
    p_term = KP * error

    # 积分项（I）：只在误差较小时累积，防止积分饱和
    if abs(error) < 0.3:
        _pid_integral += error * 0.015  # 乘以 dt（约15ms）
        # 积分限幅，防止累积过大
        _pid_integral = max(-0.5, min(0.5, _pid_integral))
    else:
        # 大误差时重置积分，防止响应滞后
        _pid_integral = 0

    i_term = KI * _pid_integral

    # 微分项（D）
    d_term = KD * (error - _pid_last_error) / 0.015  # dt = 15ms
    _pid_last_error = error

    # 计算最终转向量
    turn_raw = p_term + i_term + d_term
    # 放大到电机转向范围（max_turn 对应 error=1 时的转向量）
    turn = int(turn_raw * MAX_TURN)

    # 限幅，防止转向过猛
    turn = max(-MAX_TURN, min(MAX_TURN, turn))

    # 记录本次转向量（用于脱线平滑）
    _pid_last_turn = turn

    # ---------- 特殊处理：全黑（十字路口） ----------
    # 当 error == 0 且四路都是黑线时，可能是十字路口，直行通过
    all_black = (line["left_1"] is False and line["left_2"] is False and
                 line["right_1"] is False and line["right_2"] is False)
    if all_black and abs(error) < 0.05:
        _run(car, LINE_SPEED)
        # print("[十字路口] 直行通过")
        return

    # ---------- 执行转向 ----------
    # 如果转向量很小（在死区内），直接直行
    if abs(turn) <= 3:
        _run(car, LINE_SPEED)
    else:
        # 转向时稍微降低前进速度，保证稳定性
        speed_factor = 1.0 - 0.3 * (abs(turn) / MAX_TURN)
        current_speed = max(15, int(LINE_SPEED * speed_factor))
        _curve(car, turn, current_speed)
        # print(f"[PID] error={error:.3f}, turn={turn}, speed={current_speed}")


def _avoid_obstacle(car: Raspbot, prefer_right: bool) -> bool:
    """避障动作。返回下一次是否优先右绕。"""
    global _pid_lost_counter, _pid_integral, _pid_last_error

    _set_light(car, "red")
    _brake(car, 0.05)
    _back(car, BACK_SPEED)
    time.sleep(0.12)
    _brake(car, 0.1)

    # 右侧测距
    car.Ctrl_Servo(1, 0)
    time.sleep(0.55)
    right_distance = _distance_cm(car)

    # 左侧测距
    car.Ctrl_Servo(1, 180)
    time.sleep(0.55)
    left_distance = _distance_cm(car)

    # 正前方复位
    car.Ctrl_Servo(1, 90)
    time.sleep(0.35)
    front_distance = _distance_cm(car)

    print(f"避障测距：左={left_distance:.1f}, 前={front_distance:.1f}, 右={right_distance:.1f}")

    left_clear = left_distance == 0 or left_distance >= CLEAR_DISTANCE_CM
    right_clear = right_distance == 0 or right_distance >= CLEAR_DISTANCE_CM
    front_blocked = 0 < front_distance <= OBSTACLE_DISTANCE_CM

    if not left_clear and not right_clear and front_blocked:
        print("三面较近，执行掉头。")
        _set_light(car, "magenta")
        _spin_right(car, SPIN_SPEED)
        time.sleep(0.65)
        _brake(car, 0.1)
        # 避障后重置PID状态，防止循迹异常
        _pid_lost_counter = 0
        _pid_integral = 0
        _pid_last_error = 0
        return prefer_right

    if right_clear and (prefer_right or not left_clear):
        print("右侧可通，右绕避障。")
        _set_light(car, "blue")
        _right(car, SMALL_TURN_SPEED)
        time.sleep(0.55)
        _run(car, max(15, int(LINE_SPEED * 0.7)))
        time.sleep(0.25)
        _left(car, SMALL_TURN_SPEED)
        time.sleep(0.70)
    elif left_clear:
        print("左侧可通，左绕避障。")
        _set_light(car, "blue")
        _left(car, SMALL_TURN_SPEED)
        time.sleep(0.55)
        _run(car, max(15, int(LINE_SPEED * 0.7)))
        time.sleep(0.25)
        _right(car, SMALL_TURN_SPEED)
        time.sleep(0.70)
    else:
        print("左右测距都不理想，原地右转微调。")
        _spin_right(car, SPIN_SPEED)
        time.sleep(0.35)

    _brake(car, 0.1)
    _set_light(car, "green")

    # 避障后重置PID状态
    _pid_lost_counter = 0
    _pid_integral = 0
    _pid_last_error = 0
    return not prefer_right


def run_line_follow_obstacle() -> None:
    with RaspbotTaskLock("line_follow_obstacle"):
        car = Raspbot()
        bind_car(car)
        install_signal_handlers()
        clear_stop_request()
        prefer_right = True

        # 重置全局PID状态
        global _pid_integral, _pid_last_error, _pid_lost_counter, _pid_lost_direction, _pid_last_turn
        _pid_integral = 0.0
        _pid_last_error = 0.0
        _pid_lost_counter = 0
        _pid_lost_direction = 0
        _pid_last_turn = 0

        try:
            car.require_chassis()
            print("底盘 I2C 正常：", car.backend_name())
            _init_car(car)
            print("进入独立普通循迹/避障功能。不会打开摄像头。按 Ctrl+C 停止。")
            print(f"障碍阈值：0 < distance <= {OBSTACLE_DISTANCE_CM} 时触发避障。")
            print(f"PID 参数：KP={KP}, KI={KI}, KD={KD}, MAX_TURN={MAX_TURN}")

            while True:
                if stop_requested():
                    raise KeyboardInterrupt("收到停止请求。")

                distance = _distance_cm(car)
                if 0 < distance <= OBSTACLE_DISTANCE_CM:
                    print(f"检测到障碍：{distance:.1f}")
                    prefer_right = _avoid_obstacle(car, prefer_right)
                    continue

                line = car.read_line_sensors()
                _line_follow_step(car, line)
                time.sleep(0.015)

        except KeyboardInterrupt:
            print("用户中断普通循迹/避障。")
        finally:
            _brake(car, 0.1)
            _set_light(car, "off")
            unbind_car(car)
            car.close()


if __name__ == "__main__":
    run_line_follow_obstacle()