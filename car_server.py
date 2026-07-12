# -*- coding: UTF-8 -*-
"""RASPBOT-V2 小车服务端 —— 在树莓派上运行，接收 PC Web 控制台的指令。

基于参考代码的 PID 循迹 + 舵机扫描避障 + 传感器回线 + 自动回正 + 低速贴线。

启动方式：
    python car_server.py
    或指定端口：RASPBOT_CAR_PORT=5001 python car_server.py

PC 端设置 .env：
    RASPBOT_CAR_URL=http://<小车IP>:5001
    RASPBOT_HARDWARE_MODE=remote
"""

from __future__ import annotations

import os
import sys
import time
import threading

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, request

app = Flask(__name__)

# ---- 硬件 ----
car = None
_car_lock = threading.Lock()
_line_follow_thread = None
_line_follow_stop = threading.Event()
_line_follow_pause = threading.Event()

HOST = os.environ.get("RASPBOT_CAR_HOST", "0.0.0.0")
PORT = int(os.environ.get("RASPBOT_CAR_PORT", "5001"))


def init_hardware():
    global car
    try:
        from raspbot_v2_lib import Raspbot
        car = Raspbot()
        car.require_chassis()
        print(f"[小车] 底盘已连接：{car.backend_name()}")
        # 初始化传感器（参考代码标准流程）
        car.Ctrl_IR_Switch(1)        # 打开红外循迹传感器
        car.Ctrl_Ulatist_Switch(1)   # 打开超声波传感器
        time.sleep(0.15)             # 等待传感器上电稳定
        car.Ctrl_Servo(1, 90)        # 舵机回中
        car.Ctrl_BEEP_Switch(0)      # 关闭蜂鸣器
        print("[小车] 传感器已初始化（红外+超声波+舵机）")
    except Exception as exc:
        print(f"[小车] 底盘连接失败：{exc}")
        car = None


def require_car():
    if car is None:
        return jsonify({"success": False, "message": "底盘未连接"})
    return None


# ======================== 状态接口 ========================
@app.get("/api/status")
def api_status():
    dist = 0
    line_bits = [0, 0, 0, 0]
    line_state = "UNKNOWN"
    if car is not None:
        with _car_lock:
            try:
                mm = car.read_ultrasonic_mm()
                dist = round(mm / 10.0, 1) if mm > 0 else 0
            except Exception:
                pass
            try:
                raw = car.read_line_sensors()
                line_bits = [
                    0 if raw.get("left_1", True) else 1,
                    0 if raw.get("left_2", True) else 1,
                    0 if raw.get("right_1", True) else 1,
                    0 if raw.get("right_2", True) else 1,
                ]
                bits = tuple(line_bits)
                if bits == (0, 0, 0, 0):
                    line_state = "CROSS"
                elif bits == (1, 1, 1, 1):
                    line_state = "LOST"
                elif 0 in bits[:2] and 0 in bits[2:]:
                    line_state = "CENTER"
                elif 0 in bits[:2]:
                    line_state = "LEFT"
                elif 0 in bits[2:]:
                    line_state = "RIGHT"
                else:
                    line_state = "LOST"
            except Exception:
                pass
    return jsonify({
        "car_ok": car is not None,
        "distance_cm": dist,
        "line_bits": line_bits,
        "line_state": line_state,
        "line_follow_running": _line_follow_thread is not None and _line_follow_thread.is_alive(),
        "line_follow_paused": _line_follow_pause.is_set(),
    })


# ======================== 手动控制 ========================
@app.post("/api/control/move")
def api_move():
    err = require_car()
    if err: return err
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "stop")
    speed = int(payload.get("speed", 40))
    s = max(0, min(80, speed))
    mapping = {
        "forward": (s, 0, 0), "backward": (-s, 0, 0),
        "left": (0, -s, 0), "right": (0, s, 0),
        "rotate_left": (0, 0, -max(18, s)), "rotate_right": (0, 0, max(18, s)),
        "stop": (0, 0, 0),
    }
    try:
        f, l, t = mapping.get(action, (0, 0, 0))
        with _car_lock:
            car.Ctrl_Car(f, l, t)
        return jsonify({"success": True, "action": action, "speed": speed})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.post("/api/control/stop")
