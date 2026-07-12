# -*- coding: UTF-8 -*-
"""语音命令识别模块。

支持两种识别方式：
1. 腾讯云 ASR（在线，精度高）
2. speech_recognition + Google Web Speech（在线，无需 API Key）

命令定义使用 VoiceKeyword 和 DEFAULT_CAR_KEYWORDS。

环境变量：
    TENCENT_SECRET_ID       腾讯云 SecretId
    TENCENT_SECRET_KEY      腾讯云 SecretKey
    RECORD_SECONDS          录音时长（默认 3 秒）
    RECORD_DEVICE           录音设备（如 plughw:1,0）
"""

from __future__ import annotations

import os
import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", "3"))
RECORD_DEVICE = os.environ.get("RECORD_DEVICE", "")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")


@dataclass
class VoiceKeyword:
    """语音命令定义。"""
    command_id: int
    label: str          # 中文标签
    pinyin: str         # 拼音/关键词
    action: str         # 动作标识
    keywords: List[str] = field(default_factory=list)  # 匹配关键词列表


# 默认小车语音命令
DEFAULT_CAR_KEYWORDS: List[VoiceKeyword] = [
    VoiceKeyword(0, "唤醒", "ni hao", "wake",
                 keywords=["你好", "小车", "raspbot", "小助手"]),
    VoiceKeyword(1, "开始送餐", "kai shi song can", "route_delivery",
                 keywords=["送餐", "外卖", "配送", "开始送餐",
                           "去A", "去a", "去 B", "去b", "去 C", "去c",
                           "前往A", "前往B", "前往C",
                           "到A", "到B", "到C"]),
    VoiceKeyword(2, "人脸识别", "ren lian shi bie", "face_recognition",
                 keywords=["人脸识别", "人脸", "识别"]),
    VoiceKeyword(3, "循迹避障", "xun ji bi zhang", "line_follow",
                 keywords=["循迹", "避障", "循迹避障", "自动行驶",
                           "出发", "启动", "开始任务", "执行任务"]),
    VoiceKeyword(4, "天气", "tian qi", "weather",
                 keywords=["天气", "天气预报", "今天天气"]),
    VoiceKeyword(5, "硬件自检", "ying jian zi jian", "hardware_check",
                 keywords=["自检", "硬件", "硬件检查", "检查硬件"]),
    VoiceKeyword(6, "语音测试", "yu yin ce shi", "voice_test",
                 keywords=["语音测试", "测试语音", "麦克风测试"]),
    VoiceKeyword(7, "停止", "ting zhi", "stop",
                 keywords=["停止", "停车", "停下来", "紧急停止"]),
    VoiceKeyword(8, "退出", "tui chu", "exit",
                 keywords=["退出", "关闭语音", "结束"]),
]


class VoiceRecognizer:
    """语音命令识别器。

    使用示例：
        recog = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)
        if recog.init():
            while True:
                cmd = recog.poll_command()
                if cmd:
                    print(f"识别到：{cmd.label}")
    """

    def __init__(self, keywords: List[VoiceKeyword]):
        self.keywords = keywords
        self._rgb_func = None  # 可选 RGB 灯回调
        self._backend = "text"  # tencent / google / text

    def init(self) -> bool:
        """初始化语音识别后端。"""
        if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
            print("[语音识别] 使用腾讯云 ASR")
            self._backend = "tencent"
            return True

        # 尝试 speech_recognition
        try:
            import speech_recognition as sr  # noqa: F401
            print("[语音识别] 使用 Google Web Speech")
            self._backend = "google"
            return True
        except ImportError:
            pass

        print("[语音识别] 警告：无可用的语音识别后端，语音控制将不可用")
        print("[语音识别] 建议：pip install SpeechRecognition pyaudio")
        self._backend = "text"
        return False

    def set_rgb(self, r: int, g: int, b: int):
        """设置 RGB 灯（如果绑定了回调）。"""
        if self._rgb_func:
            try:
                self._rgb_func(r, g, b)
            except Exception:
                pass

    def set_rgb_callback(self, func):
        """绑定 RGB 灯控制回调。"""
        self._rgb_func = func

    def _record_wav(self, output_file: str = "/tmp/raspbot_command.wav") -> str:
        """使用 arecord 录音。"""
        cmd = [
            "arecord", "-q",
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-d", str(RECORD_SECONDS),
            output_file,
        ]
        if RECORD_DEVICE:
            cmd.insert(2, RECORD_DEVICE)
            cmd.insert(2, "-D")

        subprocess.run(cmd, check=True)
        return output_file

    def _tencent_asr(self, wav_file: str) -> str:
        """腾讯云一句话识别。"""
        try:
            import base64
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.asr.v20190614 import asr_client, models as asr_models

            with open(wav_file, "rb") as f:
                audio_data = f.read()
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")

            cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
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
            return json.loads(resp.to_json_string()).get("Result", "").strip()
        except Exception as exc:
            print(f"[语音识别] 腾讯云 ASR 失败：{exc}")
            return ""

    def _google_asr(self, wav_file: str) -> str:
        """Google Web Speech 识别（需联网）。"""
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_file) as source:
                audio = recognizer.record(source)
            return recognizer.recognize_google(audio, language="zh-CN")
        except Exception as exc:
            print(f"[语音识别] Google ASR 失败：{exc}")
            return ""

    def _match_keyword(self, text: str) -> Optional[VoiceKeyword]:
        """在识别文本中匹配命令关键词。"""
        if not text:
            return None
        text_lower = text.lower().replace(" ", "")
        for kw in self.keywords:
            for keyword in kw.keywords:
                if keyword.lower().replace(" ", "") in text_lower:
                    return kw
        return None

    def poll_command(self, timeout: float = 0.1) -> Optional[VoiceKeyword]:
        """轮询一次语音命令（非阻塞）。

        实际使用时需要配合外部录音循环。这里为简化实现，
        返回 None 表示未识别到命令。
        """
        if self._backend == "text":
            return None

        # 尝试录音并识别
        try:
            wav_file = self._record_wav()
            if self._backend == "tencent":
                text = self._tencent_asr(wav_file)
            else:
                text = self._google_asr(wav_file)

            if text:
                print(f"[语音识别] 识别文本：{text}")
                return self._match_keyword(text)
        except Exception as exc:
            print(f"[语音识别] 识别异常：{exc}")

        return None

    def listen_once(self) -> Optional[VoiceKeyword]:
        """录音一次并尝试识别命令。"""
        if self._backend == "text":
            return None
        try:
            wav_file = self._record_wav()
            text = ""
            if self._backend == "tencent":
                text = self._tencent_asr(wav_file)
            else:
                text = self._google_asr(wav_file)

            if text:
                print(f"[语音识别] 识别文本：{text}")
                return self._match_keyword(text)
        except Exception:
            pass
        return None

    def close(self):
        """释放资源。"""
        pass
