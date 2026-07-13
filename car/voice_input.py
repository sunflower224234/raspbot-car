# -*- coding: UTF-8 -*-
"""小车端语音输入模块 —— 使用 USB 麦克风录音 + 腾讯云 ASR 识别。

依赖：
    - USB 麦克风（arecord -l 可见）
    - tencentcloud-sdk-python
    - 环境变量 TENCENT_SECRET_ID / TENCENT_SECRET_KEY

使用示例：
    from voice_input import record_and_recognize
    text = record_and_recognize(duration=3)
    print(f"识别结果：{text}")
"""

from __future__ import annotations

import os
import base64
import json
import subprocess
import tempfile
import uuid

from dotenv import load_dotenv
load_dotenv()  # 加载 .env 中的密钥


def _get_secret_id():
    return os.environ.get("TENCENT_SECRET_ID", "")


def _get_secret_key():
    return os.environ.get("TENCENT_SECRET_KEY", "")


def _get_record_device():
    return os.environ.get("RECORD_DEVICE", "")


def record_wav(duration: float = 3.0, output_file: str = None) -> str:
    """使用 arecord 录制 WAV 音频。

    Args:
        duration: 录音时长（秒）
        output_file: 输出文件路径，留空则使用临时文件

    Returns:
        WAV 文件路径
    """
    if output_file is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        output_file = tmp.name
        tmp.close()

    cmd = [
        "arecord", "-q",
        "-f", "S16_LE",       # 16-bit signed little-endian
        "-r", "16000",        # 16kHz 采样率（ASR 推荐）
        "-c", "1",            # 单声道
        "-d", str(max(1, int(duration))),
        output_file,
    ]

    # 如果指定了录音设备，插入 -D 参数
    if _get_record_device():
        cmd.insert(1, "-D")
        cmd.insert(2, _get_record_device())

    try:
        subprocess.run(cmd, check=True, timeout=int(duration) + 5)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"录音失败（arecord 返回 {exc.returncode}），请检查麦克风连接") from exc
    except subprocess.TimeoutExpired:
        raise RuntimeError("录音超时，请检查麦克风")

    return output_file


def recognize_wav(wav_file: str) -> str:
    """使用腾讯云 ASR 识别 WAV 文件中的语音。

    Args:
        wav_file: WAV 文件路径

    Returns:
        识别文本，失败返回空字符串
    """
    if not _get_secret_id() or not _get_secret_key():
        raise RuntimeError("未配置腾讯云 API 密钥（TENCENT_SECRET_ID / TENCENT_SECRET_KEY）")

    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.asr.v20190614 import asr_client, models

        with open(wav_file, "rb") as f:
            audio_data = f.read()

        if len(audio_data) < 800:  # 16kHz * 2bytes * 0.025s 太小
            return ""

        audio_base64 = base64.b64encode(audio_data).decode("utf-8")

        cred = credential.Credential(_get_secret_id(), _get_secret_key())
        http_profile = HttpProfile()
        http_profile.endpoint = "asr.tencentcloudapi.com"
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        client = asr_client.AsrClient(cred, "ap-shanghai", client_profile)

        req = models.SentenceRecognitionRequest()
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
        text = json.loads(resp.to_json_string()).get("Result", "").strip()
        return text

    except ImportError:
        raise RuntimeError("tencentcloud-sdk-python 未安装，请执行：pip install tencentcloud-sdk-python")
    except Exception as exc:
        print(f"[小车语音] ASR 识别失败：{exc}")
        return ""


def record_and_recognize(duration: float = 3.0) -> str:
    """录音并识别，一站式接口。

    Args:
        duration: 录音时长（秒），建议 3~5 秒

    Returns:
        识别到的文本，失败返回空字符串
    """
    wav_file = None
    try:
        wav_file = record_wav(duration=duration)
        text = recognize_wav(wav_file)
        return text
    except Exception as exc:
        print(f"[小车语音] 录音识别失败：{exc}")
        return ""
    finally:
        if wav_file and os.path.exists(wav_file):
            try:
                os.unlink(wav_file)
            except OSError:
                pass


if __name__ == "__main__":
    # 快速测试
    print("🎤 正在录音（3 秒）...")
    result = record_and_recognize(duration=3)
    if result:
        print(f"✅ 识别结果：{result}")
    else:
        print("❌ 未识别到语音内容")
