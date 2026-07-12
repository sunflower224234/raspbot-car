import os
import json
import queue
import time
import subprocess
import requests
import sounddevice as sd
import base64
import uuid

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.tts.v20190823 import tts_client, models as tts_models
from tencentcloud.asr.v20190614 import asr_client, models as asr_models

TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID", "")
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY", "")
# =========================
# 1. 基础配置
# =========================

# 腾讯云 ASR 配置
TENCENT_ASR_REGION = os.getenv("TENCENT_ASR_REGION", "ap-shanghai")
TENCENT_ASR_ENGINE = os.getenv("TENCENT_ASR_ENGINE", "16k_zh")

# 每次录音时长，单位秒
RECORD_SECONDS = int(os.getenv("RECORD_SECONDS", "5"))

# 如果默认麦克风不能录音，可以填 plughw:1,0 这类设备
RECORD_DEVICE = os.getenv("RECORD_DEVICE", "")

# Dify API 地址：
DIFY_API_URL = os.getenv(
    "DIFY_API_URL",
    "http://172.20.10.11/v1/chat-messages"
)

DIFY_API_KEY = os.getenv(
    "DIFY_API_KEY",
    "app-8QNCOWIvQKfmg1tnCBUGaSK9"
)
# Vosk 中文模型路径
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "/home/pi/vosk-model-small-cn-0.22")

# 采样率
SAMPLE_RATE = 16000

# Edge TTS 中文声音
TTS_VOICE = "zh-CN-XiaoxiaoNeural"

# 用于保持上下文
conversation_id = ""

audio_queue = queue.Queue()


# =========================
# 2. 录音回调
# =========================

def audio_callback(indata, frames, time_info, status):
    if status:
        print("录音状态：", status)
    audio_queue.put(bytes(indata))


# =========================
# 3. 语音识别
# =========================

def record_wav(output_file="/tmp/raspbot_question.wav"):
    """
    使用 arecord 录制 16k、单声道、16bit wav 音频。
    """
    print(f"\n请开始说话，本次录音 {RECORD_SECONDS} 秒...")

    cmd = [
        "arecord",
        "-q",
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        "-d", str(RECORD_SECONDS),
        output_file
    ]

    if RECORD_DEVICE:
        cmd.insert(2, RECORD_DEVICE)
        cmd.insert(2, "-D")

    subprocess.run(cmd, check=True)
    return output_file


def tencent_asr_recognize(wav_file):
    """
    调用腾讯云一句话识别，把 wav 转成文字。
    """
    secret_id = TENCENTCLOUD_SECRET_ID
    secret_key = TENCENTCLOUD_SECRET_KEY

    if not secret_id or not secret_key:
        raise RuntimeError("腾讯云 SecretId 或 SecretKey 没有配置")

    with open(wav_file, "rb") as f:
        audio_data = f.read()

    audio_base64 = base64.b64encode(audio_data).decode("utf-8")

    cred = credential.Credential(secret_id, secret_key)

    http_profile = HttpProfile()
    http_profile.endpoint = "asr.tencentcloudapi.com"

    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile

    client = asr_client.AsrClient(cred, TENCENT_ASR_REGION, client_profile)

    req = asr_models.SentenceRecognitionRequest()

    params = {
        "ProjectId": 0,
        "SubServiceType": 2,
        "EngSerViceType": TENCENT_ASR_ENGINE,
        "SourceType": 1,
        "VoiceFormat": "wav",
        "Data": audio_base64,
        "DataLen": len(audio_data),
        "UsrAudioKey": str(uuid.uuid4()),

        # 可选：数字转换、标点、脏词过滤等
        "FilterDirty": 0,
        "FilterModal": 0,
        "FilterPunc": 0,
        "ConvertNumMode": 1,

        # 可选：提升小车相关词汇识别率
        "HotwordList": "小车|11,RASPBOT|11,Dify|11,千问|11,配送|11,二维码|11,避障|11"
    }

    req.from_json_string(json.dumps(params, ensure_ascii=False))

    resp = client.SentenceRecognition(req)
    resp_json = json.loads(resp.to_json_string())

    text = resp_json.get("Result","").strip()

    if not text:
        print("腾讯云ASR原始返回：",resp_json)

    return text
    
def listen_once():
    """
    录音一次，并调用腾讯云 ASR 识别。
    """
    wav_file = record_wav()
    text = tencent_asr_recognize(wav_file)
    
    text = text.replace(" ", "").strip()
    
    if text:
        print("识别结果：", text)
        return text
    
    print("没有识别到有效语音")
    return ""
# =========================
# 4. 调用 Dify / 千问
# =========================

