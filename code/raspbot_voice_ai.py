# -*- coding: UTF-8 -*-
"""AI 语音聊天模块。

实现"语音输入 → 语音识别 → AI 回答 → 语音播报"的完整交互闭环。

语音识别：腾讯云 ASR 或 Google Web Speech
AI 对话：Dify API
语音合成：腾讯云 TTS 或 espeak-ng

环境变量：
    TENCENT_SECRET_ID          腾讯云 SecretId（ASR+TTS）
    TENCENT_SECRET_KEY         腾讯云 SecretKey
    DIFY_API_URL               Dify API 地址
    DIFY_API_KEY               Dify API Key
    RECORD_SECONDS             每次录音秒数（默认 5）
    RECORD_DEVICE              录音设备（可选）
"""

from __future__ import annotations

import os
import json
import time
import subprocess
import uuid
import base64

RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", "5"))
RECORD_DEVICE = os.environ.get("RECORD_DEVICE", "")
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "")
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "")
DIFY_API_URL = os.environ.get("DIFY_API_URL", "")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")

conversation_id = ""


# ==================== 录音 ====================
def record_wav(output_file: str = "/tmp/raspbot_question.wav") -> str:
    """使用 arecord 录音。"""
    print(f"\n请说话，录音 {RECORD_SECONDS} 秒...")
    cmd = [
        "arecord", "-q",
        "-f", "S16_LE", "-r", "16000", "-c", "1",
        "-d", str(RECORD_SECONDS), output_file,
    ]
    if RECORD_DEVICE:
        cmd.insert(2, RECORD_DEVICE)
        cmd.insert(2, "-D")
    subprocess.run(cmd, check=True)
    return output_file


# ==================== 语音识别 ====================
def asr_recognize(wav_file: str) -> str:
    """语音识别：优先腾讯云 ASR，失败降级为 Google Web Speech。"""
    # 腾讯云 ASR
    if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
        try:
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
                "HotwordList": "小车|11,送餐|11,避障|11",
            }
            req.from_json_string(json.dumps(params, ensure_ascii=False))
            resp = client.SentenceRecognition(req)
            text = json.loads(resp.to_json_string()).get("Result", "").strip()
            if text:
                return text
        except Exception as exc:
            print(f"[ASR] 腾讯云失败：{exc}")

    # Google Web Speech 降级
    try:
        import speech_recognition as sr
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_file) as source:
            audio = recognizer.record(source)
        text = recognizer.recognize_google(audio, language="zh-CN")
        return text.strip()
    except ImportError:
        print("[ASR] 请安装 SpeechRecognition：pip install SpeechRecognition pyaudio")
        return ""
    except Exception as exc:
        print(f"[ASR] Google 失败：{exc}")
        return ""


def listen_once() -> str:
    """录音一次并返回识别文本。"""
    wav_file = record_wav()
    text = asr_recognize(wav_file).replace(" ", "")
    if text:
        print(f"识别结果：{text}")
    else:
        print("未识别到有效语音")
    return text


# ==================== AI 对话 ====================
def ask_dify(question: str) -> str:
    """调用 Dify API 获取 AI 回答。"""
    global conversation_id

    if not DIFY_API_KEY:
        return "Dify API Key 未配置。请设置 DIFY_API_KEY 和 DIFY_API_URL 环境变量。"

    try:
        import requests
        resp = requests.post(
            DIFY_API_URL,
            headers={
                "Authorization": f"Bearer {DIFY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "inputs": {},
                "query": question,
                "response_mode": "blocking",
                "conversation_id": conversation_id,
                "user": "raspbot-v2",
            },
            timeout=120,
        )
        if resp.status_code != 200:
            return f"Dify 调用失败：{resp.status_code}"
        data = resp.json()
        conversation_id = data.get("conversation_id", conversation_id)
        return data.get("answer", "未获取到回答。").strip()
    except Exception as exc:
        print(f"调用 Dify 失败：{exc}")
        return "调用 AI 服务失败，请检查 Dify 服务和网络。"


