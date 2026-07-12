from __future__ import annotations


class VoiceService:
    def __init__(self) -> None:
        self.listening = False
        self.last_command = "未启动"

    def start(self, target: str = "B") -> dict:
        self.listening = True
        self.last_command = f"去 {target} 点"
        return {
            "success": True,
            "command": f"go_to_{target}",
            "target": target,
            "message": f"语音指令：去 {target} 点",
        }

    def stop(self) -> dict:
        self.listening = False
        self.last_command = "已停止监听"
        return {"success": True, "message": "语音控制已停止"}

    def status(self) -> dict:
        return {
            "voice_listening": self.listening,
            "voice_command": self.last_command,
        }
