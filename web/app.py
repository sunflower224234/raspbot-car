from __future__ import annotations

import os
import sys

# 确保所有子目录在导入路径中
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)                              # web/
sys.path.insert(0, os.path.join(_HERE, "services"))     # web/services/
sys.path.insert(0, os.path.join(_ROOT, "car"))          # car/ (speech_output)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))      # scripts/ (face_recognition_only)

from dotenv import load_dotenv
load_dotenv(os.path.join(_HERE, ".env"))

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from task_manager import TaskManager


app = Flask(__name__)
manager = TaskManager()


@app.get("/")
def index():
    return redirect(url_for("dashboard" if manager.state.auth else "login"))


@app.get("/login")
def login():
    return render_template("login.html")


@app.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/status")
def api_status():
    return jsonify(manager.status())


@app.post("/api/auth/face")
def api_face_auth():
    payload = request.get_json(silent=True) or {}
    return jsonify(manager.face_auth(force_fail=bool(payload.get("force_fail"))))


@app.post("/api/auth/logout")
def api_logout():
    return jsonify(manager.logout())


@app.post("/api/control/manual")
def api_manual_control():
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "stop")
    speed = int(payload.get("speed", 40))
    return jsonify(manager.manual(action, speed))


@app.post("/api/control/emergency_stop")
def api_emergency_stop():
    return jsonify(manager.emergency_stop())


@app.post("/api/plan")
def api_plan():
    payload = request.get_json(silent=True) or {}
    target = payload.get("target")
    if not target:
        return jsonify({"success": False, "message": "请先选择目标点"})
    blocked = payload.get("blocked", [])
    return jsonify(manager.plan(target, blocked))


@app.post("/api/task/start")
def api_task_start():
    payload = request.get_json(silent=True) or {}
    target = payload.get("target") or manager.state.target
    if not target:
        return jsonify({"success": False, "message": "请先选择目标点"})
    return jsonify(
        manager.start_task(
            target=target,
            source=payload.get("source", "web"),
            task_type=payload.get("task_type", "delivery"),
        )
    )


@app.post("/api/task/pause")
def api_task_pause():
    return jsonify(manager.pause_task())


@app.post("/api/task/resume")
def api_task_resume():
    return jsonify(manager.resume_task())


@app.post("/api/task/cancel")
def api_task_cancel():
    return jsonify(manager.cancel_task())


@app.post("/api/vision/qr_scan")
def api_qr_scan():
    """二维码到达校验（使用车载摄像头）。"""
    return jsonify(manager.qr_scan())


@app.post("/api/voice/start")
def api_voice_start():
    payload = request.get_json(silent=True) or {}
    target = payload.get("target") or manager.state.target
    if not target:
        return jsonify({"success": False, "message": "请先选择目标点"})
    return jsonify(manager.voice_start(target))


@app.post("/api/voice/stop")
def api_voice_stop():
    return jsonify(manager.voice.stop())


