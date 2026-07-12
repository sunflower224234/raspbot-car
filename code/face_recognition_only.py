# -*- coding: UTF-8 -*-
"""人脸识别模块。

支持两种识别方式：
1. OpenCV Haar 级联（免费离线，无需网络）
2. 百度 AI 人脸识别 API（在线，识别精度更高）

环境变量：
    BAIDU_FACE_API_KEY        百度人脸识别 API Key
    BAIDU_FACE_SECRET_KEY     百度人脸识别 Secret Key
    BAIDU_FACE_GROUP_ID       百度人脸库分组 ID
    BAIDU_FACE_SCORE_THRESHOLD 识别置信度阈值（默认 80）
    RASPBOT_CAMERA_INDEX      摄像头编号（默认 0）
"""

from __future__ import annotations

import os
import time
import base64
from typing import Optional

import cv2

CAMERA_INDEX = int(os.environ.get("RASPBOT_CAMERA_INDEX", "0"))
BAIDU_API_KEY = os.environ.get("BAIDU_FACE_API_KEY", "")
BAIDU_SECRET_KEY = os.environ.get("BAIDU_FACE_SECRET_KEY", "")
BAIDU_GROUP_ID = os.environ.get("BAIDU_FACE_GROUP_ID", "default")
SCORE_THRESHOLD = float(os.environ.get("BAIDU_FACE_SCORE_THRESHOLD", "80"))
FACE_TIMEOUT = float(os.environ.get("RASPBOT_FACE_TIMEOUT", "30"))

# 加载 OpenCV Haar 级联分类器
_HAAR_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_face_cascade = cv2.CascadeClassifier(_HAAR_PATH)


# ======================== 百度 AI 人脸识别 ========================

def _get_baidu_access_token() -> Optional[str]:
    """获取百度 AI access_token。"""
    if not BAIDU_API_KEY or not BAIDU_SECRET_KEY:
        return None
    try:
        import requests
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": BAIDU_API_KEY,
            "client_secret": BAIDU_SECRET_KEY,
        }
        resp = requests.post(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("access_token")
        print(f"[百度AI] 获取 token 失败：{resp.text}")
    except Exception as exc:
        print(f"[百度AI] 获取 token 异常：{exc}")
    return None


def _baidu_face_search(image_path: str) -> Optional[float]:
    """使用百度 AI 在人脸库中搜索。返回置信度分数（0-100），失败返回 None。"""
    token = _get_baidu_access_token()
    if not token:
        return None

    try:
        import requests
        with open(image_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode("utf-8")

        url = (
            f"https://aip.baidubce.com/rest/2.0/face/v3/search"
            f"?access_token={token}"
        )
        payload = {
            "image": img_base64,
            "image_type": "BASE64",
            "group_id_list": BAIDU_GROUP_ID,
            "quality_control": "LOW",
        }
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()

        if data.get("error_code") != 0:
            print(f"[百度AI] 人脸搜索失败：{data.get('error_msg', '未知错误')}")
            return None

        users = data.get("result", {}).get("user_list", [])
        if not users:
            print("[百度AI] 人脸库中未找到匹配用户")
            return 0.0

        return float(users[0].get("score", 0))
    except Exception as exc:
        print(f"[百度AI] 人脸搜索异常：{exc}")
        return None


# ======================== OpenCV 本地人脸检测 ========================

def _opencv_face_detect(frame) -> bool:
    """使用 OpenCV Haar 级联检测人脸。返回是否检测到人脸。"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = _face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
    )
    return len(faces) > 0


# ======================== 主入口 ========================

def run_face_recognition_only(
    timeout_seconds: Optional[float] = None,
) -> bool:
    """仅执行人脸识别，不启动后续功能。

    Args:
        timeout_seconds: 超时秒数，None 表示不限制。

    Returns:
        True=识别通过，False=识别失败/超时。
    """
    if timeout_seconds is None:
        timeout_seconds = FACE_TIMEOUT

    print("[人脸识别] 正在打开摄像头...")
    camera = cv2.VideoCapture(CAMERA_INDEX)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    camera.set(cv2.CAP_PROP_FPS, 15)

    if not camera.isOpened():
        camera.release()
        print(f"[人脸识别] 摄像头打开失败：CAMERA_INDEX={CAMERA_INDEX}")
        return False

    start_time = time.time()
    last_detect_time = 0.0
    use_baidu = bool(BAIDU_API_KEY and BAIDU_SECRET_KEY)

    if use_baidu:
        print("[人脸识别] 使用百度 AI 人脸识别")
    else:
        print("[人脸识别] 使用 OpenCV 本地人脸检测（可配置百度 API 提高精度）")

    try:
        while True:
            # 超时检查
            if timeout_seconds and (time.time() - start_time) > timeout_seconds:
                print(f"[人脸识别] 超时（{timeout_seconds:.0f}秒），识别失败")
                return False

            ret, frame = camera.read()
            if not ret:
                time.sleep(0.1)
                continue

            if use_baidu:
                # 每隔 2 秒调用一次百度 API
                now = time.time()
                if now - last_detect_time >= 2.0:
                    last_detect_time = now
                    temp_path = "/tmp/raspbot_face_temp.jpg"
                    cv2.imwrite(temp_path, frame)
                    score = _baidu_face_search(temp_path)
                    if score is not None and score >= SCORE_THRESHOLD:
                        print(f"[人脸识别] 识别通过！置信度：{score:.2f}")
                        return True
                    elif score is not None:
                        print(f"[人脸识别] 置信度不足：{score:.2f} < {SCORE_THRESHOLD}")
            else:
                # 本地检测：每 0.5 秒检测一次
                now = time.time()
                if now - last_detect_time >= 0.5:
                    last_detect_time = now
                    if _opencv_face_detect(frame):
                        print("[人脸识别] 检测到人脸，识别通过！")
                        return True

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("[人脸识别] 用户中断")
        return False
    finally:
        camera.release()
        cv2.destroyAllWindows()


def run_face_login() -> bool:
    """人脸识别登录入口。识别成功蜂鸣器响一声。"""
    from safety_control import clear_stop_request

    clear_stop_request()
    print("=" * 40)
    print("  人脸识别登录")
    print("=" * 40)
    print("请正对摄像头...")

    result = run_face_recognition_only()

    if result:
        print("[人脸识别] ✅ 识别成功，欢迎！")
        try:
            from raspbot_v2_lib import Raspbot
            car = Raspbot()
            car.beep(0.2)
            car.close()
        except Exception:
            pass
    else:
        print("[人脸识别] ❌ 识别失败！")
        # 蜂鸣器报警
        try:
            from raspbot_v2_lib import Raspbot
            car = Raspbot()
            for _ in range(3):
                car.beep(0.3)
                time.sleep(0.2)
            car.close()
        except Exception:
            pass

    return result


if __name__ == "__main__":
    run_face_login()
