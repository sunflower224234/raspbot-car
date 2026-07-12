# -*- coding: UTF-8 -*-
"""RASPBOT-V2 语音控制入口。

功能：
1. 监听语音模块的固定词条识别结果；
2. 识别到命令后用语音播报确认；
3. 调用对应的独立功能：二维码 A* 送餐、人脸识别、循迹避障、天气播报、自检等。

安全策略：
- 语音模式空闲时持有 runtime_guard 锁，防止其它终端同时启动小车功能。
- 真正执行小车功能前释放锁，让被调用功能自己加锁；执行期间不继续监听语音，避免摄像头/I2C/电机并发冲突。
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
    car = Raspbot()
    try:
        car.stop()
        car.Ctrl_BEEP_Switch(0)
        car.Ctrl_WQ2812_brightness_ALL(0, 0, 0)
    finally:
        car.close()


def _run_with_released_lock(lock: _ManualLock, speaker: Speaker, label: str, func: Callable[[], None]) -> None:
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
        print(f"{label} 执行失败：{repr(exc)}")
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


def _execute_command(cmd: VoiceKeyword, lock: _ManualLock, speaker: Speaker) -> bool:
    """返回 False 表示退出语音模式。"""
    action = cmd.action
    label = cmd.label

    if action == "wake":
        print("语音唤醒：", label)
        speaker.speak("我在。" + HELP_TEXT, wait=False)
        return True

    if action == "route_delivery":
        from route_delivery import run_route_delivery
        _run_with_released_lock(lock, speaker, label, run_route_delivery)
        return True

    if action == "face_recognition":
        # 已按项目需求改为：人脸识别通过后自动循迹/避障。
        from face_then_follow import run_face_then_follow
        _run_with_released_lock(lock, speaker, label, run_face_then_follow)
        return True

    if action == "line_follow":
        from line_follow_obstacle import run_line_follow_obstacle
        _run_with_released_lock(lock, speaker, label, run_line_follow_obstacle)
        return True

    if action == "weather":
        # 天气播报只用网络和语音模块，不释放锁，避免其它功能同时启动。
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
    lock = _ManualLock("voice_control_mode")
    speaker = Speaker()
    recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)

    try:
        lock.acquire()
        print("正在初始化语音控制模块……")
        if not recognizer.init():
            print("语音识别模块初始化失败。")
            return

        speaker.speak("智能外卖小车语音控制已启动。" + HELP_TEXT, wait=True)
        print("语音控制已启动，可说：")
        for item in DEFAULT_CAR_KEYWORDS:
            print(f"  {item.command_id}. {item.label}: {item.pinyin}")
        print("按 Ctrl+C 可退出语音控制。")

        last_result = 0
        last_time = 0.0
        while True:
            cmd = recognizer.poll_command()
            now = time.monotonic()
            if cmd:
                # 简单防抖：同一命令 1 秒内不重复执行。
                if cmd.command_id == last_result and now - last_time < 1.0:
                    time.sleep(0.1)
                    continue
                last_result = cmd.command_id
                last_time = now

                print(f"识别到：{cmd.label} / {cmd.pinyin} / action={cmd.action}")
                recognizer.set_rgb(0, 120, 0)
                time.sleep(0.2)
                recognizer.set_rgb(0, 0, 0)
                if not _execute_command(cmd, lock, speaker):
                    break
            time.sleep(0.1)
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