def _process_voice_text(text: str):
    """处理识别到的语音文本：匹配命令并执行。供 simulate 和 recognize 共用。"""
    if not text:
        return {"success": False, "message": "语音文本为空"}

    from voice_command_device import VoiceRecognizer, DEFAULT_CAR_KEYWORDS
    from speech_output import get_speaker

    recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)
    recognizer._backend = "text"
    cmd = recognizer._match_keyword(text)
    speaker = get_speaker()

    if cmd is None:
        manager.logs.append(f"【语音识别】「{text}」→ 未匹配到命令", "warning")
        speaker.speak("抱歉，没有识别到命令", wait=False)
        return {
            "success": False,
            "message": f"未识别到命令，文本：「{text}」",
            "text": text,
            "available_commands": [kw.label for kw in DEFAULT_CAR_KEYWORDS],
            "status": manager.status(),
        }

    manager.logs.append(f"【语音识别】「{text}」→ 匹配命令：{cmd.label}", "success")
    action = cmd.action
    result = {"success": True, "text": text, "command": cmd.label, "action": action}

    if action == "weather":
        try:
            from weather_speaker import fetch_weather_text, send_to_dingtalk
            weather_text = fetch_weather_text()
            send_to_dingtalk(weather_text)
            result["weather"] = weather_text
            result["message"] = f"天气播报：{weather_text}"
            manager.logs.append("天气播报已执行", "success")
            # 让小车播报天气
            manager.robot.send_speak(weather_text)
        except Exception as exc:
            result["message"] = f"天气查询失败：{exc}"
            manager.logs.append(f"天气查询失败：{exc}", "danger")
            manager.robot.send_speak("天气查询失败")

    elif action == "stop":
        estop = manager.emergency_stop()
        result.update(estop)
        result["message"] = "语音命令「停止」→ 紧急停车"
        speaker.speak("紧急停车已执行", wait=False)

    elif action in ("route_delivery", "line_follow"):
        target = manager.state.target or "B"
        for pt in ["A", "B", "C"]:
            if pt in text.upper():
                target = pt
                break
        plan_result = manager.plan(target, manager.state.blocked)
        if plan_result["success"]:
            start_result = manager.start_task(target, source="voice")
            result.update(start_result)
            speaker.speak(f"收到，正在前往{target}点", wait=False)
        else:
            result.update(plan_result)
            speaker.speak("路径规划失败", wait=False)
        result["command"] = f"去 {target} 点"

    elif action == "face_recognition":
        auth = manager.face_auth()
        result.update(auth)
        speaker.speak("人脸识别成功，欢迎使用" if auth["success"] else "人脸识别失败，未授权用户", wait=False)

    elif action == "hardware_check":
        result["message"] = "硬件自检命令已触发（需要真实硬件环境）"
        manager.logs.append("硬件自检：请在树莓派上运行 hardware_check.py", "info")
        speaker.speak("硬件自检需要在树莓派上运行", wait=False)

    elif action == "voice_test":
        result["message"] = "语音测试：模拟模式下语音模块工作正常 ✓"
        manager.logs.append("语音测试通过（模拟模式）", "success")
        speaker.speak("语音测试通过，模块运行正常", wait=False)

    elif action == "wake":
        result["message"] = "小车已唤醒，等待指令"
        manager.logs.append("语音唤醒", "info")
        speaker.speak("我在，请吩咐", wait=False)

    elif action == "exit":
        manager.voice.stop()
        result["message"] = "语音控制已退出"
        result.update(manager.status())
        speaker.speak("语音控制已退出，再见", wait=False)

    else:
        result["message"] = f"命令 {cmd.label} 已识别（action={action}）"
        speaker.speak(f"收到命令：{cmd.label}", wait=False)

    return {**result, "status": manager.status()}


@app.post("/api/voice/recognize")
def api_voice_recognize():
    """浏览器录音上传 → 腾讯云 ASR → 命令匹配 → 执行。
    前端用 MediaRecorder 录制 WAV，以 multipart/form-data 上传。
    字段名: audio
    """
    from voice_command_device import VoiceRecognizer, DEFAULT_CAR_KEYWORDS
    import base64, uuid

    if "audio" not in request.files:
        return jsonify({"success": False, "message": "未收到音频文件，请确认麦克风权限已开启"})

    audio_file = request.files["audio"]
    audio_data = audio_file.read()

    if len(audio_data) < 100:
        return jsonify({"success": False, "message": "录音太短，请重试"})

    # 保存临时 WAV 文件供 ASR 使用
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_data)
        wav_path = tmp.name

    try:
        # 腾讯云 ASR 识别
        from tencentcloud.common import credential
        from tencentcloud.asr.v20190614 import asr_client, models as asr_models
        import json

        secret_id = os.environ.get("TENCENT_SECRET_ID", "")
        secret_key = os.environ.get("TENCENT_SECRET_KEY", "")

        if not secret_id or not secret_key:
            os.unlink(wav_path)
            return jsonify({"success": False, "message": "未配置腾讯云 API 密钥"})

        audio_base64 = base64.b64encode(audio_data).decode("utf-8")

        cred = credential.Credential(secret_id, secret_key)
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        http_profile = HttpProfile()
        http_profile.endpoint = "asr.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client = asr_client.AsrClient(cred, "ap-shanghai", client_profile)

        req = asr_models.SentenceRecognitionRequest()
        params = {
            "ProjectId": 0,
            "SubServiceType": 2,
            "EngSerViceType": "16k_zh",
            "SourceType": 1,
            "VoiceFormat": "wav",
            "Data": audio_base64,
            "DataLen": len(audio_data),
            "UsrAudioKey": str(uuid.uuid4()),
        }
        req.from_json_string(json.dumps(params, ensure_ascii=False))
        resp = client.SentenceRecognition(req)
        text = json.loads(resp.to_json_string()).get("Result", "").strip()

        manager.logs.append(f"【语音识别】ASR 识别结果：「{text}」", "info")

        if not text:
            from speech_output import get_speaker
            get_speaker().speak("没有听清，请再说一次", wait=False)
            return jsonify({"success": False, "message": "未识别到语音内容", "text": "", "status": manager.status()})

        # 走和 simulate 一样的命令处理逻辑
        result = _process_voice_text(text)
        return jsonify(result)

    except Exception as exc:
        manager.logs.append(f"【语音识别】ASR 失败：{exc}", "danger")
        return jsonify({"success": False, "message": f"语音识别失败：{exc}", "status": manager.status()})
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