def api_stop():
    err = require_car()
    if err: return err
    try:
        with _car_lock:
            car.Ctrl_Car(0, 0, 0)
        return jsonify({"success": True, "message": "已停车"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.post("/api/control/emergency_stop")
def api_emergency_stop():
    if car is None:
        return jsonify({"success": False, "message": "底盘未连接"})
    try:
        car.emergency_stop(repeats=10, interval=0.03)
        car.Ctrl_BEEP_Switch(1)
        car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
        return jsonify({"success": True, "message": "急停已执行"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


# ======================== 声光反馈 ========================
@app.post("/api/feedback/rgb")
def api_rgb():
    if car is None:
        return jsonify({"success": False, "message": "底盘未连接"})
    payload = request.get_json(silent=True) or {}
    color = payload.get("color", "off")
    colors = {
        "blue": (0, 0, 120), "yellow": (120, 120, 0),
        "red": (160, 0, 0), "green": (0, 120, 0), "off": (0, 0, 0),
    }
    r, g, b = colors.get(color, (0, 0, 80))
    try:
        car.Ctrl_WQ2812_brightness_ALL(r, g, b)
        return jsonify({"success": True, "color": color})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.post("/api/feedback/beep")
def api_beep():
    if car is None:
        return jsonify({"success": False, "message": "底盘未连接"})
    payload = request.get_json(silent=True) or {}
    on = bool(payload.get("on", False))
    try:
        car.Ctrl_BEEP_Switch(1 if on else 0)
        return jsonify({"success": True, "buzzer": "on" if on else "off"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


# ======================== 语音播报 ========================
@app.post("/api/speak")
def api_speak():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "message": "文本为空"})
    try:
        from speech_output import Speaker
        Speaker().speak(text, wait=False)
        return jsonify({"success": True, "message": "播报中"})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


# ======================== 循迹 + 避障 参数 ========================
LF_SPEED = int(os.environ.get("RASPBOT_LINE_SPEED", "32"))
LF_SMALL_TURN = int(os.environ.get("RASPBOT_SMALL_TURN_SPEED", "20"))
LF_SPIN = int(os.environ.get("RASPBOT_SPIN_SPEED", "36"))
LF_BACK = int(os.environ.get("RASPBOT_BACK_SPEED", "25"))
LF_OBS_CM = float(os.environ.get("RASPBOT_OBSTACLE_DISTANCE_CM", "35"))
LF_CLEAR_CM = float(os.environ.get("RASPBOT_CLEAR_DISTANCE_CM", "30"))
LF_KP = float(os.environ.get("RASPBOT_LINE_KP", "0.45"))
LF_KI = float(os.environ.get("RASPBOT_LINE_KI", "0.02"))
LF_KD = float(os.environ.get("RASPBOT_LINE_KD", "0.08"))
LF_MAX_TURN = int(os.environ.get("RASPBOT_MAX_TURN", "35"))

# PID 全局状态
_lf_integral = 0.0
_lf_last_error = 0.0
_trigger_avoidance = False
_last_lf_error = ""  # 最近一次循迹异常信息


def _pid_reset():
    global _lf_integral, _lf_last_error
    _lf_integral = 0.0
    _lf_last_error = 0.0


# ==================== 传感器读取 ====================
def _read_bits():
    raw = car.read_line_sensors()
    bits = [
        0 if raw.get("left_1", True) else 1,
        0 if raw.get("left_2", True) else 1,
        0 if raw.get("right_1", True) else 1,
        0 if raw.get("right_2", True) else 1,
    ]
    return bits, raw


def _read_raw():
    """(l1,l2,r1,r2) True=黑线"""
    try:
        raw = car.read_line_sensors()
        return (not raw.get("left_1", True), not raw.get("left_2", True),
                not raw.get("right_1", True), not raw.get("right_2", True))
    except Exception:
        return None


def _read_distance_cm(samples: int = 3) -> float:
    """超声波多次采样取中位数（厘米）。

    直接读毫米寄存器再 /10，不依赖 read_ultrasonic_cm() 的 /10 修复。
    """
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


# ==================== PID 循迹 ====================
def _compute_line_error(bits):
    """加权误差计算。负=偏左需右转，正=偏右需左转，None=脱线（全白或全黑）。"""
    l1, l2, r1, r2 = [float(b) for b in bits]
    total = l1 + l2 + r1 + r2
    if total == 0 or total == 4:
        return None
    weighted = -1.5 * l1 - 0.5 * l2 + 0.5 * r1 + 1.5 * r2
    return max(-1.0, min(1.0, weighted / (total * 1.5)))


# ==================== 避障（参考代码融合版） ====================
def _avoid_obstacle(speed: int = 30):
    """舵机扫描避障 + 传感器回线 + 自动回正 + 低速贴线。"""
    global _lf_integral, _lf_last_error

    # ---- 参数 ----
    SIDE_SPEED = 24;       FWD_SPEED = 18;      BACK_SPD = 18
    BACK_TIME = 0.15;      SIDE_OUT = 0.90;      PASS_TIME = 0.60
    TOO_CLOSE = 14.0
    RET_SIDE = 15;         RET_FWD = 4;          RET_TO = 3.20
    RC_TO = 1.60;          RC_FWD = 10
    RC_SIDE_MAX = 12;      RC_SIDE_MIN = 5;      RC_SIDE_KP = 4.0
    RC_TURN_MAX = 7;       RC_TURN_KP = 2.0;     RC_DZ = 1.05
    ALIGN_T = 0.80;        DT = 0.03

    _avoid_count = getattr(_avoid_obstacle, "_count", 0) + 1
    _avoid_obstacle._count = _avoid_count
    prefer_right = (_avoid_count % 2 == 0)

    def _stop(d=0.06):
        car.Ctrl_Car(0, 0, 0); time.sleep(d)

    def _chk():
        if _line_follow_stop.is_set():
            car.Ctrl_Car(0, 0, 0); return True
        return False

    def _dist():
        try: return float(car.read_ultrasonic_cm())
        except: return 0.0

    def _is_b(v): return v is True

    def _center(line):
        return line is not None and len(line) >= 4 and (_is_b(line[1]) or _is_b(line[2]))

    def _any_b(line):
        return line is not None and any(_is_b(v) for v in line)

    def _err(line):
        if line is None or len(line) < 4: return None
        w = (-3.0, -1.0, 1.0, 3.0)
        pos = [w[i] for i, v in enumerate(line[:4]) if _is_b(v)]
        return sum(pos) / len(pos) if pos else None

    def _strafe(d, spd):
        car.Ctrl_Car(0, spd if d == "right" else -spd, 0)

    def _strafe_fwd(d, s_spd, f_spd=0):
        car.Ctrl_Car(f_spd, s_spd if d == "right" else -s_spd, 0)

    print(f"[小车] 绕障开始 prefer_right={prefer_right}")

    # ======== 1. 停车 + 必要时后退 ========
    car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
    car.emergency_stop(repeats=5, interval=0.02)
    _stop(0.08)
    if _chk(): return

    d = _dist()
    if 0 < d < TOO_CLOSE:
        print(f"[小车] 距离太近 {d:.0f}cm，先后退")
        car.Ctrl_Car(-BACK_SPD, 0, 0); time.sleep(BACK_TIME); _stop()
        if _chk(): return

    # ======== 2. 舵机扫描 ========
    car.Ctrl_Servo(1, 0); time.sleep(0.55); right_d = _dist()
    car.Ctrl_Servo(1, 180); time.sleep(0.55); left_d = _dist()
    car.Ctrl_Servo(1, 90); time.sleep(0.35); front_d = _dist()
    print(f"[小车] 测距：左={left_d:.1f} 前={front_d:.1f} 右={right_d:.1f}")

    left_ok = left_d == 0 or left_d >= LF_CLEAR_CM
    right_ok = right_d == 0 or right_d >= LF_CLEAR_CM
    front_ok = 0 < front_d <= LF_OBS_CM

    # ======== 3. 选定方向 + 绕行 ========
    if not left_ok and not right_ok and front_ok:
        print("[小车] 三面较近，掉头")
        car.Ctrl_WQ2812_brightness_ALL(120, 0, 120)
        car.Ctrl_Car(0, 0, LF_SPIN); time.sleep(0.65); _stop()
        if _chk(): return
        _pid_reset(); car.Ctrl_WQ2812_brightness_ALL(0, 120, 0); return

    if right_ok and (prefer_right or not left_ok):
        side = "right"
    elif left_ok:
        side = "left"
    else:
        print("[小车] 测距不理想，右转微调")
        car.Ctrl_Car(0, 0, LF_SPIN); time.sleep(0.35); _stop()
        _pid_reset(); car.Ctrl_WQ2812_brightness_ALL(0, 120, 0); return

    back_side = "left" if side == "right" else "right"
    car.Ctrl_WQ2812_brightness_ALL(0, 0, 120)

    print(f"[小车] 向{side}侧移 {SIDE_OUT}s")
    _strafe(side, SIDE_SPEED); time.sleep(SIDE_OUT); _stop()
    if _chk(): return

    print(f"[小车] 前进越过 {PASS_TIME}s")
    car.Ctrl_Car(FWD_SPEED, 0, 0); time.sleep(PASS_TIME); _stop()
    if _chk(): return

    # ======== 4. 传感器回线（看到黑线先降速，防止冲过） ========
    print(f"[小车] 向{back_side}传感器回线...")
    t0 = time.time(); stable = 0; saw = False; back = False
    cur_side = RET_SIDE; cur_fwd = RET_FWD

    while time.time() - t0 < RET_TO:
        if _chk(): return
        line = _read_raw()
        if _any_b(line):
            if not saw:
                cur_side = max(6, RET_SIDE // 2); cur_fwd = 0
                print(f"[小车] 检测到黑线，降速 side={cur_side}")
            saw = True
        if _center(line):
            stable += 1
            if stable >= 2:
                _stop(0.05); print("[小车] ✓ 已回线"); back = True; break
        else:
            stable = 0
        _strafe_fwd(back_side, cur_side, cur_fwd)
        time.sleep(DT)

    if not back:
        _stop(0.05)
        print("[小车] 回线超时" + ("（擦过线）" if saw else "（未见线）"))

    # ======== 5. 自动回正 ========
    print("[小车] 自动回正...")
    t0 = time.time(); stable = 0; saw = False

    while time.time() - t0 < RC_TO:
        if _chk(): return
        line = _read_raw(); err = _err(line)
        if err is None:
            stable = 0; _strafe_fwd(back_side, RC_SIDE_MAX, 0); time.sleep(DT); continue
        saw = True
        lat = int(err * RC_SIDE_KP)
        lat = max(-RC_SIDE_MAX, min(RC_SIDE_MAX, lat))
        if abs(err) > RC_DZ and abs(lat) < RC_SIDE_MIN:
            lat = (1 if err > 0 else -1) * RC_SIDE_MIN
        turn = max(-RC_TURN_MAX, min(RC_TURN_MAX, int(err * RC_TURN_KP)))
        if _center(line) and abs(err) <= RC_DZ:
            stable += 1; car.Ctrl_Car(RC_FWD, 0, 0)
            if stable >= 6: _stop(0.05); print("[小车] ✓ 回正完成"); break
        else:
            stable = 0; car.Ctrl_Car(RC_FWD, lat, turn)
        time.sleep(DT)
    else:
        _stop(0.05)
        print("[小车] 回正超时" + ("（已见线）" if saw else "（未见线）"))

    # ======== 6. 低速贴线 ========
    print(f"[小车] 贴线稳定 {ALIGN_T}s")
    end = time.time() + ALIGN_T
    while time.time() < end:
        if _chk(): return
        line = _read_raw()
        if line:
            b0, b1, b2, b3 = (_is_b(v) for v in line)
            spd = max(12, speed - 2)
            if b1 and b2:       car.Ctrl_Car(spd, 0, 0)
            elif b1:            car.Ctrl_Car(spd, -9, -3)
            elif b2:            car.Ctrl_Car(spd, 9, 3)
            elif b0:            car.Ctrl_Car(max(10, spd - 4), -9, -6)
            elif b3:            car.Ctrl_Car(max(10, spd - 4), 9, 6)
            else:               car.Ctrl_Car(max(10, spd - 5), 0, 0)
        else:
            car.Ctrl_Car(max(12, speed - 2), 0, 0)
        time.sleep(DT)
    _stop(0.04)

    _pid_reset()
    car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
    print("[小车] 绕障完成")


# ==================== 主循迹线程 ====================
def _run_line_follow(target: str, speed: int):
    global _line_follow_thread, _lf_integral, _lf_last_error, _trigger_avoidance
    _line_follow_stop.clear()
    _line_follow_pause.clear()
    _pid_reset()
    print(f"[小车] 开始循迹 → {target}，速度 {speed}")

    try:
        with _car_lock:
            car.Ctrl_IR_Switch(1)
            car.Ctrl_Ulatist_Switch(1)
            time.sleep(0.15)
            car.Ctrl_Servo(1, 90)
            car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)

        cycle = 0  # 循环计数，隔 N 轮查一次超声波，保持 PID 频率
        while not _line_follow_stop.is_set():
            if _line_follow_pause.is_set():
                with _car_lock:
                    car.Ctrl_Car(0, 0, 0)
                while _line_follow_pause.is_set() and not _line_follow_stop.is_set():
                    time.sleep(0.1)
                if _line_follow_stop.is_set(): break
                _lf_integral = 0.0
                cycle = 0

            with _car_lock:
                # 超声波采样较慢，每 3 轮 PID 查一次，保持巡线精度
                if cycle % 3 == 0:
                    dist_cm = _read_distance_cm()
                else:
                    dist_cm = 0.0  # 跳过本轮超声检测

                manual = _trigger_avoidance
                danger = 0 < dist_cm <= LF_OBS_CM

                if manual or danger:
                    # ===== 先刹停，避免撞上 =====
                    car.emergency_stop(repeats=3, interval=0.02)
                    car.Ctrl_Car(0, 0, 0)

                    if manual:
                        # PC 端手动触发 → 只刹停，不绕障
                        _trigger_avoidance = False
                        car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
                        print("[小车] PC 端手动触发，已刹停")
                        break

                    # 自动检测到障碍 → 先刹停再绕障
                    print(f"[小车] ⚠ 前方 {dist_cm:.0f}cm ≤ {LF_OBS_CM:.0f}cm，刹停 → 绕障")
                    _avoid_obstacle(speed)
                    # 绕障完成后恢复循迹
                    _pid_reset()
                    car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
                    print("[小车] 绕障完成，继续循迹")
                    slp = 0.02
                else:
                    # ===== PID 巡线（参考版本算法）=====
                    bits, _ = _read_bits()
                    error = _compute_line_error(bits)

                    if error is None:
                        # 脱线：向最后已知方向慢转找线
                        search_dir = -1 if _lf_last_error < 0 else 1
                        car.Ctrl_Car(0, 0, 18 * search_dir)
                        _lf_integral = 0.0
                        slp = 0.05
                    else:
                        # PID 控制
                        _lf_integral += error
                        _lf_integral = max(-3.0, min(3.0, _lf_integral))
                        derivative = error - _lf_last_error
                        _lf_last_error = error

                        turn = int(LF_KP * error * LF_MAX_TURN +
                                   LF_KI * _lf_integral * LF_MAX_TURN * 0.3 +
                                   LF_KD * derivative * LF_MAX_TURN * 0.5)
                        turn = max(-LF_MAX_TURN, min(LF_MAX_TURN, turn))
                        car.Ctrl_Car(speed, 0, turn)
                        slp = 0.02
            time.sleep(slp)
            cycle += 1

    except Exception as exc:
        import traceback
        global _last_lf_error
        _last_lf_error = f"{exc}\n{traceback.format_exc()}"
        print(f"[小车] 循迹异常：{exc}")
        traceback.print_exc()
    finally:
        with _car_lock:
            car.Ctrl_Car(0, 0, 0)
        _line_follow_thread = None
        print("[小车] 循迹任务结束")


# ======================== 任务 API ========================
@app.post("/api/task/line_follow")
def api_line_follow():
    global _line_follow_thread
    err = require_car()
    if err: return err
    payload = request.get_json(silent=True) or {}
    target = payload.get("target", "B")
    speed = int(payload.get("speed", 40))
    if _line_follow_thread and _line_follow_thread.is_alive():
        return jsonify({"success": False, "message": "已有循迹任务在运行"})
    _line_follow_thread = threading.Thread(
        target=_run_line_follow, args=(target, speed), daemon=True)
    _line_follow_thread.start()
    return jsonify({"success": True, "message": f"循迹任务已启动 → {target}", "target": target})


@app.post("/api/task/stop")
def api_task_stop():
    global _line_follow_thread
    _line_follow_stop.set()
    _line_follow_pause.clear()
    if car:
        with _car_lock:
            try:
                car.emergency_stop(repeats=10, interval=0.02)
                car.Ctrl_Car(0, 0, 0)
            except Exception: pass
    return jsonify({"success": True, "message": "任务已停止"})


@app.post("/api/task/pause")
def api_task_pause():
    if _line_follow_thread is None or not _line_follow_thread.is_alive():
        return jsonify({"success": False, "message": "没有正在运行的循迹任务"})
    _line_follow_pause.set()
    return jsonify({"success": True, "message": "循迹已暂停"})


@app.post("/api/task/resume")
def api_task_resume():
    if not _line_follow_pause.is_set():
        return jsonify({"success": False, "message": "循迹未在暂停状态"})
    _line_follow_pause.clear()
    return jsonify({"success": True, "message": "循迹已恢复"})


@app.post("/api/test/avoid")
def api_test_avoid():
    global _trigger_avoidance
    if car is None:
        return jsonify({"success": False, "message": "底盘未连接"})
    payload = request.get_json(silent=True) or {}
    speed = int(payload.get("speed", 30))
    if _line_follow_thread and _line_follow_thread.is_alive():
        _trigger_avoidance = True
        return jsonify({"success": True, "message": "绕障信号已发送（循迹线程内执行）"})
    try:
        with _car_lock:
            car.Ctrl_IR_Switch(1); car.Ctrl_Ulatist_Switch(1)
            time.sleep(0.15); car.Ctrl_Servo(1, 90)
            _avoid_obstacle(speed)
        return jsonify({"success": True, "message": "绕障动作已完成"})
    except Exception as exc:
        return jsonify({"success": False, "message": f"绕障失败：{exc}"})


# ======================== 调试 ========================
@app.get("/api/debug/last_error")
def api_debug_error():
    return jsonify({
        "last_lf_error": _last_lf_error or "(无错误)",
        "line_follow_thread_alive": _line_follow_thread is not None and _line_follow_thread.is_alive(),
    })


# ======================== 启动 ========================
if __name__ == "__main__":
    print("=" * 50)
    print("  RASPBOT-V2 小车服务端 (参考代码融合版)")
    print(f"  监听: http://{HOST}:{PORT}")
    print("=" * 50)
    init_hardware()
    print(f"  底盘: {'✓ 已连接' if car else '✗ 未连接'}")
    print()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
