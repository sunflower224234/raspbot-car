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

# 确保当前目录和 scripts/ 在导入路径中（人脸识别模块在 scripts/ 下）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "web"))       # path_planner

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
_last_ir_value = 0xFF  # 缓存红外避障值，避免状态API与循迹线程争抢寄存器

# ---- 人脸识别锁 ----
_face_unlocked = threading.Event()
_face_required = os.environ.get("RASPBOT_FACE_REQUIRED", "0") == "1"

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


def _check_face_unlock():
    """如果启用人脸识别锁，检查是否已解锁。返回 None 表示通过。"""
    if not _face_required:
        return None
    if not _face_unlocked.is_set():
        return jsonify({"success": False, "message": "请先完成人脸识别解锁"})
    return None


# ======================== 人脸识别 API ========================
@app.post("/api/face/recognize")
def api_face_recognize():
    """执行人脸识别。成功返回用户信息，失败返回错误。"""
    payload = request.get_json(silent=True) or {}
    timeout_seconds = float(payload.get("timeout", 15))

    try:
        from face_recognition_only import run_face_recognition_only
    except ImportError as exc:
        return jsonify({
            "success": False,
            "message": f"人脸识别模块导入失败：{exc}。请确保 scripts/face_recognition_only.py 存在且 OpenCV 已安装。"
        })

    try:
        ok = run_face_recognition_only(timeout_seconds=timeout_seconds)
    except Exception as exc:
        return jsonify({"success": False, "message": f"人脸识别异常：{exc}"})

    if ok:
        _face_unlocked.set()
        # 声光反馈：绿灯 + 短鸣一声
        if car is not None:
            try:
                car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
                car.Ctrl_BEEP_Switch(1); time.sleep(0.15)
                car.Ctrl_BEEP_Switch(0)
            except Exception:
                pass
        return jsonify({
            "success": True,
            "user": "authorized_user",
            "message": "人脸识别成功，小车已解锁",
            "face_unlocked": True,
        })
    else:
        _face_unlocked.clear()
        # 声光反馈：红灯 + 蜂鸣三声
        if car is not None:
            try:
                car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
                for _ in range(3):
                    car.Ctrl_BEEP_Switch(1); time.sleep(0.2)
                    car.Ctrl_BEEP_Switch(0); time.sleep(0.15)
            except Exception:
                pass
        return jsonify({
            "success": False,
            "message": "人脸识别失败：未匹配到授权用户",
            "face_unlocked": False,
        })


@app.get("/api/face/status")
def api_face_status():
    """查询人脸解锁状态。"""
    return jsonify({
        "face_required": _face_required,
        "face_unlocked": _face_unlocked.is_set(),
    })


@app.post("/api/face/lock")
def api_face_lock():
    """重新锁定（登出），需要重新人脸识别才能控制小车。"""
    _face_unlocked.clear()
    if car is not None:
        try:
            car.Ctrl_WQ2812_brightness_ALL(0, 0, 80)  # 蓝灯待机
        except Exception:
            pass
    return jsonify({
        "success": True,
        "message": "人脸锁已重新锁定，需要重新识别",
        "face_unlocked": False,
    })


