# -*- coding: UTF-8 -*-
"""唤醒词监听模块 —— 类似 Siri 的常态监听 + 唤醒词激活。

流程：
    1. 后台循环检测麦克风音量（音频能量 VAD）
    2. 检测到声音 → 录音 2 秒 → ASR 检查是否包含唤醒词
    3. 唤醒词命中 → 播报"我在" → 录音 4 秒 → ASR 识别命令
    4. 返回识别到的命令文本

依赖：
    - USB 麦克风（arecord -l 可见）
    - tencentcloud-sdk-python
    - .env 中配置 TENCENT_SECRET_ID / TENCENT_SECRET_KEY

使用示例：
    from voice_wake import WakeListener
    wl = WakeListener()
    wl.start()  # 后台开始监听
    cmd = wl.wait_for_command(timeout=30)  # 阻塞等待命令
    print(f"收到命令：{cmd}")
"""

from __future__ import annotations

import os
import struct
import threading
import time

from dotenv import load_dotenv
load_dotenv()

from voice_input import record_wav, recognize_wav

# ---- 唤醒词配置 ----
WAKE_WORDS = os.environ.get("RASPBOT_WAKE_WORDS", "小车,你好小车,小助手").split(",")
WAKE_WORDS = [w.strip() for w in WAKE_WORDS if w.strip()]
WAKE_WORDS = [w.strip() for w in WAKE_WORDS if w.strip()]

# ---- 音频参数 ----
VAD_DURATION = 1          # 每次 VAD 检测录音秒数（arecord 仅支持整数）
VAD_THRESHOLD = int(os.environ.get("RASPBOT_VAD_THRESHOLD", "5000"))
VAD_COOLDOWN = 0.8        # 检测到声音后的冷却时间
WAKE_DURATION = 2          # 唤醒词确认录音秒数
COMMAND_DURATION = 4       # 命令录音秒数