def ask_dify(question):
    global conversation_id

    if not DIFY_API_KEY:
        return "Dify API Key 没有配置，请先设置 DIFY_API_KEY。"

    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "inputs": {},
        "query": question,
        "response_mode": "blocking",
        "conversation_id": conversation_id,
        "user": "raspbot-v2"
    }

    try:
        response = requests.post(
            DIFY_API_URL,
            headers=headers,
            json=payload,
            timeout=120
        )

        if response.status_code != 200:
            print("Dify 返回错误：", response.status_code, response.text)
            return f"Dify 调用失败，状态码是 {response.status_code}。"

        data = response.json()

        conversation_id = data.get("conversation_id", conversation_id)
        answer = data.get("answer", "")

        if not answer:
            return "我没有获取到有效回答。"

        return answer.strip()

    except Exception as e:
        print("调用 Dify 失败：", e)
        return "调用人工智能服务失败，请检查 Dify 服务和网络。"


# =========================
# 5. 语音输出
# =========================
def parse_ai_answer(answer):
    """
    解析 Dify 返回内容：
    如果是 JSON，只取 response 用于语音播报；
    function 保留下来，后续可用于控制小车动作。
    """
    if not answer:
        return "", []

    try:
        data = json.loads(answer)

        if isinstance(data, dict):
            response_text = data.get("response", "")
            functions = data.get("function", [])

            if isinstance(functions, str):
                functions = [functions]

            return response_text.strip(), functions

    except Exception:
        pass

    # 如果不是 JSON，就按普通文本处理
    return str(answer).strip(), []
    
def split_text(text, max_len=150):
    """
    腾讯云语音合成单次文本不宜太长，这里按句子简单切分。
    """
    if not text:
        return []

    seps = ["。", "！", "？", "\n", "；", "，"]
    chunks = []
    current = ""

    for ch in text:
        current += ch
        if ch in seps and len(current) >= 30:
            chunks.append(current.strip())
            current = ""
        elif len(current) >= max_len:
            chunks.append(current.strip())
            current = ""

    if current.strip():
        chunks.append(current.strip())

    return chunks


def tencent_tts_to_mp3(text, output_file):
    """
    调用腾讯云 TTS，把文字合成为 mp3 文件。
    """
    secret_id = TENCENTCLOUD_SECRET_ID
    secret_key = TENCENTCLOUD_SECRET_KEY

    if not secret_id or not secret_key:
        raise RuntimeError("腾讯云 SecretId 或 SecretKey 没有配置")

    cred = credential.Credential(secret_id, secret_key)

    http_profile = HttpProfile()
    http_profile.endpoint = "tts.tencentcloudapi.com"

    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile

    client = tts_client.TtsClient(cred, "ap-guangzhou", client_profile)

    req = tts_models.TextToVoiceRequest()

    params = {
        "Text": text,
        "SessionId": str(uuid.uuid4()),
        "ModelType": 1,
        "VoiceType": 101001,
        "Codec": "mp3",
        "SampleRate": 16000,
        "Speed": 0,
        "Volume": 5,
        "PrimaryLanguage": 1
    }

    req.from_json_string(json.dumps(params, ensure_ascii=False))

    resp = client.TextToVoice(req)
    resp_json = json.loads(resp.to_json_string())

    audio_base64 = resp_json.get("Audio", "")
    if not audio_base64:
        raise RuntimeError("腾讯云 TTS 没有返回音频内容")

    with open(output_file, "wb") as f:
        f.write(base64.b64decode(audio_base64))


def speak(text):
    """
    使用腾讯云语音合成，并通过小车扬声器播放。
    """
    print("\nAI回答：", text)

    if not text:
        return

    try:
        chunks = split_text(text)
        for i, chunk in enumerate(chunks):
            audio_file = f"/tmp/raspbot_tencent_tts_{i}.mp3"

            print(f"正在合成第 {i + 1}/{len(chunks)} 段语音...")
            tencent_tts_to_mp3(chunk, audio_file)

            subprocess.run(
                ["mpg123", "-q", audio_file],
                check=True
            )
        return
    
    except Exception as e:
        print("腾讯云语音播报失败：", e)

    # 兜底：本地 espeak-ng
    try:
        subprocess.run(
            ["espeak-ng", "-v", "zh", "-s", "150", "--stdout", text],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )

        p1 = subprocess.Popen(
            ["espeak-ng", "-v", "zh", "-s", "150", "--stdout", text],
            stdout=subprocess.PIPE
        )

        subprocess.run(
            ["aplay", "-q"],
            stdin=p1.stdout,
            check=True
        )

    except Exception as e2:
        print("本地兜底语音播放也失败：", e2)
        print("请检查腾讯云密钥、网络和小车音频输出设备。")

# =========================
# 6. 主循环
# =========================

def main():
    print("使用腾讯云ASR在线语音识别：")

    speak("你好，我是智能小车语音助手。你可以开始向我提问。")

    while True:
        try:
            question = listen_once()
            if not question:
                speak("我没有听清楚，请再说一遍")
                continue
                
            if question in ["退出", "停止", "关闭", "结束"]:
                speak("好的，语音助手已关闭。")
                break

            answer = ask_dify(question)
            response_text, functions = parse_ai_answer(answer)
            speak(response_text)

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n用户手动退出。")
            speak("语音助手已退出。")
            break

        except Exception as e:
            print("程序异常：", e)
            speak("程序出现异常，请检查终端错误信息。")
            time.sleep(1)


if __name__ == "__main__":
    main()