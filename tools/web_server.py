#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""
RASPBOT-V2 iOS18 风格 Web 控制台后端

运行位置：请把本文件放在小车功能代码同一目录下运行。
启动命令：python3 web_server.py
访问地址：http://小车IP:5000

说明：
1. 前端按钮通过 Flask API 启动对应 .py 功能文件。
2. 同一时间只允许一个自动任务运行，避免摄像头、电机、I2C 并发冲突。
3. 紧急停车会写入 /tmp/raspbot_v2_stop.flag，并调用 safety_control.py 发送停车命令。
4. 摄像头预览使用 OpenCV MJPEG 流。若某个功能脚本独占摄像头，预览可能无法同时显示。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from flask import Flask, Response, jsonify, request, send_file

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "web_index.html"
STOP_FLAG_PATH = os.environ.get("RASPBOT_STOP_FLAG", "/tmp/raspbot_v2_stop.flag")
CAMERA_INDEX = int(os.environ.get("RASPBOT_WEB_CAMERA_INDEX", "0"))
WEB_HOST = os.environ.get("RASPBOT_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("RASPBOT_WEB_PORT", "5000"))
CAR_SERVER_URL = os.environ.get("RASPBOT_CAR_URL", "http://127.0.0.1:5001")

app = Flask(__name__)

TASKS: Dict[str, Dict[str, object]] = {
    "face_login": {
        "label": "人脸识别登录",
        "script": "FaceRecognition.py",
        "camera": True,
        "desc": "调用百度云/本地人脸识别入口。",
    },
    "delivery": {
        "label": "外卖送餐",
        "script": "route_delivery.py",
        "camera": True,
        "desc": "二维码识别目的地，执行 A* 路径规划与送餐。",
    },
    "line_follow": {
        "label": "自动循迹",
        "type": "car_api",
        "api_path": "/api/task/line_follow",
        "camera": False,
        "desc": "PID 循迹 + 超声波避障（走 car_server 优化版）。",
    },
    "auto_obstacle": {
        "label": "自动避障",
        "type": "car_api",
        "api_path": "/api/task/line_follow",
        "camera": False,
        "desc": "循迹中自动检测障碍并绕行（走 car_server）。",
    },
    "continuous_obstacle": {
        "label": "连续绕桩避障",
        "type": "car_api",
        "api_path": "/api/test/avoid",
        "camera": False,
        "desc": "麦克纳姆轮横移绕障，回线并自动回正。",
    },
    "weather": {
        "label": "天气查询",
        "script": "weather_speaker.py",
        "camera": False,
        "desc": "查询天气并语音播报/钉钉提示。",
    },
    "voice_control": {
        "label": "语音控制",
        "script": "voice_control_car.py",
        "camera": False,
        "desc": "固定语音命令控制小车功能。",
    },
    "ai_chat": {
        "label": "AI 语音聊天",
        "script": "raspbot_voice_ai.py",
        "camera": False,
        "desc": "腾讯云 ASR + Dify AI + 腾讯云 TTS。",
    },
}

_logs = deque(maxlen=800)
_current_process: Optional[subprocess.Popen] = None
_current_task: Optional[Dict[str, object]] = None
_process_lock = threading.Lock()
_manual_car = None
_manual_lock = threading.Lock()
_car_task_running = False
_car_task_id: Optional[str] = None
_car_lock = threading.Lock()


# ---- car_server HTTP 转发 ----
def _car_post(path: str, body: Optional[dict] = None) -> Optional[dict]:
    """向 car_server 发 HTTP 请求；失败返回 None。"""
    try:
        data = json.dumps(body or {}).encode("utf-8")
        req = Request(f"{CAR_SERVER_URL}{path}", data=data,
                      headers={"Content-Type": "application/json"},
                      method="POST")
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        add_log(f"car_server 请求失败 [{path}]：{exc}")
        return None


def _car_get(path: str) -> Optional[dict]:
    try:
        req = Request(f"{CAR_SERVER_URL}{path}")
        with urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _car_task_is_running() -> bool:
    """通过 car_server /api/status 确认循迹是否还在跑。"""
    data = _car_get("/api/status")
    if data:
        return bool(data.get("line_follow_running"))
    return False


def _now() -> str:
    return time.strftime("%H:%M:%S")


def add_log(message: str) -> None:
    line = f"[{_now()}] {message}"
    print(line, flush=True)
    _logs.append(line)


def _task_running() -> bool:
    global _current_process, _car_task_running
    subprocess_alive = _current_process is not None and _current_process.poll() is None
    if subprocess_alive:
        return True
    if _car_task_running and _car_task_is_running():
        return True
    # car_task 已结束则清理标记
    if _car_task_running and not _car_task_is_running():
        with _car_lock:
            _car_task_running = False
            _car_task_id = None
    return False


def _clear_stop_flag() -> None:
    try:
        os.remove(STOP_FLAG_PATH)
    except FileNotFoundError:
        pass
    except Exception as exc:
        add_log(f"清除停止标志失败：{exc!r}")


def _write_stop_flag() -> None:
    try:
        with open(STOP_FLAG_PATH, "w", encoding="utf-8") as fp:
            fp.write("stop\n")
    except Exception as exc:
        add_log(f"写入停止标志失败：{exc!r}")


def _monitor_process(proc: subprocess.Popen, task_label: str) -> None:
    global _current_process, _current_task
    try:
        if proc.stdout is not None:
            for raw in iter(proc.stdout.readline, ""):
                if not raw:
                    break
                add_log(raw.rstrip())
        code = proc.wait()
        add_log(f"{task_label} 已结束，退出码：{code}")
    except Exception as exc:
        add_log(f"监听 {task_label} 输出失败：{exc!r}")
    finally:
        with _process_lock:
            if _current_process is proc:
                _current_process = None
                _current_task = None


def _terminate_current_process(timeout: float = 2.0) -> None:
    global _current_process, _current_task
    proc = _current_process
    label = (_current_task or {}).get("label", "当前任务")
    if proc is None or proc.poll() is not None:
        _current_process = None
        _current_task = None
        return

    add_log(f"正在停止：{label}")
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except Exception as exc:
        add_log(f"发送终止信号失败：{exc!r}")

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        add_log("普通停止超时，执行强制结束。")
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception as exc:
            add_log(f"强制结束失败：{exc!r}")

    _current_process = None
    _current_task = None


def _emergency_stop_hardware() -> None:
    """直接调用 safety_control.py 的停车逻辑；失败时仍保持 API 正常返回。"""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from safety_control import emergency_stop_once, request_stop

        request_stop()
        emergency_stop_once()
        add_log("已向底盘发送紧急停车命令。")
    except Exception as exc:
        add_log(f"调用 safety_control 紧急停车失败：{exc!r}")


def _get_manual_car():
    global _manual_car
    if _manual_car is None:
        sys.path.insert(0, str(BASE_DIR))
        from raspbot_v2_lib import Raspbot

        _manual_car = Raspbot()
    return _manual_car


@app.route("/")
def index():
    return send_file(INDEX_FILE)


@app.route("/api/tasks", methods=["GET"])
def api_tasks():
    return jsonify({"ok": True, "tasks": TASKS})


@app.route("/api/start/<task_id>", methods=["POST"])
def api_start_task(task_id: str):
    global _current_process, _current_task, _car_task_running, _car_task_id
    if task_id not in TASKS:
        return jsonify({"ok": False, "message": "未知功能按钮。"}), 404

    with _process_lock:
        if _task_running():
            return jsonify({"ok": False, "message": "已有功能正在运行，请先停止。"}), 409

        task = TASKS[task_id]

        # ---- car_api 类型：转发到 car_server ----
        if task.get("type") == "car_api":
            api_path = str(task["api_path"])
            payload = {}
            if task_id == "line_follow":
                payload = {"target": "B", "speed": 32}
            elif task_id == "auto_obstacle":
                payload = {"target": "B", "speed": 32}
            elif task_id == "continuous_obstacle":
                payload = {"speed": 30}

            result = _car_post(api_path, payload)
            if result is None:
                return jsonify({"ok": False, "message": "car_server 无响应，请确认小车服务已启动。"}), 503
            if not result.get("success"):
                return jsonify({"ok": False, "message": result.get("message", "car_server 拒绝启动")}), 500

            with _car_lock:
                _car_task_running = True
                _car_task_id = task_id
            _current_task = {"id": task_id, "label": task["label"], "type": "car_api",
                             "api_path": api_path, "start_time": time.time()}
            add_log(f"已启动 [car_server]：{task['label']}")
            return jsonify({"ok": True, "message": f"已启动：{task['label']}", "task": _current_task})

        # ---- 原有 subprocess 类型 ----
        script = BASE_DIR / str(task["script"])
        if not script.exists():
            return jsonify({"ok": False, "message": f"找不到脚本：{script.name}"}), 404

        _clear_stop_flag()
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        cmd = [sys.executable, "-u", str(script)]
        kwargs = dict(
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if os.name != "nt":
            kwargs["preexec_fn"] = os.setsid

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            add_log(f"启动 {task['label']} 失败：{exc!r}")
            return jsonify({"ok": False, "message": f"启动失败：{exc}"}), 500

        _current_process = proc
        _current_task = {"id": task_id, "label": task["label"], "script": script.name, "start_time": time.time()}
        add_log(f"已启动：{task['label']}（{script.name}）")
        threading.Thread(target=_monitor_process, args=(proc, str(task["label"])), daemon=True).start()
        return jsonify({"ok": True, "message": f"已启动：{task['label']}", "task": _current_task})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _car_task_running, _car_task_id
    with _process_lock:
        _write_stop_flag()
        _terminate_current_process(timeout=2.0)
    # 同时停 car_server 任务
    with _car_lock:
        if _car_task_running:
            _car_post("/api/task/stop")
            _car_post("/api/control/stop")
            _car_task_running = False
            _car_task_id = None
            add_log("已向 car_server 发送停止命令。")
    _emergency_stop_hardware()
    return jsonify({"ok": True, "message": "已执行停止。"})


@app.route("/api/status", methods=["GET"])
def api_status():
    running = _task_running()
    return jsonify({
        "ok": True,
        "running": running,
        "task": _current_task if running else None,
        "logs": list(_logs)[-160:],
        "stop_flag": os.path.exists(STOP_FLAG_PATH),
    })


@app.route("/api/logs/clear", methods=["POST"])
def api_clear_logs():
    _logs.clear()
    add_log("运行日志已清空。")
    return jsonify({"ok": True})


@app.route("/api/manual/move", methods=["POST"])
def api_manual_move():
    if _task_running():
        return jsonify({"ok": False, "message": "自动任务运行中，不能手动控制。请先停止当前任务。"}), 409

    data = request.get_json(force=True, silent=True) or {}
    direction = str(data.get("direction", "stop"))
    speed = int(data.get("speed", 32))
    speed = max(0, min(80, speed))

    mapping = {
        "forward": (speed, 0, 0),
        "backward": (-speed, 0, 0),
        "left": (0, -speed, 0),
        "right": (0, speed, 0),
        "rotate_left": (0, 0, -max(18, speed)),
        "rotate_right": (0, 0, max(18, speed)),
        "stop": (0, 0, 0),
    }
    if direction not in mapping:
        return jsonify({"ok": False, "message": "未知方向。"}), 400

    try:
        with _manual_lock:
            car = _get_manual_car()
            f, l, t = mapping[direction]
            if direction == "stop":
                try:
                    car.stop()
                except Exception:
                    car.Ctrl_Car(0, 0, 0)
            else:
                car.Ctrl_Car(f, l, t)
        return jsonify({"ok": True, "message": f"手动控制：{direction}"})
    except Exception as exc:
        add_log(f"手动控制失败：{exc!r}")
        return jsonify({"ok": False, "message": f"手动控制失败：{exc}"}), 500


@app.route("/api/manual/release", methods=["POST"])
def api_manual_release():
    global _manual_car
    try:
        with _manual_lock:
            if _manual_car is not None:
                try:
                    _manual_car.stop()
                    _manual_car.close()
                finally:
                    _manual_car = None
        return jsonify({"ok": True, "message": "手动控制底盘连接已释放。"})
    except Exception as exc:
        return jsonify({"ok": False, "message": f"释放失败：{exc}"}), 500


@app.route("/video_feed")
def video_feed():
    def generate():
        try:
            import cv2
        except Exception as exc:
            add_log(f"OpenCV 未安装或导入失败：{exc!r}")
            return

        cap = cv2.VideoCapture(CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)
        if not cap.isOpened():
            add_log(f"摄像头打开失败：index={CAMERA_INDEX}")
            return

        add_log(f"摄像头预览已打开：index={CAMERA_INDEX}")
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if not ok:
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                time.sleep(0.03)
        except GeneratorExit:
            pass
        except Exception as exc:
            add_log(f"摄像头推流异常：{exc!r}")
        finally:
            cap.release()
            add_log("摄像头预览已关闭。")

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/health", methods=["GET"])
def api_health():
    missing = []
    for task in TASKS.values():
        if task.get("type") == "car_api":
            continue  # car_api 任务不需要本地脚本
        script = BASE_DIR / str(task.get("script", ""))
        if not script.exists():
            missing.append(script.name)
    return jsonify({
        "ok": True,
        "base_dir": str(BASE_DIR),
        "python": sys.executable,
        "car_server": CAR_SERVER_URL,
        "car_server_ok": _car_get("/api/status") is not None,
        "missing_scripts": sorted(set(missing)),
        "camera_index": CAMERA_INDEX,
    })


if __name__ == "__main__":
    add_log("RASPBOT-V2 Web 控制台启动。")
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
