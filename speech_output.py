# -*- coding: UTF-8 -*-
"""语音播报模块。

支持多种 TTS 后端（按优先级）：
1. 腾讯云 TTS（在线，音质最好）
2. pyttsx3（Windows SAPI5 / 跨平台，离线）
3. espeak-ng（Linux 系统自带）
4. text_only（仅打印，兜底方案）

环境变量：
    TENCENT_SECRET_ID      腾讯云 SecretId
    TENCENT_SECRET_KEY     腾讯云 SecretKey
    TTS_VOICE_TYPE         腾讯云音色类型（默认 1001=智瑜女声）
"""

from __future__ import annotations

import os
import base64
import platform
import subprocess
import tempfile
import time
from typing import Optional

TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")
TTS_VOICE_TYPE = int(os.environ.get("TTS_VOICE_TYPE", "1001"))

_IS_WINDOWS = platform.system() == "Windows"


def _play_audio_file(filepath: str) -> bool:
    """跨平台播放音频文件。"""
    if _IS_WINDOWS:
        # 优先用 winsound（Windows 内置，WAV 播放最可靠）
        if filepath.endswith(".wav"):
            try:
                import winsound
                winsound.PlaySound(filepath, winsound.SND_FILENAME)
                return True
            except Exception:
                pass
        # MP3 或其他格式：用系统默认播放器
        try:
            os.startfile(filepath)
            return True
        except Exception:
            pass
        return False

    # Linux: 命令行播放器
    for player_cmd in [
        ["mpg123", "-q"],
        ["mplayer", "-really-quiet", "-nodisplay"],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
        ["aplay"],
        ["paplay"],
    ]:
        try:
            subprocess.run(
                player_cmd + [filepath],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


class Speaker:
    """语音播报器。

    使用示例：
        speaker = Speaker()
        speaker.speak("你好，我是智能小车")
        speaker.close()
    """

    def __init__(self):
        self._tts_engine = None  # pyttsx3 引擎实例
        self._initialized = self._check_backend()

    def _check_backend(self) -> str:
        """检测可用的 TTS 后端。"""
        # 1. 腾讯云 TTS（在线，需 API Key）
        if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
            print("[语音] 使用腾讯云 TTS")
            return "tencent"

        # 2. pyttsx3（跨平台，Windows SAPI5 中文语音）
        try:
            import pyttsx3
            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            for voice in voices:
                if "chinese" in voice.name.lower() or "zh" in voice.id.lower():
                    engine.setProperty("voice", voice.id)
                    break
            engine.setProperty("rate", 160)
            engine.setProperty("volume", 0.9)
            self._tts_engine = engine
            print(f"[语音] 使用 pyttsx3（{len(voices)} 个语音可用）")
            return "pyttsx3"
        except Exception:
            pass

        # 3. espeak-ng（Linux）
        try:
            subprocess.run(
                ["espeak-ng", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[语音] 使用 espeak-ng 本地 TTS")
            return "espeak"
        except FileNotFoundError:
            pass

        print("[语音] 未检测到可用 TTS 后端，语音播报将仅打印文本")
        return "text_only"

    def speak(self, text: str, wait: bool = True) -> bool:
        """播报文本。"""
        if not text:
            return False

        print(f"[语音播报] {text}")

        if self._initialized == "tencent":
            return self._tencent_tts(text, wait)
        elif self._initialized == "pyttsx3":
            return self._pyttsx3_tts(text, wait)
        elif self._initialized == "espeak":
            return self._espeak_tts(text, wait)
        else:
            return True  # text_only

    # ---- 后端实现 ----

    def _tencent_tts(self, text: str, wait: bool) -> bool:
        """腾讯云 TTS → 下载 MP3 → 跨平台播放。"""
        try:
            from tencentcloud.common import credential
            from tencentcloud.tts.v20190823 import tts_client, models

            cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
            client = tts_client.TtsClient(cred, "ap-guangzhou")

            req = models.TextToVoiceRequest()
            req.Text = text
            req.SessionId = "raspbot-tts"
            req.VoiceType = TTS_VOICE_TYPE
            req.Codec = "wav"          # wav 兼容性更好
            req.Speed = 0
            req.Volume = 5
            req.PrimaryLanguage = 1

            resp = client.TextToVoice(req)
            audio_data = base64.b64decode(resp.Audio)

            suffix = ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_data)
                filename = tmp.name

            ok = _play_audio_file(filename)
            try:
                time.sleep(0.5)  # 等播放器打开再删
                os.remove(filename)
            except OSError:
                pass

            if not ok:
                print("[语音] 无可用的音频播放器，降级到 pyttsx3")
                return self._pyttsx3_tts(text, wait)

            return True

        except Exception as exc:
            print(f"[语音] 腾讯云 TTS 失败：{exc}")
            return self._pyttsx3_tts(text, wait)  # 降级

    def _pyttsx3_tts(self, text: str, wait: bool) -> bool:
        """pyttsx3 离线播报。"""
        try:
            if self._tts_engine is None:
                import pyttsx3
                self._tts_engine = pyttsx3.init()
                self._tts_engine.setProperty("rate", 160)
            self._tts_engine.say(text)
            if wait:
                self._tts_engine.runAndWait()
            return True
        except Exception as exc:
            print(f"[语音] pyttsx3 失败：{exc}")
            return False

    def _espeak_tts(self, text: str, wait: bool) -> bool:
        """espeak-ng 播报（Linux）。"""
        try:
            proc = subprocess.Popen(
                ["espeak-ng", "-v", "zh", "-s", "150", "--stdout", text],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["aplay", "-q"],
                stdin=proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if wait:
                proc.wait()
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            print(f"[语音] espeak-ng 失败：{exc}")
            return False

    def close(self):
        """释放资源。"""
        if self._tts_engine:
            try:
                self._tts_engine.stop()
            except Exception:
                pass
            self._tts_engine = None


# 模块级快捷函数
_default_speaker: Optional[Speaker] = None


def get_speaker() -> Speaker:
    """获取全局 Speaker 实例。"""
    global _default_speaker
    if _default_speaker is None:
        _default_speaker = Speaker()
    return _default_speaker
