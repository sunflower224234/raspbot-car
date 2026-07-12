#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
RASPBOT-V2 麦克纳姆轮连续绕桩避障代码：偏移后自动回正版

本版重点修复：
0. 针对视频中“能绕开，但回到线后车头偏斜，继续斜着跑出原直线”的问题。
1. 不再只依赖 SIDE_BACK_TIME 盲目回位。
2. 绕过障碍后，优先使用循迹传感器寻找原本黑线/直线。
3. 如果当前官方库读不到循迹传感器，则自动退回“时间补偿回位”。
4. 回位速度单独降低，避免平移过快直接冲过原路线。
5. 保留你原来的官方 McLumk_Wheel_Sports 控制方式，不强制混用 raspbot_v2_lib.py。
6. 新增“偏移后自动回正”模块：绕桩侧移后先按传感器找回黑线，再按线位置误差闭环修正横向偏移和车头角度。

运行：
    cd ~/ai_car2
    python3 continuous_obstacle_avoidance_auto_recenter.py

如果左右平移方向反了：
    修改 strafe_robot() 中 Ctrl_Car 的 lateral 正负号，
    或者把 AVOID_SIDE 改成 left/right 测试。
"""

import os
import sys
import time
import statistics


# ==========================================================
# 导入 RASPBOT-V2 官方库
# ==========================================================

sys.path.append("/home/pi/project_demo/lib")

try:
    from McLumk_Wheel_Sports import *
except Exception as e:
    raise RuntimeError(
        "导入 McLumk_Wheel_Sports 失败，请确认官方库路径是否存在："
        "/home/pi/project_demo/lib"
    ) from e


# ==========================================================
# 参数配置
# ==========================================================

def env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


# 正常前进速度：这份独立绕桩代码不是完整循迹代码，所以速度不要太高。
LINE_SPEED = env_int("RASPBOT_LINE_SPEED", 20)

# 绕桩动作速度
SIDE_SPEED = env_int("RASPBOT_AVOID_SIDE_SPEED", 24)
FORWARD_SPEED = env_int("RASPBOT_AVOID_FORWARD_SPEED", 22)
BACK_SPEED = env_int("RASPBOT_BACK_SPEED", 18)

# 回线速度必须比绕开速度低，否则循迹传感器刚检测到黑线就冲过了。
RETURN_SIDE_SPEED = env_int("RASPBOT_RETURN_SIDE_SPEED", 15)
RETURN_FORWARD_SPEED = env_int("RASPBOT_RETURN_FORWARD_SPEED", 4)

# 触发距离，单位 mm
OBSTACLE_TRIGGER_MM = env_int("RASPBOT_OBSTACLE_TRIGGER_MM", 260)
TOO_CLOSE_MM = env_int("RASPBOT_TOO_CLOSE_MM", 140)
CLEAR_MM = env_int("RASPBOT_CLEAR_MM", 420)

CONFIRM_COUNT = env_int("RASPBOT_OBSTACLE_CONFIRM_COUNT", 2)
COOLDOWN_TIME = env_float("RASPBOT_AVOID_COOLDOWN", 1.0)

# 动作时间
BACK_TIME = env_float("RASPBOT_AVOID_BACK_TIME", 0.15)
SIDE_OUT_TIME = env_float("RASPBOT_AVOID_SIDE_OUT_TIME", 0.90)
PASS_TIME = env_float("RASPBOT_AVOID_PASS_TIME", 1.25)

# 仅在读不到循迹传感器时使用。
# 你原来是 0.82，实际麦克纳姆轮反向回位通常要更久一点。
SIDE_BACK_TIME = env_float("RASPBOT_AVOID_SIDE_BACK_TIME", 1.25)

STABILIZE_TIME = env_float("RASPBOT_AVOID_STABILIZE_TIME", 0.00)

# 绕桩方向：right / left / alternate
AVOID_SIDE = os.environ.get("RASPBOT_AVOID_SIDE", "alternate").strip().lower()

# 回线模式：
# line：优先用循迹传感器找黑线，失败后时间补偿
# time：只按 SIDE_BACK_TIME 时间回位
RETURN_MODE = os.environ.get("RASPBOT_RETURN_MODE", "line").strip().lower()

# 循迹传感器回线参数
RETURN_TIMEOUT = env_float("RASPBOT_AVOID_RETURN_TIMEOUT", 3.20)
LINE_CONFIRM_COUNT = env_int("RASPBOT_LINE_CONFIRM_COUNT", 2)
LINE_ALIGN_TIMEOUT = env_float("RASPBOT_LINE_ALIGN_TIMEOUT", 0.80)
LINE_SEARCH_EXTRA_TIME = env_float("RASPBOT_LINE_SEARCH_EXTRA_TIME", 0.25)

# 轻微车头修正，默认不开。
# 如果每次向右绕完车头都偏右，可以设置：
# export RASPBOT_TRIM_AFTER_RIGHT=-0.08
# 如果每次向左绕完车头都偏左，可以设置：
# export RASPBOT_TRIM_AFTER_LEFT=0.08
# 约定：正数=右转，负数=左转。
# 已新增自动回正闭环，因此这里默认不再做盲目车头微调；确实存在固定偏航时再手动设置环境变量。
TRIM_AFTER_RIGHT = env_float("RASPBOT_TRIM_AFTER_RIGHT", 0.00)
TRIM_AFTER_LEFT = env_float("RASPBOT_TRIM_AFTER_LEFT", 0.00)
TRIM_TURN_SPEED = env_int("RASPBOT_TRIM_TURN_SPEED", 14)

# 绕桩后和正常前进时，尽量用循迹传感器做小幅闭环，避免纯直行越跑越偏。
USE_LINE_FOLLOW = os.environ.get("RASPBOT_USE_LINE_FOLLOW", "1") == "1"
LINE_FOLLOW_SPEED = env_int("RASPBOT_LINE_FOLLOW_SPEED", 17)
LINE_CORRECT_SIDE_SPEED = env_int("RASPBOT_LINE_CORRECT_SIDE_SPEED", 9)
LINE_CORRECT_TURN_SPEED = env_int("RASPBOT_LINE_CORRECT_TURN_SPEED", 6)
AFTER_AVOID_ALIGN_TIME = env_float("RASPBOT_AFTER_AVOID_ALIGN_TIME", 0.80)

# ==========================================================
# 偏移之后自动回正模块参数
# ==========================================================
# 这个模块解决的问题是：小车侧移绕障后，即使回到黑线附近，车身中心和车头方向仍可能有残余偏移。
# 自动回正会读取四路循迹传感器，根据黑线在传感器下方的位置计算误差，并同时修正横向偏移和车头角度。
USE_AUTO_RECENTER = os.environ.get("RASPBOT_USE_AUTO_RECENTER", "1") == "1"
RECENTER_TIMEOUT = env_float("RASPBOT_RECENTER_TIMEOUT", 1.60)
RECENTER_FORWARD_SPEED = env_int("RASPBOT_RECENTER_FORWARD_SPEED", 10)
RECENTER_SIDE_MAX = env_int("RASPBOT_RECENTER_SIDE_MAX", 12)
RECENTER_SIDE_MIN = env_int("RASPBOT_RECENTER_SIDE_MIN", 5)
RECENTER_SIDE_KP = env_float("RASPBOT_RECENTER_SIDE_KP", 4.0)
RECENTER_TURN_MAX = env_int("RASPBOT_RECENTER_TURN_MAX", 7)
RECENTER_TURN_KP = env_float("RASPBOT_RECENTER_TURN_KP", 2.0)
RECENTER_ERROR_DEADZONE = env_float("RASPBOT_RECENTER_ERROR_DEADZONE", 1.05)
RECENTER_STABLE_COUNT = env_int("RASPBOT_RECENTER_STABLE_COUNT", 6)
RECENTER_NO_LINE_SPEED = env_int("RASPBOT_RECENTER_NO_LINE_SPEED", 10)
# 如果读不到循迹传感器，则只做很短的反向补偿。0.18 表示约补偿 SIDE_OUT_TIME 的 18%。
# 如果发现回正过头，把它改小到 0.08；如果仍然偏在绕障侧，把它调大到 0.25。
RECENTER_TIME_FALLBACK_RATIO = env_float("RASPBOT_RECENTER_TIME_FALLBACK_RATIO", 0.18)

DISTANCE_SAMPLES = env_int("RASPBOT_DISTANCE_SAMPLES", 3)
LOOP_DT = env_float("RASPBOT_AVOID_LOOP_DT", 0.03)
DEBUG_DISTANCE = os.environ.get("RASPBOT_DEBUG_DISTANCE", "1") == "1"
DEBUG_LINE = os.environ.get("RASPBOT_DEBUG_LINE", "0") == "1"


# ==========================================================
# 基础控制函数
# ==========================================================

def _has_func(name):
    return name in globals() and callable(globals()[name])


def _call_func(name, *args):
    if _has_func(name):
        globals()[name](*args)
        return True
    return False


def stop_all(delay=0.05):
    try:
        if _call_func("stop_robot"):
            time.sleep(delay)
            return
    except Exception as e:
        print("stop_robot 调用失败：", e)

    try:
        bot.Ctrl_Car(0, 0, 0)
    except Exception:
        pass

    time.sleep(delay)


def drive_robot(forward=0, lateral=0, turn=0):
    """
    底层三轴控制。
    forward > 0：前进
    lateral > 0：向右平移
    lateral < 0：向左平移
    turn > 0：右转
    turn < 0：左转
    """
    try:
        bot.Ctrl_Car(int(forward), int(lateral), int(turn))
        return True
    except Exception:
        return False


def forward_robot(speed):
    if _call_func("move_forward", speed):
        return
    if _call_func("forward", speed):
        return
    if not drive_robot(speed, 0, 0):
        print("前进函数不可用")


def backward_robot(speed):
    if _call_func("move_backward", speed):
        return
    if _call_func("backward", speed):
        return
    if not drive_robot(-speed, 0, 0):
        print("后退函数不可用")


def strafe_robot(direction, speed):
    """
    麦克纳姆轮左右平移。
    direction="right"：向右平移
    direction="left" ：向左平移
    """
    direction = direction.lower()

    if direction == "right":
        for name in ("move_right", "right_move", "translate_right", "lateral_right"):
            if _call_func(name, speed):
                return
        if drive_robot(0, speed, 0):
            return

    elif direction == "left":
        for name in ("move_left", "left_move", "translate_left", "lateral_left"):
            if _call_func(name, speed):
                return
        if drive_robot(0, -speed, 0):
            return

    print(f"警告：没有找到 {direction} 平移函数，请检查 McLumk_Wheel_Sports.py。")
    stop_all(0.05)


def strafe_with_forward(direction, side_speed, forward_speed=0):
    """
    回线时使用：一边慢速平移，一边给一点点前进量。
    原因：循迹探头在车头下方，纯横移有时会擦着黑线过去；
    带很小前进量更容易让中间探头压回黑线。
    """
    direction = direction.lower()
    lateral = side_speed if direction == "right" else -side_speed

    if drive_robot(forward_speed, lateral, 0):
        return

    # 如果 Ctrl_Car 不可用，退化为纯平移。
    strafe_robot(direction, side_speed)


def rotate_robot(turn_speed):
    """
    turn_speed > 0 右转，turn_speed < 0 左转。
    """
    if not drive_robot(0, 0, turn_speed):
        # 兼容可能存在的官方转向函数
        if turn_speed > 0:
            for name in ("turn_right", "spin_right", "rotate_right"):
                if _call_func(name, abs(turn_speed)):
                    return
        elif turn_speed < 0:
            for name in ("turn_left", "spin_left", "rotate_left"):
                if _call_func(name, abs(turn_speed)):
                    return


def timed_action(action, duration, stop_after=True):
    action()
    time.sleep(max(0.0, duration))
    if stop_after:
        stop_all(0.06)


# ==========================================================
# 超声波读取
# ==========================================================

def enable_ultrasonic(enable=True):
    try:
        bot.Ctrl_Ulatist_Switch(1 if enable else 0)
        time.sleep(0.15)
        print("超声波测距已打开" if enable else "超声波测距已关闭")
    except Exception as e:
        print("设置超声波开关失败：", e)


def read_distance_mm_once():
    try:
        diss_H = bot.read_data_array(0x1b, 1)[0]
        diss_L = bot.read_data_array(0x1a, 1)[0]
        dis = (int(diss_H) << 8) | int(diss_L)
        if dis <= 0:
            return 0
        return dis
    except Exception as e:
        print("读取超声波失败：", e)
        return 0


def read_distance_mm(samples=None):
    n = max(1, samples or DISTANCE_SAMPLES)
    values = []

    for _ in range(n):
        d = read_distance_mm_once()
        if d > 0:
            values.append(d)
        time.sleep(0.015)

    if not values:
        return 0

    return int(statistics.median(values))


# ==========================================================
# 循迹传感器读取与回线判断
# ==========================================================

def enable_line_sensor(enable=True):
    """
    打开循迹/红外模块。
    如果官方库没有 Ctrl_IR_Switch，会自动忽略。
    """
    try:
        if hasattr(bot, "Ctrl_IR_Switch"):
            bot.Ctrl_IR_Switch(1 if enable else 0)
            time.sleep(0.08)
            print("循迹传感器已打开" if enable else "循迹传感器已关闭")
            return True
    except Exception as e:
        print("设置循迹传感器失败：", e)

    return False


def _normalize_line_values(value):
    """
    统一转换为四元组：
    (left_1, left_2, right_1, right_2)

    约定：
    False / 0 = 黑线
    True  / 1 = 白底
    """
    if value is None:
        return None

    if isinstance(value, dict):
        keys = ("left_1", "left_2", "right_1", "right_2")
        if all(k in value for k in keys):
            return tuple(bool(value[k]) for k in keys)

    if isinstance(value, (list, tuple)) and len(value) >= 4:
        return tuple(bool(v) for v in value[:4])

    return None


def read_line_sensors_safe():
    """
    尝试读取四路循迹传感器。
    优先调用 bot.read_line_sensors()，其次尝试全局 read_line_sensors()。

    如果你的官方 McLumk_Wheel_Sports.py 没有封装循迹读取，
    这里会返回 None，程序自动退回时间回位。
    """
    # 1. bot.read_line_sensors()
    try:
        if hasattr(bot, "read_line_sensors"):
            line = _normalize_line_values(bot.read_line_sensors())
            if line is not None:
                return line
    except Exception as e:
        if DEBUG_LINE:
            print("bot.read_line_sensors 读取失败：", e)

    # 2. 官方库全局函数 read_line_sensors()
    try:
        if _has_func("read_line_sensors"):
            line = _normalize_line_values(globals()["read_line_sensors"]())
            if line is not None:
                return line
    except Exception as e:
        if DEBUG_LINE:
            print("全局 read_line_sensors 读取失败：", e)

    return None


def is_black(v):
    """
    当前项目约定：False=黑线，True=白底。
    这里同时兼容 0=黑线、1=白底。
    """
    return (v is False) or (v == 0)


def line_pattern(line):
    if line is None:
        return "None"
    return "".join("1" if is_black(v) else "0" for v in line)


def any_black(line):
    return line is not None and any(is_black(v) for v in line)


def center_on_line(line):
    """
    中间两个探头任意一个压到黑线，就认为基本回到原线。
    四路顺序：(左外, 左内, 右内, 右外)
    """
    if line is None or len(line) < 4:
        return False
    return is_black(line[1]) or is_black(line[2])


def left_black(line):
    if line is None or len(line) < 4:
        return False
    return is_black(line[0]) or is_black(line[1])


def right_black(line):
    if line is None or len(line) < 4:
        return False
    return is_black(line[2]) or is_black(line[3])


def line_sensor_available():
    line = read_line_sensors_safe()
    if DEBUG_LINE:
        print("循迹传感器测试：", line_pattern(line))
    return line is not None


def fine_align_on_line():
    """
    回到黑线附近后做短时间横向微调：
    - 线在左侧探头：小车中心偏右，向左挪。
    - 线在右侧探头：小车中心偏左，向右挪。
    """
    start = time.time()
    stable = 0

    while time.time() - start < LINE_ALIGN_TIMEOUT:
        line = read_line_sensors_safe()

        if DEBUG_LINE:
            print("回线微调：", line_pattern(line))

        if center_on_line(line):
            stable += 1
            if stable >= LINE_CONFIRM_COUNT:
                stop_all(0.04)
                return True
        else:
            stable = 0

        if left_black(line):
            strafe_with_forward("left", max(8, RETURN_SIDE_SPEED - 5), 0)
        elif right_black(line):
            strafe_with_forward("right", max(8, RETURN_SIDE_SPEED - 5), 0)
        else:
            # 已经看不到黑线，停止微调，让后续循迹接管。
            break

        time.sleep(0.025)

    stop_all(0.04)
    return False


def return_to_line_by_sensor(back_side):
    """
    使用循迹传感器闭环回到原本黑线/直线。
    这是本版最关键修改：回线不是按固定时间，而是“看到线再停”。
    """
    if not line_sensor_available():
        print("没有读到循迹传感器，改用时间补偿回位")
        return False

    print(f"向{back_side}慢速回线，直到中间循迹探头检测到黑线")

    start = time.time()
    stable = 0
    saw_any_line = False

    while time.time() - start < RETURN_TIMEOUT:
        line = read_line_sensors_safe()

        if DEBUG_LINE:
            print("回线检测：", line_pattern(line))

        if any_black(line):
            saw_any_line = True

        if center_on_line(line):
            stable += 1
            if stable >= LINE_CONFIRM_COUNT:
                stop_all(0.05)
                print("已回到原线：中间探头检测到黑线")
                fine_align_on_line()
                return True
        else:
            stable = 0

        # 还没有看到中线，就继续向原线路方向平移。
        strafe_with_forward(back_side, RETURN_SIDE_SPEED, RETURN_FORWARD_SPEED)
        time.sleep(LOOP_DT)

    stop_all(0.05)

    # 超时但曾经擦到线，说明可能冲过或角度偏了，做一次短微调。
    if saw_any_line:
        print("回线超时，但检测到过黑线，尝试微调")
        return fine_align_on_line()

    print("回线超时，未检测到黑线")
    return False


def return_to_line_by_time(back_side):
    """
    无循迹传感器时的退化方案。
    """
    print(f"按时间补偿向{back_side}平移回原路线：{SIDE_BACK_TIME:.2f}s")
    timed_action(lambda: strafe_robot(back_side, SIDE_SPEED), SIDE_BACK_TIME, stop_after=True)

def line_follow_step(speed=None):
    """
    简单循迹前进。
    作用不是高速巡线，而是在绕桩后把车慢慢压回原直线，防止纯 forward_robot() 按偏掉的车头方向斜着走。

    四路顺序按本文件约定：
        (左外, 左内, 右内, 右外)
    False/0 表示黑线，True/1 表示白底。
    """
    if not USE_LINE_FOLLOW:
        forward_robot(LINE_SPEED)
        return False

    line = read_line_sensors_safe()
    if line is None:
        forward_robot(LINE_SPEED)
        return False

    b0 = is_black(line[0])
    b1 = is_black(line[1])
    b2 = is_black(line[2])
    b3 = is_black(line[3])

    speed = int(speed or LINE_FOLLOW_SPEED)
    slow_speed = max(10, speed - 4)

    if DEBUG_LINE:
        print("循迹前进：", line_pattern(line))

    # 中间探头压线：保持直行。
    if b1 and b2:
        drive_robot(speed, 0, 0)
    elif b1:
        # 黑线在车体左内侧，车身略偏右，向左小幅修正。
        drive_robot(speed, -LINE_CORRECT_SIDE_SPEED, -max(2, LINE_CORRECT_TURN_SPEED // 2))
    elif b2:
        # 黑线在车体右内侧，车身略偏左，向右小幅修正。
        drive_robot(speed, LINE_CORRECT_SIDE_SPEED, max(2, LINE_CORRECT_TURN_SPEED // 2))
    elif b0:
        # 黑线只在左外侧：偏得较多，左移并轻微左转。
        drive_robot(slow_speed, -LINE_CORRECT_SIDE_SPEED, -LINE_CORRECT_TURN_SPEED)
    elif b3:
        # 黑线只在右外侧：偏得较多，右移并轻微右转。
        drive_robot(slow_speed, LINE_CORRECT_SIDE_SPEED, LINE_CORRECT_TURN_SPEED)
    else:
        # 暂时没看到线，不要猛转，保持低速向前，避免被斜线/反光误导。
        drive_robot(max(10, speed - 5), 0, 0)

    return True


def settle_on_line_after_avoid():
    """
    绕桩后不要立刻纯直行，而是低速循迹稳定一小段。
    视频中的问题是：小车约 9 秒时已经压回黑线，但车头没有完全回正，随后按偏航方向跑走。
    """
    if AFTER_AVOID_ALIGN_TIME <= 0:
        return

    print(f"绕桩后低速贴线稳定 {AFTER_AVOID_ALIGN_TIME:.2f}s")
    end = time.time() + AFTER_AVOID_ALIGN_TIME
    while time.time() < end:
        line_follow_step(max(12, LINE_FOLLOW_SPEED - 2))
        time.sleep(LOOP_DT)
    stop_all(0.04)



# ==========================================================
# 偏移之后自动回正模块
# ==========================================================

def clamp(value, low, high):
    return max(low, min(high, value))


def sign(value):
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def get_line_error(line):
    """
    根据四路循迹传感器计算黑线相对车体中心的位置误差。

    四路顺序：
        左外、左内、右内、右外

    返回值含义：
        None：没有任何探头检测到黑线
        负数：黑线在车体左侧，说明车身偏右，需要向左回正
        正数：黑线在车体右侧，说明车身偏左，需要向右回正
        接近 0：黑线在车体中间，基本回正
    """
    if line is None or len(line) < 4:
        return None

    weights = (-3.0, -1.0, 1.0, 3.0)
    black_positions = [weights[i] for i, v in enumerate(line[:4]) if is_black(v)]

    if not black_positions:
        return None

    return sum(black_positions) / len(black_positions)


def auto_recenter_after_offset(side, back_side, returned_by_sensor=False):
    """
    偏移之后自动回正。

    使用场景：
        小车先向 side 方向侧移绕障，再向 back_side 回线。
        即使已经检测到黑线，车体中心和车头方向仍可能不正，
        所以这里再进行一段短时间闭环修正。

    修正逻辑：
        1. 如果看不到线，继续向 back_side 小速度找线。
        2. 如果线在左边，说明车偏右，向左平移并轻微左转。
        3. 如果线在右边，说明车偏左，向右平移并轻微右转。
        4. 连续多次检测到中心区域压线后，判定回正完成。
    """
    if not USE_AUTO_RECENTER:
        return returned_by_sensor

    print("启动偏移后自动回正模块")

    if not line_sensor_available():
        fallback_time = max(0.0, SIDE_OUT_TIME * RECENTER_TIME_FALLBACK_RATIO)
        if fallback_time <= 0.001:
            print("未读取到循迹传感器，且时间兜底补偿为 0，跳过自动回正")
            return False

        fallback_speed = max(8, min(RETURN_SIDE_SPEED, RECENTER_NO_LINE_SPEED))
        print(f"未读取到循迹传感器，执行短时间反向补偿：向{back_side} {fallback_time:.2f}s")
        timed_action(lambda: strafe_robot(back_side, fallback_speed), fallback_time, stop_after=True)
        return False

    start = time.time()
    stable = 0
    saw_line = False

    while time.time() - start < RECENTER_TIMEOUT:
        line = read_line_sensors_safe()
        err = get_line_error(line)

        if DEBUG_LINE:
            print("自动回正检测：", line_pattern(line), "error=", err)

        if err is None:
            stable = 0
            # 看不到线时，不要原地乱转，沿回线方向慢速横移寻找黑线。
            strafe_with_forward(back_side, RECENTER_NO_LINE_SPEED, 0)
            time.sleep(LOOP_DT)
            continue

        saw_line = True

        # err < 0：线在左边，小车偏右，lateral 应为负，向左平移；
        # err > 0：线在右边，小车偏左，lateral 应为正，向右平移。
        lateral = int(err * RECENTER_SIDE_KP)
        lateral = clamp(lateral, -RECENTER_SIDE_MAX, RECENTER_SIDE_MAX)

        if abs(err) > RECENTER_ERROR_DEADZONE and abs(lateral) < RECENTER_SIDE_MIN:
            lateral = sign(err) * RECENTER_SIDE_MIN

        turn = int(err * RECENTER_TURN_KP)
        turn = clamp(turn, -RECENTER_TURN_MAX, RECENTER_TURN_MAX)

        centered = center_on_line(line) and abs(err) <= RECENTER_ERROR_DEADZONE

        if centered:
            stable += 1
            # 已经在中线附近时继续低速向前，让车头自然顺线回正。
            drive_robot(RECENTER_FORWARD_SPEED, 0, 0)
            if stable >= RECENTER_STABLE_COUNT:
                stop_all(0.05)
                print("偏移后自动回正完成")
                return True
        else:
            stable = 0
            drive_robot(RECENTER_FORWARD_SPEED, lateral, turn)

        time.sleep(LOOP_DT)

    stop_all(0.05)

    if saw_line:
        print("自动回正超时：已检测到黑线，后续交给低速循迹继续修正")
        return True

    print("自动回正超时：未检测到黑线")
    return False


# ==========================================================
# 绕桩避障动作
# ==========================================================

def opposite_side(side):
    return "left" if side == "right" else "right"


def choose_avoid_side(avoid_count):
    if AVOID_SIDE == "left":
        return "left"
    if AVOID_SIDE == "alternate":
        return "right" if avoid_count % 2 == 0 else "left"
    return "right"


def apply_heading_trim(side):
    """
    纯时间绕桩和平移容易导致车头偏航。
    这里提供一个可选的方向修正，默认不开。
    """
    trim_time = TRIM_AFTER_RIGHT if side == "right" else TRIM_AFTER_LEFT

    if abs(trim_time) < 0.001:
        return

    turn = TRIM_TURN_SPEED if trim_time > 0 else -TRIM_TURN_SPEED
    print(f"执行车头微调：{'右转' if turn > 0 else '左转'} {abs(trim_time):.2f}s")
    timed_action(lambda: rotate_robot(turn), abs(trim_time), stop_after=True)


def avoid_pole(side):
    """
    执行一次绕桩避障。

    side:
        "right" = 向右侧移绕桩
        "left"  = 向左侧移绕桩
    """
    back_side = opposite_side(side)

    print(f"开始绕桩：向{side}侧绕开")

    # 1. 停车
    stop_all(0.08)

    # 2. 如果离障碍太近，先后退一点
    d = read_distance_mm()
    if 0 < d < TOO_CLOSE_MM:
        print(f"距离太近 {d} mm，先后退")
        timed_action(lambda: backward_robot(BACK_SPEED), BACK_TIME, stop_after=True)

    # 3. 侧移离开原路线
    print(f"向{side}平移，绕开障碍")
    timed_action(lambda: strafe_robot(side, SIDE_SPEED), SIDE_OUT_TIME, stop_after=True)

    # 4. 前进越过障碍
    print("前进越过障碍")
    timed_action(lambda: forward_robot(FORWARD_SPEED), PASS_TIME, stop_after=True)

    # 5. 回到原路线：优先靠循迹传感器闭环，失败再时间补偿
    returned = False

    if RETURN_MODE == "line":
        returned = return_to_line_by_sensor(back_side)

    if not returned:
        return_to_line_by_time(back_side)

        # 时间回位后可以再额外找线一小段，防止只差一点点。
        if RETURN_MODE == "line" and LINE_SEARCH_EXTRA_TIME > 0 and line_sensor_available():
            print("时间回位后继续短距离找线")
            end = time.time() + LINE_SEARCH_EXTRA_TIME
            stable = 0
            while time.time() < end:
                line = read_line_sensors_safe()
                if center_on_line(line):
                    stable += 1
                    if stable >= LINE_CONFIRM_COUNT:
                        stop_all(0.04)
                        returned = True
                        break
                else:
                    stable = 0
                strafe_with_forward(back_side, max(8, RETURN_SIDE_SPEED - 4), 0)
                time.sleep(LOOP_DT)
            stop_all(0.04)

    # 6. 可选车头微调。默认关闭；如果存在固定偏航，可以通过环境变量开启。
    apply_heading_trim(side)

    # 7. 偏移之后自动回正：这是本版新增模块。
    #    它不是简单多平移一段时间，而是根据黑线位置误差闭环修正横向偏移和车头角度。
    auto_recenter_after_offset(side, back_side, returned_by_sensor=returned)

    # 8. 绕桩后先低速贴线稳定，不能再直接纯直行，否则会沿偏掉的车头方向斜着跑。
    settle_on_line_after_avoid()

    # 9. 兼容保留：一般保持 0；如果确实需要额外直行，再手动设置环境变量。
    if STABILIZE_TIME > 0:
        print("额外稳定直行")
        timed_action(lambda: forward_robot(min(LINE_SPEED, 14)), STABILIZE_TIME, stop_after=True)

    print("绕桩完成")


# ==========================================================
# 主程序
# ==========================================================

def main():
    print("RASPBOT-V2 连续绕桩避障启动：偏移后自动回正版")
    print(f"触发距离：{OBSTACLE_TRIGGER_MM} mm")
    print(f"绕桩方向：{AVOID_SIDE}")
    print(f"回线模式：{RETURN_MODE}")
    print(f"偏移后自动回正：{'开启' if USE_AUTO_RECENTER else '关闭'}")
    print("按 Ctrl+C 退出")

    enable_ultrasonic(True)
    enable_line_sensor(True)

    hit_count = 0
    avoid_count = 0
    cooldown_until = 0.0
    last_print = 0.0

    try:
        while True:
            d = read_distance_mm()

            if DEBUG_DISTANCE and time.time() - last_print > 0.5:
                print(f"当前超声波距离：{d} mm")
                last_print = time.time()

            now = time.time()

            if now < cooldown_until:
                line_follow_step(LINE_FOLLOW_SPEED)
                time.sleep(LOOP_DT)
                continue

            if 0 < d <= OBSTACLE_TRIGGER_MM:
                hit_count += 1
            else:
                hit_count = max(0, hit_count - 1)

            if hit_count >= CONFIRM_COUNT:
                print(f"检测到障碍物，距离：{d} mm")
                hit_count = 0

                side = choose_avoid_side(avoid_count)
                avoid_pole(side)

                avoid_count += 1
                cooldown_until = time.time() + COOLDOWN_TIME
            else:
                line_follow_step(LINE_FOLLOW_SPEED)

            time.sleep(LOOP_DT)

    except KeyboardInterrupt:
        print("用户停止")

    finally:
        enable_ultrasonic(False)
        try:
            enable_line_sensor(False)
        except Exception:
            pass
        stop_all(0.1)
        print("连续绕桩避障结束")


if __name__ == "__main__":
    main()