# ======================== 视觉识别 API ========================
@app.post("/api/vision/qr_scan")
def api_qr_scan():
    """使用车载摄像头扫描二维码。"""
    payload = request.get_json(silent=True) or {}
    timeout_seconds = float(payload.get("timeout", 10))

    try:
        import cv2
        camera_index = int(os.environ.get("RASPBOT_CAMERA_INDEX", "0"))
        camera = cv2.VideoCapture(camera_index)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        if not camera.isOpened():
            camera.release()
            return jsonify({"success": False, "qr_value": None,
                            "message": "车载摄像头打开失败"})

        try:
            qr_detector = cv2.QRCodeDetector()
            import time as _time
            deadline = _time.time() + timeout_seconds
            while _time.time() < deadline:
                ret, frame = camera.read()
                if not ret:
                    _time.sleep(0.05)
                    continue
                data, points, _ = qr_detector.detectAndDecode(frame)
                if points is not None and data:
                    # 声光反馈：绿灯闪一下
                    if car is not None:
                        try:
                            car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
                            car.Ctrl_BEEP_Switch(1); _time.sleep(0.1)
                            car.Ctrl_BEEP_Switch(0)
                        except Exception:
                            pass
                    return jsonify({
                        "success": True,
                        "qr_value": data,
                        "message": f"识别到二维码：{data}",
                    })
                _time.sleep(0.05)

            return jsonify({
                "success": False, "qr_value": None,
                "message": f"超时（{timeout_seconds:.0f}秒）未识别到二维码",
            })
        finally:
            camera.release()
    except ImportError:
        return jsonify({"success": False, "qr_value": None,
                        "message": "OpenCV 未安装，无法扫描二维码"})
    except Exception as exc:
        return jsonify({"success": False, "qr_value": None,
                        "message": f"二维码扫描异常：{exc}"})


