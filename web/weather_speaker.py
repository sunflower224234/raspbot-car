# -*- coding: UTF-8 -*-
"""天气播报功能。

默认使用 Open-Meteo 免费接口，无需 API Key。
默认城市为杭州；可以通过环境变量改位置：
    export WEATHER_CITY_NAME=杭州
    export WEATHER_LAT=30.2741
    export WEATHER_LON=120.1551

腾讯云 TTS 配置：
    export TENCENT_SECRET_ID="你的SecretId"
    export TENCENT_SECRET_KEY="你的SecretKey"
"""

from __future__ import annotations

from datetime import datetime
import os
import base64
import tempfile
import subprocess
from typing import Dict

import requests
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.tts.v20190823 import tts_client, models

# 钉钉 Webhook（可选，在 .env 中配置 DINGTALK_WEBHOOK）
WEBHOOK = os.environ.get("DINGTALK_WEBHOOK",
    "https://oapi.dingtalk.com/robot/send?access_token=3bda9b3fb94eda809cd294defd13b60f650d31a90df22d7324f742c0c96efa94")

# 腾讯云密钥
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")
VOICE_TYPE = int(os.environ.get("TTS_VOICE_TYPE", "1001"))  # 1001=智瑜女声

WEATHER_CITY_NAME = os.environ.get("WEATHER_CITY_NAME", "杭州")
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "30.2741"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "120.1551"))

WEATHER_CODE_CN: Dict[int, str] = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "大毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}


def fetch_weather_text() -> str:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        "forecast_days": 1,
        "timezone": "auto",
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    current = data.get("current", {})

    temp = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    code = int(current.get("weather_code", 0))
    wind = current.get("wind_speed_10m")
    weather = WEATHER_CODE_CN.get(code, f"天气代码{code}")
    today = datetime.now().strftime("%Y年%m月%d日")

    return (
        f"小主人好，今天是{today}。"
        f"{WEATHER_CITY_NAME}当前天气{weather}，"
        f"气温{temp}摄氏度，湿度百分之{humidity}，"
        f"十米风速{wind}公里每小时。"
    )


def speak_text(text: str) -> bool:
    """使用腾讯云TTS播报文本"""
    if not text:
        return False
    
    if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
        print("错误: 未配置腾讯云密钥")
        return False
    
    try:
        cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
        client = tts_client.TtsClient(cred, "ap-guangzhou")
        
        req = models.TextToVoiceRequest()
        req.Text = text
        req.SessionId = "weather-tts"
        req.VoiceType = VOICE_TYPE
        req.Codec = "mp3"
        req.Speed = 0
        req.Volume = 5
        req.PrimaryLanguage = 1
        
        resp = client.TextToVoice(req)
        audio_data = base64.b64decode(resp.Audio)
        
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(audio_data)
            filename = tmp.name
        
        subprocess.run(
            ["mplayer", filename],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        
        os.remove(filename)
        return True
        
    except TencentCloudSDKException as e:
        print(f"TTS调用失败: {e}")
        return False
    except Exception as e:
        print(f"语音播报失败: {e}")
        return False


def send_to_dingtalk(text: str) -> None:
    """发送消息到钉钉（自动添加关键词"小车"）"""
    if not WEBHOOK:
        return
    # 确保包含钉钉机器人关键词
    if "小车" not in text:
        text = "【小车】" + text
    try:
        data = {
            "msgtype": "text",
            "text": {"content": text}
        }
        requests.post(WEBHOOK, json=data, timeout=5)
    except Exception as e:
        print(f"钉钉发送失败: {e}")


def run_weather_speaker() -> None:
    print("正在查询天气……")
    speak_text("正在查询天气")
    
    try:
        text = fetch_weather_text()
    except Exception as exc:
        print("天气查询失败：", repr(exc))
        text = "抱歉，天气查询失败。请检查小车网络连接，或者稍后再试。"
    
    print(text)
    send_to_dingtalk(text)
    speak_text(text)


if __name__ == "__main__":
    run_weather_speaker()