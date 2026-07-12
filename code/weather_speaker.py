# -*- coding: UTF-8 -*-
"""天气播报功能。

使用 Open-Meteo 免费天气接口（无需 API Key）。
语音播报使用 speech_output 模块。
可选钉钉机器人推送。

环境变量：
    WEATHER_CITY_NAME          城市名（默认 杭州）
    WEATHER_LAT                纬度（默认 30.2741）
    WEATHER_LON                经度（默认 120.1551）
    DINGTALK_WEBHOOK           钉钉机器人 Webhook（可选）
    TENCENT_SECRET_ID          腾讯云 SecretId（TTS 用）
    TENCENT_SECRET_KEY         腾讯云 SecretKey（TTS 用）
"""

from __future__ import annotations

from datetime import datetime
import os

import requests

WEATHER_CITY_NAME = os.environ.get("WEATHER_CITY_NAME", "杭州")
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "30.2741"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "120.1551"))
DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK", "")

# 天气代码 → 中文
WEATHER_TEXT = {
    0: "晴天", 1: "大部晴朗", 2: "多云", 3: "阴天",
    45: "有雾", 48: "有雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}


def fetch_weather_text() -> str:
    """获取天气信息并生成播报文本。"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": WEATHER_LAT,
        "longitude": WEATHER_LON,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        "forecast_days": 1,
        "timezone": "auto",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    current = resp.json().get("current", {})

    temp = current.get("temperature_2m", "?")
    humidity = current.get("relative_humidity_2m", "?")
    code = int(current.get("weather_code", 0))
    wind = current.get("wind_speed_10m", "?")
    weather = WEATHER_TEXT.get(code, f"代码{code}")

    today = datetime.now().strftime("%Y年%m月%d日")
    weekday = datetime.now().strftime("%A")
    weekday_cn = {"Monday": "一", "Tuesday": "二", "Wednesday": "三",
                  "Thursday": "四", "Friday": "五", "Saturday": "六", "Sunday": "日"}
    wk = weekday_cn.get(weekday, "")

    return (
        f"【天气播报】小主人好，今天是{today}，星期{wk}。"
        f"{WEATHER_CITY_NAME}今天{weather}，"
        f"气温{temp}度，湿度百分之{humidity}，"
        f"风速{wind}公里每小时。"
    )


def send_to_dingtalk(text: str) -> None:
    """发送消息到钉钉机器人。"""
    if not DINGTALK_WEBHOOK:
        return
    try:
        resp = requests.post(
            DINGTALK_WEBHOOK,
            json={"msgtype": "text", "text": {"content": text}},
            timeout=5,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            print("[钉钉] 消息已发送")
        else:
            print(f"[钉钉] 发送失败：{data.get('errmsg', '未知错误')}")
    except Exception as exc:
        print(f"[钉钉] 发送失败：{exc}")


def run_weather_speaker() -> None:
    """天气查询与播报主入口。"""
    from speech_output import Speaker

    speaker = Speaker()
    print("正在查询天气...")
    speaker.speak("正在查询天气", wait=True)

    try:
        text = fetch_weather_text()
    except Exception as exc:
        print(f"天气查询失败：{exc}")
        text = "抱歉，天气查询失败。请检查网络连接后重试。"

    print(text)
    send_to_dingtalk(text)
    speaker.speak(text, wait=True)
    speaker.close()


if __name__ == "__main__":
    run_weather_speaker()