# ======================== 状态接口 ========================
@app.get("/api/status")
def api_status():
    global _last_ir_value
    dist = 0
    line_bits = [0, 0, 0, 0]
    line_state = "UNKNOWN"
    ir_obstacle = 0xFF
    collision = False
    if car is not None:
        with _car_lock:
            try:
                mm = car.read_ultrasonic_mm()
                dist = round(mm / 10.0, 1) if mm > 0 else 0
            except Exception:
                pass
            try:
                raw = car.read_line_sensors()
                # 循迹: 0=黑线(在线上), 1=白底(离线)
                line_bits = [
                    1 if raw.get("left_1", True) else 0,
                    1 if raw.get("left_2", True) else 0,
                    1 if raw.get("right_1", True) else 0,
                    1 if raw.get("right_2", True) else 0,
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
            try:
                ir_obstacle = car.read_ir_obstacle()
                _last_ir_value = ir_obstacle
            except Exception:
                pass
            try:
                collision = car.read_collision()
            except Exception:
                pass

    # 判断障碍物状态
    obstacle_detected = (0 < dist <= 25) or (0 < ir_obstacle < 0x50)
    running = _line_follow_thread is not None and _line_follow_thread.is_alive()
    if running:
        strategy = "巡线中（超声+红外避障）" if not _line_follow_pause.is_set() else "巡线暂停"
    elif obstacle_detected:
        strategy = "检测到障碍物，等待处理"
    elif car is None:
        strategy = "底盘未连接"
    else:
        strategy = "待机中（超声+红外监测）"

    return jsonify({
        "car_ok": car is not None,
        "distance_cm": dist,
        "line_bits": line_bits,
        "line_state": line_state,
        "ir_obstacle": ir_obstacle,
        "collision": collision,
        "obstacle": obstacle_detected,
        "strategy": strategy,
        "line_follow_running": running,
        "line_follow_paused": _line_follow_pause.is_set(),
        "face_required": _face_required,
        "face_unlocked": _face_unlocked.is_set(),
        "path_nodes": _path_nodes,
        "path_index": _path_index,
        "path_done": _path_done,
    })


# ======================== 手动控制 ========================
@app.post("/api/control/move")
def api_move():
    err = require_car()
    if err: return err
    err = _check_face_unlock()
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


# ======================== 语音监听（小车端麦克风） ========================
@app.post("/api/voice/listen")
def api_voice_listen():
    """使用小车 USB 麦克风录音并识别，返回识别文本。
    如果匹配到命令，直接在车上执行（移动类命令），或返回命令让 Web 端处理。
    """
    payload = request.get_json(silent=True) or {}
    duration = float(payload.get("duration", 3.0))
    execute = bool(payload.get("execute", True))  # 是否直接执行匹配到的命令

    try:
        from voice_input import record_and_recognize
    except ImportError as exc:
        return jsonify({"success": False, "message": f"语音输入模块导入失败：{exc}"})

    text = record_and_recognize(duration=duration)

    if not text:
        return jsonify({
            "success": True,
            "text": "",
            "command": None,
            "message": "未识别到语音内容",
        })

    # 尝试匹配命令关键词
    try:
        from voice_command_device import VoiceRecognizer, DEFAULT_CAR_KEYWORDS
        recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)
        recognizer._backend = "text"
        cmd = recognizer._match_keyword(text)
    except ImportError:
        cmd = None

    result = {
        "success": True,
        "text": text,
        "command": cmd.label if cmd else None,
        "action": cmd.action if cmd else None,
    }

    # 如果匹配到命令且允许执行，在车上直接处理
    if cmd and execute:
        action = cmd.action

        if action == "stop":
            if car is not None:
                with _car_lock:
                    car.emergency_stop(repeats=10, interval=0.02)
                    car.Ctrl_Car(0, 0, 0)
            result["executed"] = True
            result["message"] = f"语音命令「{cmd.label}」→ 紧急停车已执行"

        elif action in ("route_delivery", "line_follow"):
            # 提取目标点
            target = "B"
            for pt in ["A", "B", "C"]:
                if pt in text.upper():
                    target = pt
                    break
            from path_planner import astar
            plan = astar(start="S", target=target, blocked=[])
            path = plan.get("path", [target]) if plan.get("success") else [target]
            global _line_follow_thread
            if _line_follow_thread and _line_follow_thread.is_alive():
                result["executed"] = False
                result["message"] = "已有循迹任务在运行，请先停止"
            elif car is None:
                result["executed"] = False
                result["message"] = "底盘未连接"
            else:
                _line_follow_thread = threading.Thread(
                    target=_run_line_follow, args=(target, 15, path), daemon=True)
                _line_follow_thread.start()
                result["executed"] = True
                result["message"] = f"语音命令「{cmd.label}」→ 开始前往 {target} 点"

        elif action == "weather":
            from speech_output import Speaker
            Speaker().speak("正在查询天气", wait=False)
            try:
                import requests as _req2
                from datetime import datetime as _dt2
                WEATHER_LAT2 = float(os.environ.get("WEATHER_LAT", "30.2741"))
                WEATHER_LON2 = float(os.environ.get("WEATHER_LON", "120.1551"))
                WEATHER_CITY2 = os.environ.get("WEATHER_CITY_NAME", "杭州")
                _resp2 = _req2.get("https://api.open-meteo.com/v1/forecast", params={
                    "latitude": WEATHER_LAT2, "longitude": WEATHER_LON2,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "forecast_days": 1, "timezone": "auto",
                }, timeout=10)
                _resp2.raise_for_status()
                _d2 = _resp2.json()["current"]
                _c2 = {0:"晴",1:"大部晴朗",2:"局部多云",3:"阴",45:"有雾",51:"小毛毛雨",53:"中等毛毛雨",55:"大毛毛雨",61:"小雨",63:"中雨",65:"大雨",71:"小雪",73:"中雪",75:"大雪",80:"小阵雨",81:"中等阵雨",82:"强阵雨",95:"雷暴",96:"雷暴伴小冰雹",99:"雷暴伴大冰雹"}
                _w2 = _c2.get(_d2.get("weather_code",0), "未知")
                weather_text = f"小主人好，今天是{_dt2.now().strftime('%Y年%m月%d日')}。{WEATHER_CITY2}当前天气{_w2}，气温{_d2.get('temperature_2m')}度，湿度百分之{_d2.get('relative_humidity_2m')}，风速{_d2.get('wind_speed_10m')}公里每小时。"
                Speaker().speak(weather_text, wait=False)
                try:
                    _wh = os.environ.get("DINGTALK_WEBHOOK", "")
                    if _wh:
                        _req2.post(_wh, json={"msgtype":"text","text":{"content":"【小车】"+weather_text}}, timeout=5)
                except Exception: pass
                result["executed"] = True
                result["message"] = f"天气播报：{weather_text}"
                result["weather"] = weather_text
            except Exception as _exc2:
                result["executed"] = False
                result["message"] = f"天气查询失败：{_exc2}"
                Speaker().speak("天气查询失败", wait=False)

        elif action == "face_recognition":
            # 触发车载人脸识别
            try:
                from face_recognition_only import run_face_recognition_only
                ok = run_face_recognition_only(timeout_seconds=10)
                if ok:
                    _face_unlocked.set()
                    result["message"] = "人脸识别成功，小车已解锁"
                else:
                    _face_unlocked.clear()
                    result["message"] = "人脸识别失败，未匹配到授权用户"
                result["executed"] = True
            except Exception as exc:
                result["executed"] = False
                result["message"] = f"人脸识别异常：{exc}"

        elif action == "wake":
            from speech_output import Speaker
            Speaker().speak("我在，请吩咐", wait=False)
            result["executed"] = True
            result["message"] = "小车已唤醒"

        else:
            result["executed"] = False
            result["message"] = f"命令「{cmd.label}」已识别，交由 Web 端处理"
    else:
        result["executed"] = False
        result["message"] = f"识别文本：「{text}」" + ("（未匹配到命令）" if not cmd else "")

    return jsonify(result)


