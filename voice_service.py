# -*- coding: UTF-8 -*-
"""语音服务 —— 语音命令识别（对接 voice_command_device）。"""

from __future__ import annotations

import os


class VoiceService:
    def __init__(self) -> None:
        self.mode_name = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.listening = False
        self.last_command = "未启动"
        self._recognizer = None

    def start(self, target: str = "B") -> dict:
        """开始语音监听。"""
        self.listening = True

        if self.mode_name == "real":
            try:
                from voice_command_device import VoiceRecognizer, DEFAULT_CAR_KEYWORDS
                self._recognizer = VoiceRecognizer(DEFAULT_CAR_KEYWORDS)
                if self._recognizer.init():
                    self.last_command = f"去 {target} 点"
                    return {"success": True, "command": f"go_to_{target}",
                            "target": target, "message": f"语音指令：去 {target} 点"}
                else:
                    return {"success": False, "command": "",
                            "target": target, "message": "语音识别模块初始化失败"}
            except Exception as exc:
                return {"success": False, "command": "",
                        "target": target, "message": f"语音模块异常：{exc}"}

        # simulated
        self.last_command = f"去 {target} 点"
        return {"success": True, "command": f"go_to_{target}",
                "target": target, "message": f"语音指令：去 {target} 点（模拟）"}

    def stop(self) -> dict:
        self.listening = False
        self.last_command = "已停止监听"
        if self._recognizer:
            try:
                self._recognizer.close()
            except Exception:
                pass
            self._recognizer = None
        return {"success": True, "message": "语音控制已停止"}

    def listen_once(self) -> dict:
        """单次监听语音命令。"""
        if not self.listening or self._recognizer is None:
            return {"success": False, "command": "", "message": "语音服务未启动"}
        try:
            cmd = self._recognizer.listen_once()
            if cmd:
                self.last_command = cmd.label
                return {"success": True, "command": cmd.action,
                        "target": cmd.label, "message": f"识别到命令：{cmd.label}"}
            return {"success": False, "command": "", "message": "未识别到命令"}
        except Exception as exc:
            return {"success": False, "command": "", "message": f"语音识别异常：{exc}"}

    def status(self) -> dict:
        return {"voice_listening": self.listening,
                "voice_command": self.last_command}
