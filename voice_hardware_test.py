# -*- coding: UTF-8 -*-
"""语音硬件测试模块。

测试麦克风录音和扬声器播报是否正常。
"""

from __future__ import annotations

import time


def run_voice_hardware_test(listen_seconds: int = 10) -> bool:
    """测试语音硬件（麦克风和扬声器）。

    Args:
        listen_seconds: 测试持续秒数。

    Returns:
        是否测试通过。
    """
    print("=" * 40)
    print("  语音硬件测试")
    print("=" * 40)

    all_ok = True

    # 1. 测试扬声器
    print("\n[1/3] 测试扬声器...")
    try:
        from speech_output import Speaker
        speaker = Speaker()
        speaker.speak("语音硬件测试开始", wait=True)
        print("  ✅ 扬声器正常")
    except Exception as exc:
        print(f"  ❌ 扬声器异常：{exc}")
        all_ok = False

    # 2. 测试麦克风录音
    print("\n[2/3] 测试麦克风录音...")
    try:
        import subprocess
        test_wav = "/tmp/raspbot_voice_test.wav"
        subprocess.run(
            ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
             "-d", "3", test_wav],
            check=True, timeout=10,
        )
        print(f"  ✅ 录音正常（文件：{test_wav}）")
    except FileNotFoundError:
        print("  ⚠️ arecord 未安装，跳过录音测试")
    except Exception as exc:
        print(f"  ❌ 录音异常：{exc}")
        all_ok = False

    # 3. 语音识别测试（如果有后端）
    print("\n[3/3] 测试语音识别...")
    try:
        from voice_command_device import VoiceRecognizer, DEFAULT_CAR_KEYWORDS

        recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)
        if recognizer.init():
            print(f"  请在 {listen_seconds} 秒内对麦克风说话...")
            start = time.time()
            while time.time() - start < listen_seconds:
                cmd = recognizer.listen_once()
                if cmd:
                    print(f"  ✅ 识别到命令：{cmd.label}")
                    speaker.speak(f"识别到命令：{cmd.label}", wait=True)
                    break
                time.sleep(1)
            else:
                print("  ⚠️ 未识别到命令（可能环境嘈杂或麦克风音量低）")
            recognizer.close()
        else:
            print("  ⚠️ 无可用的语音识别后端")
    except Exception as exc:
        print(f"  ❌ 语音识别异常：{exc}")
        all_ok = False

    print(f"\n测试结果：{'✅ 全部通过' if all_ok else '⚠️ 存在异常，请检查'}")
    return all_ok


if __name__ == "__main__":
    run_voice_hardware_test()