# ======================== 唤醒词监听（Siri 模式） ========================
_wake_listener = None


def _get_wake_listener():
    global _wake_listener
    if _wake_listener is None:
        from voice_wake import WakeListener
        _wake_listener = WakeListener()
    return _wake_listener


@app.post("/api/voice/wake/start")
def api_wake_start():
    """启动后台唤醒词监听。小车持续监听，检测到唤醒词后自动录音识别命令。"""
    global _wake_thread
    wl = _get_wake_listener()
    wl.start()

    # 启动命令处理线程
    if _wake_thread is None or not _wake_thread.is_alive():
        _wake_thread = threading.Thread(target=_wake_command_loop, daemon=True)
        _wake_thread.start()

    return jsonify({"success": True, "message": "唤醒监听已启动",
                    "wake_words": WAKE_WORDS,
                    "status": wl.status})


@app.post("/api/voice/wake/stop")
def api_wake_stop():
    """停止后台唤醒词监听。"""
    global _wake_listener
    if _wake_listener:
        _wake_listener.stop()
        _wake_listener = None
    return jsonify({"success": True, "message": "唤醒监听已停止"})


@app.get("/api/voice/wake/status")
def api_wake_status():
    """查询唤醒监听状态。"""
    wl = _wake_listener
    return jsonify({
        "running": wl is not None and wl._listening if wl else False,
        "status": wl.status if wl else "stopped",
    })


# 唤醒后命令处理线程
_wake_thread = None
# 需要从顶层导入的变量引用
try:
    from voice_wake import WAKE_WORDS
except ImportError:
    WAKE_WORDS = ["小车", "你好小车"]


