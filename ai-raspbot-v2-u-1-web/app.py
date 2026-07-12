from __future__ import annotations

from flask import Flask, jsonify, redirect, render_template, request, url_for

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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