def _compute_rms(filepath: str) -> float:
    """计算 WAV 文件的 RMS（均方根）能量值。"""
    try:
        with open(filepath, "rb") as f:
            f.seek(44)  # 跳过 WAV 头
            data = f.read()
    except Exception:
        return 0.0

    if len(data) < 100:
        return 0.0

    count = len(data) // 2  # S16_LE = 2 bytes per sample
    if count == 0:
        return 0.0

    try:
        samples = struct.unpack(f"<{count}h", data[:count * 2])
    except struct.error:
        return 0.0

    # 取中间一段避免边界噪音
    mid = len(samples) // 2
    window = min(16000, len(samples) // 2)  # 最多 1 秒的采样点
    start = max(0, mid - window // 2)
    end = min(len(samples), start + window)
    chunk = samples[start:end]
    if not chunk:
        return 0.0

    return (sum(s * s for s in chunk) / len(chunk)) ** 0.5


def _match_wake(text: str) -> bool:
    """模糊匹配唤醒词。支持 ASR 部分识别（如只听清"车"而漏掉"小"）。"""
    if not text:
        return False
    # 精确匹配
    if any(w in text for w in WAKE_WORDS):
        return True
    # 短文本（≤2 字）模糊匹配：只要任一字出现在任一唤醒词中就触发
    if len(text) <= 2:
        for ch in text:
            for w in WAKE_WORDS:
                if ch in w:
                    print(f"[唤醒监听] 模糊匹配：'{text}' → '{ch}' in '{w}'")
                    return True
    return False


def _say(text: str):
    """简短播报。"""
    try:
        from speech_output import Speaker
        Speaker().speak(text, wait=False)
    except Exception:
        pass


class WakeListener:
    """唤醒词监听器。"""

    def __init__(self):
        self._listening = False
        self._thread: threading.Thread | None = None
        self._wake_detected = threading.Event()
        self._command_ready = threading.Event()
        self._command_text = ""
        self._status = "idle"

    @property
    def status(self) -> str:
        return self._status

    def start(self):
        if self._listening:
            return
        self._listening = True
        self._wake_detected.clear()
        self._command_ready.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print(f"[唤醒监听] 已启动，唤醒词：{WAKE_WORDS}，VAD阈值={VAD_THRESHOLD}")

    def stop(self):
        self._listening = False
        self._wake_detected.set()
        self._command_ready.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._status = "stopped"
        print("[唤醒监听] 已停止")

    def wait_for_command(self, timeout: float = 60) -> str:
        if not self._listening:
            return ""
        ok = self._command_ready.wait(timeout=timeout)
        if ok:
            self._command_ready.clear()
            return self._command_text
        return ""

    # ---- 内部 ----

    def _listen_loop(self):
        while self._listening:
            self._status = "listening"

            # 1. VAD：录短片段，检测能量
            try:
                vad_path = record_wav(duration=VAD_DURATION)
            except Exception as exc:
                print(f"[唤醒监听] VAD 录音失败：{exc}")
                time.sleep(0.5)
                continue

            rms = _compute_rms(vad_path)
            # 清理临时文件
            try:
                os.unlink(vad_path)
            except OSError:
                pass

            print(f"[唤醒监听] VAD RMS={rms:.0f} (阈值={VAD_THRESHOLD})", end="\r")

            if rms < VAD_THRESHOLD:
                time.sleep(0.1)
                continue

            print(f"\n[唤醒监听] 🔔 检测到声音 (RMS={rms:.0f})")

            # 2. 短暂冷却
            time.sleep(VAD_COOLDOWN)

            # 3. 录音检测唤醒词
            self._status = "checking_wake"
            print("[唤醒监听] 录音 2 秒检测唤醒词...")
            try:
                wake_path = record_wav(duration=WAKE_DURATION)
            except Exception as exc:
                print(f"[唤醒监听] 唤醒词录音失败：{exc}")
                continue

            text = recognize_wav(wake_path)
            try:
                os.unlink(wake_path)
            except OSError:
                pass

            if not text:
                print("[唤醒监听] ASR 无结果")
                continue

            print(f"[唤醒监听] 听到：「{text}」")

            # 4. 匹配唤醒词（支持部分识别）
            found_wake = _match_wake(text)
            if not found_wake:
                print("[唤醒监听] 非唤醒词，忽略")
                continue

            print(f"[唤醒监听] ✅ 唤醒词命中！")
            self._status = "wake_detected"
            self._wake_detected.set()

            # 5. 反馈 + 录命令
            _say("我在")
            time.sleep(0.5)

            self._status = "recording_command"
            print("[唤醒监听] 🎤 正在录命令（4 秒）...")
            try:
                cmd_path = record_wav(duration=COMMAND_DURATION)
            except Exception as exc:
                print(f"[唤醒监听] 命令录音失败：{exc}")
                self._status = "idle"
                continue

            cmd_text = recognize_wav(cmd_path)
            try:
                os.unlink(cmd_path)
            except OSError:
                pass

            if cmd_text:
                print(f"[唤醒监听] 📢 命令：「{cmd_text}」")
                self._command_text = cmd_text
                self._command_ready.set()
            else:
                print("[唤醒监听] 未识别到命令")
                _say("没有听清")

            time.sleep(1.0)

        self._status = "stopped"


# ======================== 模块级快捷实例 ========================
_default_listener: WakeListener | None = None


def get_listener() -> WakeListener:
    global _default_listener
    if _default_listener is None:
        _default_listener = WakeListener()
    return _default_listener


# ======================== 测试 ========================
if __name__ == "__main__":
    print("🎤 唤醒词监听测试")
    print(f"   唤醒词：{WAKE_WORDS}")
    print("   说话试试... (Ctrl+C 退出)")
    print()

    wl = WakeListener()
    wl.start()

    try:
        while True:
            cmd = wl.wait_for_command(timeout=60)
            if cmd:
                print(f"\n📢 收到命令：{cmd}\n")
            else:
                print("⏰ 等待中...")
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        wl.stop()