def _wake_command_loop():
    """后台线程：等待唤醒词 → 执行命令。"""
    global _last_ir_value
    wl = _get_wake_listener()
    while wl._listening:
        cmd = wl.wait_for_command(timeout=60)
        if not cmd:
            continue

        print(f"[唤醒命令] 收到：{cmd}")

        # 匹配命令并执行
        try:
            from voice_command_device import VoiceRecognizer, DEFAULT_CAR_KEYWORDS
            recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)
            recognizer._backend = "text"
            matched = recognizer._match_keyword(cmd)
        except ImportError:
            matched = None

        if matched is None:
            from speech_output import Speaker
            Speaker().speak("抱歉，没有识别到命令", wait=False)
            continue

        action = matched.action
        print(f"[唤醒命令] 匹配：{matched.label} (action={action})")

        if action == "stop":
            if car is not None:
                with _car_lock:
                    car.emergency_stop(repeats=10, interval=0.02)
                    car.Ctrl_Car(0, 0, 0)
            from speech_output import Speaker
            Speaker().speak("紧急停车已执行", wait=False)

        elif action in ("route_delivery", "line_follow"):
            target = "B"
            for pt in ["A", "B", "C"]:
                if pt in cmd.upper():
                    target = pt
                    break
            from path_planner import astar
            plan = astar(start="S", target=target, blocked=[])
            path = plan.get("path", [target]) if plan.get("success") else [target]
            global _line_follow_thread
            if _line_follow_thread and _line_follow_thread.is_alive():
                from speech_output import Speaker
                Speaker().speak("已有任务在运行，请先停止", wait=False)
            elif car is None:
                from speech_output import Speaker
                Speaker().speak("底盘未连接", wait=False)
            else:
                _line_follow_thread = threading.Thread(
                    target=_run_line_follow, args=(target, 15, path), daemon=True)
                _line_follow_thread.start()
                from speech_output import Speaker
                Speaker().speak(f"收到，正在前往{target}点", wait=False)

        elif action == "weather":
            from speech_output import Speaker
            Speaker().speak("正在查询天气", wait=False)
            try:
                import requests as _req
                from datetime import datetime as _dt
                WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "30.2741"))
                WEATHER_LON = float(os.environ.get("WEATHER_LON", "120.1551"))
                WEATHER_CITY = os.environ.get("WEATHER_CITY_NAME", "杭州")
                _resp = _req.get("https://api.open-meteo.com/v1/forecast", params={
                    "latitude": WEATHER_LAT, "longitude": WEATHER_LON,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "forecast_days": 1, "timezone": "auto",
                }, timeout=10)
                _resp.raise_for_status()
                _data = _resp.json()["current"]
                _codes = {0:"晴",1:"大部晴朗",2:"局部多云",3:"阴",45:"有雾",51:"小毛毛雨",53:"中等毛毛雨",55:"大毛毛雨",61:"小雨",63:"中雨",65:"大雨",71:"小雪",73:"中雪",75:"大雪",80:"小阵雨",81:"中等阵雨",82:"强阵雨",95:"雷暴",96:"雷暴伴小冰雹",99:"雷暴伴大冰雹"}
                _w = _codes.get(_data.get("weather_code",0), "未知")
                _text = f"小主人好，今天是{_dt.now().strftime('%Y年%m月%d日')}。{WEATHER_CITY}当前天气{_w}，气温{_data.get('temperature_2m')}度，湿度百分之{_data.get('relative_humidity_2m')}，风速{_data.get('wind_speed_10m')}公里每小时。"
                print(f"[唤醒命令] 天气：{_text}")
                Speaker().speak(_text, wait=False)
                # 钉钉推送
                try:
                    _webhook = os.environ.get("DINGTALK_WEBHOOK", "")
                    if _webhook:
                        _req.post(_webhook, json={"msgtype":"text","text":{"content":"【小车】"+_text}}, timeout=5)
                except Exception: pass
            except Exception as _exc:
                print(f"[唤醒命令] 天气查询失败：{_exc}")
                Speaker().speak("天气查询失败", wait=False)

        elif action == "face_recognition":
            try:
                from face_recognition_only import run_face_recognition_only
                ok = run_face_recognition_only(timeout_seconds=10)
                from speech_output import Speaker
                if ok:
                    _face_unlocked.set()
                    Speaker().speak("人脸识别成功", wait=False)
                else:
                    _face_unlocked.clear()
                    Speaker().speak("人脸识别失败", wait=False)
            except Exception as exc:
                from speech_output import Speaker
                Speaker().speak(f"人脸识别异常：{exc}", wait=False)

        elif action == "wake":
            from speech_output import Speaker
            Speaker().speak("我在", wait=False)

        elif action == "voice_test":
            from speech_output import Speaker
            Speaker().speak("语音模块运行正常", wait=False)

        elif action == "hardware_check":
            from speech_output import Speaker
            # 快速自检
            checks = []
            if car is not None:
                try:
                    dist = car.read_ultrasonic_mm() / 10.0
                    checks.append(f"超声波{dist:.0f}厘米")
                except Exception: checks.append("超声波异常")
                try:
                    bits = car.read_line_sensors()
                    ok = sum(1 for k in ["left_1","left_2","right_1","right_2"] if not bits.get(k, True))
                    checks.append(f"循迹{ok}路正常")
                except Exception: checks.append("循迹异常")
                try:
                    ir = car.read_ir_obstacle()
                    _last_ir_value = ir
                    checks.append(f"红外{ir}")
                except Exception: checks.append("红外异常")
            else:
                checks.append("底盘未连接")
            msg = "硬件自检：" + "，".join(checks)
            print(f"[唤醒命令] {msg}")
            Speaker().speak(msg, wait=False)

        elif action == "exit":
            from speech_output import Speaker
            Speaker().speak("语音控制已退出，再见", wait=False)
            global _wake_listener
            if _wake_listener:
                _wake_listener.stop()
                _wake_listener = None
            return  # 退出线程

        else:
            from speech_output import Speaker
            Speaker().speak(f"收到命令：{matched.label}", wait=False)