def parse_answer(answer: str) -> str:
    """解析 AI 回答。如果是 JSON，提取 response 字段。"""
    if not answer:
        return ""
    try:
        data = json.loads(answer)
        if isinstance(data, dict):
            return data.get("response", answer).strip()
    except Exception:
        pass
    return answer.strip()


# ==================== 语音合成 ====================
def split_text(text: str, max_len: int = 150) -> list:
    """按句子切分长文本。"""
    chunks, current = [], ""
    for ch in text:
        current += ch
        if ch in "。！？\n；，" and len(current) >= 30:
            chunks.append(current.strip())
            current = ""
        elif len(current) >= max_len:
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return chunks


def tts_to_mp3(text: str, output_file: str) -> bool:
    """腾讯云 TTS 合成 MP3。"""
    if not TENCENT_SECRET_ID or not TENCENT_SECRET_KEY:
        return False
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.tts.v20190823 import tts_client, models

        cred = credential.Credential(TENCENT_SECRET_ID, TENCENT_SECRET_KEY)
        http_profile = HttpProfile()
        http_profile.endpoint = "tts.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client = tts_client.TtsClient(cred, "ap-guangzhou", client_profile)

        req = models.TextToVoiceRequest()
        req.Text = text
        req.SessionId = str(uuid.uuid4())
        req.VoiceType = 101001
        req.Codec = "mp3"
        req.Speed = 0
        req.Volume = 5
        req.PrimaryLanguage = 1

        resp = client.TextToVoice(req)
        audio_base64 = json.loads(resp.to_json_string()).get("Audio", "")
        if audio_base64:
            with open(output_file, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            return True
    except Exception as exc:
        print(f"[TTS] 腾讯云失败：{exc}")
    return False


def speak(text: str) -> None:
    """语音播报。"""
    print(f"\nAI 回答：{text}")
    if not text:
        return

    # 优先腾讯云 TTS
    if TENCENT_SECRET_ID and TENCENT_SECRET_KEY:
        try:
            chunks = split_text(text)
            for i, chunk in enumerate(chunks):
                audio_file = f"/tmp/raspbot_tts_{i}.mp3"
                print(f"合成语音 {i + 1}/{len(chunks)}...")
                if tts_to_mp3(chunk, audio_file):
                    for player in (["mpg123", "-q"], ["mplayer", "-really-quiet"],
                                   ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
                        try:
                            subprocess.run([*player, audio_file],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, check=True)
                            break
                        except Exception:
                            continue
            return
        except Exception as exc:
            print(f"[TTS] 播报失败：{exc}")

    # espeak-ng 兜底
    try:
        proc = subprocess.Popen(
            ["espeak-ng", "-v", "zh", "-s", "150", "--stdout", text],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        subprocess.run(["aplay", "-q"], stdin=proc.stdout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"[TTS] espeak-ng 也失败：{exc}")


# ==================== 主循环 ====================
def run_raspbot_voice_ai() -> None:
    """启动 AI 语音聊天。"""
    print("=" * 40)
    print("  AI 语音聊天助手")
    print("=" * 40)

    speak("你好，我是智能小车语音助手。你可以开始向我提问。")

    while True:
        try:
            question = listen_once()
            if not question:
                speak("我没有听清楚，请再说一遍")
                continue

            if question in ["退出", "停止", "关闭", "结束"]:
                speak("好的，语音助手已关闭。再见！")
                break

            answer = ask_dify(question)
            response_text = parse_answer(answer)
            speak(response_text)
            time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n用户手动退出。")
            speak("语音助手已退出。")
            break
        except Exception as exc:
            print(f"异常：{exc}")
            speak("程序出现异常，请检查终端错误信息。")
            time.sleep(1)


if __name__ == "__main__":
    run_raspbot_voice_ai()