@app.post("/api/voice/simulate")
def api_voice_simulate():
    """文本模拟语音命令 —— 调试用，直接输入文字代替语音。"""
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "message": "请输入模拟语音文本"})
    result = _process_voice_text(text)
    return jsonify(result)


@app.post("/api/car/voice/listen")
def api_car_voice_listen():
    """触发小车端 USB 麦克风录音识别，并执行匹配到的命令。"""
    payload = request.get_json(silent=True) or {}
    duration = float(payload.get("duration", 3.0))

    manager.logs.append("🎤 正在通过小车麦克风录音...", "info")

    # 小车端录音 + ASR + 命令匹配
    car_result = manager.robot.voice_listen(duration=duration, execute=False)

    if not car_result.get("success"):
        return jsonify(car_result)

    text = car_result.get("text", "").strip()
    if not text:
        manager.logs.append("小车麦克风未识别到语音", "warning")
        return jsonify({**car_result, "status": manager.status()})

    manager.logs.append(f"小车识别文本：「{text}」", "info")

    # 如果有匹配到的命令，走和浏览器录音一样的处理逻辑
    cmd = car_result.get("command")
    if cmd:
        manager.logs.append(f"匹配命令：{cmd}（action={car_result.get('action')}）", "success")

    # 走统一的命令处理
    result = _process_voice_text(text)
    return jsonify(result)


@app.post("/api/car/voice/wake/start")
def api_car_wake_start():
    """启动小车端 Siri 模式：常态监听唤醒词。"""
    result = manager.robot.wake_start()
    if result.get("success"):
        manager.logs.append("🔊 小车唤醒监听已启动", "success")
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/voice/wake/stop")
def api_car_wake_stop():
    """停止小车端唤醒词监听。"""
    result = manager.robot.wake_stop()
    if result.get("success"):
        manager.logs.append("🔇 小车唤醒监听已停止", "info")
    return jsonify({**result, "status": manager.status()})


@app.get("/api/car/voice/wake/status")
def api_car_wake_status():
    """查询小车唤醒监听状态。"""
    result = manager.robot.wake_status()
    return jsonify({**result, "status": manager.status()})


@app.post("/api/gesture/start")
def api_gesture_start():
    return jsonify(manager.gesture_start())


@app.post("/api/gesture/stop")
def api_gesture_stop():
    manager.state.mode = "IDLE"
    manager.state.message = "手势控制已停止"
    manager.logs.append("手势控制已停止")
    return jsonify({"success": True, "status": manager.status()})


@app.post("/api/simulate/obstacle")
def api_simulate_obstacle():
    return jsonify(manager.simulate_obstacle())


@app.post("/api/simulate/arrival")
def api_simulate_arrival():
    return jsonify(manager.simulate_arrival())


@app.get("/api/logs")
def api_logs():
    return jsonify(manager.logs.list())


@app.post("/api/logs/clear")
def api_logs_clear():
    manager.logs.clear()
    manager.logs.append("任务日志已清空")
    return jsonify({"success": True, "logs": manager.logs.list()})