# ======================== 循迹 + 避障 参数 ========================
LF_SPEED = int(os.environ.get("RASPBOT_LINE_SPEED", "15"))
LF_SMALL_TURN = int(os.environ.get("RASPBOT_SMALL_TURN_SPEED", "12"))
LF_SPIN = int(os.environ.get("RASPBOT_SPIN_SPEED", "18"))
LF_BACK = int(os.environ.get("RASPBOT_BACK_SPEED", "18"))
LF_OBS_CM = float(os.environ.get("RASPBOT_OBSTACLE_DISTANCE_CM", "50"))
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





# ==================== 避障（转弯绕障） ====================
def _avoid_obstacle(speed: int = 30):
    """停车 → 后退 → 左/右转90° → 前进越过 → 转回 → 直行找回黑线。

    每次左右交替，不依赖舵机扫描，纯转弯绕过。
    """
    global _lf_integral, _lf_last_error

    _avoid_count = getattr(_avoid_obstacle, "_count", 0) + 1
    _avoid_obstacle._count = _avoid_count

    def _stop(d=0.06):
        car.Ctrl_Car(0, 0, 0); time.sleep(d)

    def _chk():
        if _line_follow_stop.is_set():
            car.Ctrl_Car(0, 0, 0); return True
        return False

    def _dist():
        try: return float(car.read_ultrasonic_cm())
        except: return 0.0

    def _spin(clockwise: bool, t: float):
        s = 18 if clockwise else -18
        car.Ctrl_Car(0, 0, s); time.sleep(t); _stop()

    turn_dir = "右" if True else "左"
    print(f"[小车] 绕障开始 → 向{turn_dir}绕行")

    # ======== 1. 停车 + 后退 ========
    car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
    car.emergency_stop(repeats=5, interval=0.02)
    _stop(0.08)
    if _chk(): return

    d = _dist()
    if 0 < d < 14:
        print(f"[小车] 太近 {d:.0f}cm，后退")
        car.Ctrl_Car(-18, 0, 0); time.sleep(0.15); _stop()
        if _chk(): return

    # ======== 2. 矩形绕行：右转→直行→左转→直行→左转→找线 ========
    car.Ctrl_WQ2812_brightness_ALL(0, 0, 120)

    # ① 右转90°，离开线
    _spin(True, 0.55)
    if _chk(): return

    # ② 直行越过障碍物宽度
    car.Ctrl_Car(15, 0, 0); time.sleep(0.60); _stop()
    if _chk(): return

    # ③ 左转90°，沿障碍物侧面行进
    _spin(False, 0.55)
    if _chk(): return

    # ④ 直行越过障碍物长度
    car.Ctrl_Car(15, 0, 0); time.sleep(0.80); _stop()
    if _chk(): return

    # ⑤ 左转90°，车头朝向原线方向
    _spin(False, 0.55)
    if _chk(): return

    # ⑥ 直行找回黑线
    print("[小车] 直行找回黑线...")
    t0 = time.time()
    while time.time() - t0 < 3.0:
        if _chk(): return
        line = _read_raw()
        if line is not None and (line[1] is True or line[2] is True):
            _stop(0.05)
            print("[小车] ✓ 找到黑线")
            break
        car.Ctrl_Car(12, 0, 0)
        time.sleep(0.05)
    else:
        _stop(0.05)
        print("[小车] 找线超时")

    _pid_reset()
    car.Ctrl_Servo(1, 90)
    car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
    print("[小车] 绕障完成")


