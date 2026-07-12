# -*- coding: UTF-8 -*-
"""语音控制入口。

监听固定语音命令并启动对应功能：
- "开始送餐" → route_delivery
- "人脸识别" → face_then_follow
- "循迹避障" → line_follow_obstacle
- "天气" → weather_speaker
- "硬件自检" → hardware_check
- "停止" → 紧急停车
- "退出" → 退出语音控制

环境变量：
    TENCENT_SECRET_ID       腾讯云 SecretId（ASR）
    TENCENT_SECRET_KEY      腾讯云 SecretKey
    RECORD_SECONDS          录音时长
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from raspbot_v2_lib import Raspbot
from runtime_guard import RaspbotTaskLock
from speech_output import Speaker
from voice_command_device import VoiceRecognizer, VoiceKeyword, DEFAULT_CAR_KEYWORDS
from weather_speaker import run_weather_speaker
from voice_hardware_test import run_voice_hardware_test

HELP_TEXT = "你可以说：开始送餐，人脸识别，循迹避障，天气，硬件自检，语音测试，停止，退出。"


class _ManualLock:
    """手动管理任务锁。语音控制空闲时持有锁。"""

    def __init__(self, name: str):
        self.name = name
        self.lock: Optional[RaspbotTaskLock] = None

    def acquire(self) -> None:
        if self.lock is None:
            self.lock = RaspbotTaskLock(self.name)
            self.lock.__enter__()

    def release(self) -> None:
        if self.lock is not None:
            self.lock.__exit__(None, None, None)
            self.lock = None


def _stop_car_now() -> None:
    """立即停车并关闭外设。"""
    car = Raspbot()
    try:
        car.stop()
        car.Ctrl_BEEP_Switch(0)
        car.Ctrl_WQ2812_brightness_ALL(0, 0, 0)
    finally:
        car.close()


def _run_with_released_lock(
    lock: _ManualLock, speaker: Speaker, label: str, func: Callable[[], None]
) -> None:
    """释放锁后执行功能，执行完毕后重新持有锁。"""
    speaker.speak(f"已识别到{label}，即将开始执行", wait=True)
    lock.release()
    try:
        func()
    except KeyboardInterrupt:
        print(f"{label} 被用户中断。")
        try:
            _stop_car_now()
        except Exception:
            pass
    except Exception as exc:
        print(f"{label} 执行失败：{exc!r}")
        try:
            speaker.speak(f"{label}执行失败，请查看终端错误信息", wait=True)
        except Exception:
            pass
    finally:
        lock.acquire()
        try:
            speaker.speak(f"{label}已经结束，语音控制继续监听", wait=False)
        except Exception:
            pass


def _execute_command(
    cmd: VoiceKeyword, lock: _ManualLock, speaker: Speaker
) -> bool:
    """执行语音命令。返回 False 表示退出语音控制。"""
    action = cmd.action
    label = cmd.label

    if action == "wake":
        print(f"语音唤醒：{label}")
        speaker.speak("我在。" + HELP_TEXT, wait=False)
        return True

    if action == "route_delivery":
        from route_delivery import run_route_delivery
        _run_with_released_lock(lock, speaker, label, run_route_delivery)
        return True

    if action == "face_recognition":
        from face_then_follow import run_face_then_follow
        _run_with_released_lock(lock, speaker, label, run_face_then_follow)
        return True

    if action == "line_follow":
        from line_follow_obstacle import run_line_follow_obstacle
        _run_with_released_lock(lock, speaker, label, run_line_follow_obstacle)
        return True

    if action == "weather":
        speaker.speak("已识别到天气播报", wait=True)
        run_weather_speaker()
        return True

    if action == "hardware_check":
        from hardware_check import run_hardware_check
        _run_with_released_lock(lock, speaker, label, run_hardware_check)
        return True

    if action == "voice_test":
        speaker.speak("开始语音模块测试", wait=True)
        run_voice_hardware_test(listen_seconds=10)
        return True

    if action == "stop":
        _stop_car_now()
        speaker.speak("小车已停止", wait=True)
        return True

    if action == "exit":
        speaker.speak("语音控制已退出", wait=True)
        return False

    speaker.speak("暂不支持这个语音命令", wait=True)
    return True


def run_voice_control_car() -> None:
    """语音控制主入口。"""
    lock = _ManualLock("voice_control_mode")
    speaker = Speaker()
    recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)

    try:
        lock.acquire()
        print("正在初始化语音控制模块...")
        if not recognizer.init():
            print("语音识别模块初始化失败，语音控制不可用。")
            return

        speaker.speak("智能小车语音控制已启动。" + HELP_TEXT, wait=True)
        print("语音控制已启动，可说的命令：")
        for item in DEFAULT_CAR_KEYWORDS:
            print(f"  {item.command_id}. {item.label}: {item.pinyin}")
        print("按 Ctrl+C 退出。")

        last_result = 0
        last_time = 0.0

        while True:
            cmd = recognizer.listen_once()
            now = time.monotonic()
            if cmd:
                # 防抖：同一命令 1 秒内不重复执行
                if cmd.command_id == last_result and now - last_time < 1.0:
                    time.sleep(0.1)
                    continue
                last_result = cmd.command_id
                last_time = now

                print(f"识别到：{cmd.label} / {cmd.pinyin} / action={cmd.action}")
                try:
                    recognizer.set_rgb(0, 120, 0)
                    time.sleep(0.2)
                    recognizer.set_rgb(0, 0, 0)
                except Exception:
                    pass
                if not _execute_command(cmd, lock, speaker):
                    break
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("语音控制被用户中断。")
        try:
            speaker.speak("语音控制已停止", wait=True)
        except Exception:
            pass
    finally:
        recognizer.close()
        speaker.close()
        lock.release()


if __name__ == "__main__":
    run_voice_control_car()