# ======================== 直连小车控制（绕过任务管理器） ========================
@app.post("/api/car/move")
def api_car_move():
    """直接控制小车移动，不经过任务管理器。"""
    payload = request.get_json(silent=True) or {}
    action = payload.get("action", "stop")
    speed = int(payload.get("speed", 40))
    result = manager.robot.command(action, speed)
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/stop")
def api_car_stop():
    """直接停止小车。"""
    result = manager.robot.stop()
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/line_follow")
def api_car_line_follow():
    """直接启动循迹，不校验认证和路径。"""
    payload = request.get_json(silent=True) or {}
    target = payload.get("target", "B")
    speed = int(payload.get("speed", 30))
    manager.robot.set_task_target(target)
    result = manager.robot.command("line_follow", speed)
    manager.state.mode = "LINE_FOLLOW"
    manager.state.target = target
    manager.logs.append(f"直接循迹启动 → {target}，速度 {speed}", "success")
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/avoid")
def api_car_avoid():
    """直接触发绕障测试。"""
    payload = request.get_json(silent=True) or {}
    speed = int(payload.get("speed", 30))
    result = manager.robot._remote_post("/api/test/avoid", {"speed": speed})
    manager.logs.append("手动触发绕障测试", "info")
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/pause")
def api_car_pause():
    """直接暂停循迹。"""
    result = manager.robot._remote_post("/api/task/pause", {})
    manager.logs.append("直连暂停循迹", "info")
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/resume")
def api_car_resume():
    """直接恢复循迹。"""
    result = manager.robot._remote_post("/api/task/resume", {})
    manager.logs.append("直连恢复循迹", "info")
    return jsonify({**result, "status": manager.status()})


# ======================== 小车端人脸识别 ========================
@app.post("/api/car/face/recognize")
def api_car_face_recognize():
    """触发小车端人脸识别（摄像头在小车上，识别更准确）。"""
    payload = request.get_json(silent=True) or {}
    timeout = float(payload.get("timeout", 15))
    result = manager.robot.face_recognize(timeout=timeout)
    manager.logs.append(
        f"小车端人脸识别：{'成功' if result.get('success') else '失败'} — {result.get('message', '')}",
        "success" if result.get("success") else "danger"
    )
    return jsonify({**result, "status": manager.status()})


@app.get("/api/car/face/status")
def api_car_face_status():
    """查询小车端人脸解锁状态。"""
    result = manager.robot.face_status()
    return jsonify({**result, "status": manager.status()})


@app.post("/api/car/qr_scan")
def api_car_qr_scan():
    """使用车载摄像头扫描二维码（二维码在地面上，小车摄像头更合适）。"""
    payload = request.get_json(silent=True) or {}
    timeout = float(payload.get("timeout", 10))
    result = manager.robot.qr_scan_car(timeout=timeout)
    manager.logs.append(
        f"车载 QR 扫描：{'识别到 ' + result.get('qr_value', '') if result.get('success') else '失败'} — {result.get('message', '')}",
        "success" if result.get("success") else "warning"
    )
    # 如果扫描结果与目标一致，自动完成校验
    qr_value = result.get("qr_value")
    if result.get("success") and qr_value and qr_value == manager.state.target:
        manager.state.mode = "DONE"
        manager.state.task_status = "done"
        manager.state.message = f"目标点 {qr_value} 校验成功，任务完成"
        manager.feedback.done()
        manager.logs.append(f"目标校验成功：{qr_value}，任务完成", "success")
    return jsonify({**result, "status": manager.status()})


@app.route("/video_feed")
def video_feed():
    """摄像头 MJPEG 推流。"""
    import cv2
    import time

    camera_index = int(os.environ.get("RASPBOT_CAMERA_INDEX", "0"))

    def generate():
        cap = cv2.VideoCapture(camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not cap.isOpened():
            return
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                ok, buffer = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                )
                if not ok:
                    continue
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + buffer.tobytes() + b"\r\n")
                time.sleep(0.03)
        except GeneratorExit:
            pass
        finally:
            cap.release()

    return Response(generate(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    import os
    host = os.environ.get("RASPBOT_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("RASPBOT_WEB_PORT", "5000"))
    app.run(host=host, port=port, debug=False, threaded=True)