# 路径导航全局状态
_path_nodes = []       # ["S", "P1", "P3", "B"]
_path_index = 0        # 当前所在节点索引
_path_done = False     # 是否已到达终点


# ==================== 主循迹线程 ====================
def _run_line_follow(target: str, speed: int, path_nodes: list = None):
    global _line_follow_thread, _lf_integral, _lf_last_error, _trigger_avoidance
    global _path_nodes, _path_index, _path_done, _last_ir_value
    _line_follow_stop.clear()
    _line_follow_pause.clear()
    _pid_reset()

    _path_nodes = path_nodes or [target]
    _path_index = 0
    _path_done = False

    # 生成路口导航指令
    try:
        from path_planner import get_path_directions
        directions = get_path_directions(_path_nodes)
    except ImportError:
        directions = [{"node": n, "action": "stop" if i == len(_path_nodes) - 1 else "straight"}
                      for i, n in enumerate(_path_nodes)]

    path_desc = " → ".join(_path_nodes)
    dir_desc = " → ".join(f"{d['node']}({d['action']})" for d in directions)
    print(f"[小车] 开始循迹 → {target}，速度 {speed}")
    print(f"[小车] 路径: {path_desc}")
    print(f"[小车] 指令: {dir_desc}")

    # 路口检测防抖
    _cross_timer = 0.0          # 持续检测到全黑的时间
    _cross_handled = False      # 当前路口是否已处理
    CROSS_CONFIRM_S = 0.12      # 需要持续 120ms 才确认为路口

    try:
        with _car_lock:
            car.Ctrl_IR_Switch(1)
            car.Ctrl_Ulatist_Switch(1)
            time.sleep(0.15)
            car.Ctrl_Servo(1, 90)
            car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)

        cycle = 0
        while not _line_follow_stop.is_set():
            if _line_follow_pause.is_set():
                with _car_lock:
                    car.Ctrl_Car(0, 0, 0)
                while _line_follow_pause.is_set() and not _line_follow_stop.is_set():
                    time.sleep(0.1)
                if _line_follow_stop.is_set(): break
                _lf_integral = 0.0
                _cross_timer = 0.0
                _cross_handled = False
                cycle = 0

            with _car_lock:
                # 超声波采样较慢，每 3 轮 PID 查一次
                if cycle % 3 == 0:
                    dist_cm = _read_distance_cm()
                else:
                    dist_cm = 0.0

                # 碰撞检测
                try:
                    if car.read_collision():
                        car.emergency_stop(repeats=5, interval=0.02)
                        car.Ctrl_Car(0, 0, 0)
                        car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
                        car.Ctrl_BEEP_Switch(1)
                        print("[小车] ⚠ 碰撞传感器触发，紧急停车！")
                        break
                except Exception:
                    pass

                # 红外避障
                ir_danger = False
                try:
                    ir_val = car.read_ir_obstacle()
                    _last_ir_value = ir_val
                    ir_danger = 0 < ir_val < 0x50
                except Exception:
                    pass

                manual = _trigger_avoidance
                danger = (0 < dist_cm <= LF_OBS_CM) or ir_danger

                if manual or danger:
                    car.emergency_stop(repeats=3, interval=0.02)
                    car.Ctrl_Car(0, 0, 0)

                    if manual:
                        _trigger_avoidance = False
                        car.Ctrl_WQ2812_brightness_ALL(160, 0, 0)
                        print("[小车] PC 端手动触发，已刹停")
                        break

                    print(f"[小车] ⚠ 前方 {dist_cm:.0f}cm ≤ {LF_OBS_CM:.0f}cm，刹停 → 绕障")
                    _avoid_obstacle(speed)
                    _pid_reset()
                    _cross_timer = 0.0
                    _cross_handled = False
                    car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
                    print("[小车] 绕障完成，继续循迹")
                    slp = 0.02
                else:
                    # ===== PID 巡线 =====
                    bits, _ = _read_bits()

                    # ---- 路口检测：四路全黑 = 十字路口 ----
                    raw = car.read_line_sensors()
                    all_black_raw = all(not raw.get(k, True) for k in
                                        ["left_1", "left_2", "right_1", "right_2"])

                    if all_black_raw:
                        _cross_timer += 0.02  # PID 周期约 20ms
                    else:
                        _cross_timer = 0.0
                        _cross_handled = False

                    # 路口确认：持续检测到黑线超过阈值
                    if _cross_timer >= CROSS_CONFIRM_S and not _cross_handled:
                        _cross_handled = True
                        _cross_timer = 0.0
                        _path_index = min(_path_index, len(directions) - 1)
                        current_dir = directions[_path_index]
                        action = current_dir["action"]
                        node = current_dir["node"]
                        print(f"[小车] 🚦 路口 #{_path_index}: {node}, 动作={action}")

                        if action == "stop":
                            # 到达终点
                            _path_done = True
                            car.emergency_stop(repeats=5, interval=0.02)
                            car.Ctrl_Car(0, 0, 0)
                            car.Ctrl_WQ2812_brightness_ALL(0, 120, 0)
                            car.Ctrl_BEEP_Switch(1); time.sleep(0.3)
                            car.Ctrl_BEEP_Switch(0); time.sleep(0.1)
                            car.Ctrl_BEEP_Switch(1); time.sleep(0.3)
                            car.Ctrl_BEEP_Switch(0)
                            print(f"[小车] 🏁 到达目标点 {node}，停车！")
                            break

                        # 执行路口转向
                        _execute_cross_turn(action)
                        _path_index += 1
                        _pid_reset()
                        slp = 0.02
                        continue

                    # ---- PID 正常巡线 ----
                    error = _compute_line_error(bits)

                    if error is None:
                        search_dir = -1 if _lf_last_error < 0 else 1
                        car.Ctrl_Car(0, 0, 18 * search_dir)
                        _lf_integral = 0.0
                        slp = 0.05
                    else:
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


def _execute_cross_turn(action: str):
    """在路口执行转向动作。"""
    spd = LF_SMALL_TURN
    duration = 0.45  # 转弯持续时间

    if action == "straight":
        # 直行：短暂加速通过路口
        car.Ctrl_Car(LF_SPEED, 0, 0)
        time.sleep(0.25)
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.05)
        print("[路口] 直行通过")

    elif action == "left":
        car.Ctrl_Car(0, 0, -spd)  # 原地左转
        time.sleep(duration)
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.1)
        print("[路口] ← 左转")

    elif action == "right":
        car.Ctrl_Car(0, 0, spd)   # 原地右转
        time.sleep(duration)
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.1)
        print("[路口] → 右转")

    else:
        print(f"[路口] 未知动作: {action}，直行通过")
        car.Ctrl_Car(LF_SPEED, 0, 0)
        time.sleep(0.2)
        car.Ctrl_Car(0, 0, 0)


# ======================== 任务 API ========================
@app.post("/api/task/line_follow")
def api_line_follow():
    global _line_follow_thread
    err = require_car()
    if err: return err
    err = _check_face_unlock()
    if err: return err
    payload = request.get_json(silent=True) or {}
    target = payload.get("target", "B")
    speed = int(payload.get("speed", 20))
    path_nodes = payload.get("path")  # ["S", "P1", "P3", "B"]，可为 None
    if _line_follow_thread and _line_follow_thread.is_alive():
        return jsonify({"success": False, "message": "已有循迹任务在运行"})
    _line_follow_thread = threading.Thread(
        target=_run_line_follow, args=(target, speed, path_nodes), daemon=True)
    _line_follow_thread.start()
    return jsonify({
        "success": True,
        "message": f"循迹任务已启动 → {target}",
        "target": target,
        "path": path_nodes or [target],
    })



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
