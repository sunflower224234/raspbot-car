from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List


class LogService:
    def __init__(self, path: str = "data/tasks.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def _read(self) -> List[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def append(self, message: str, level: str = "info") -> dict:
        item = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level,
        }
        data = self._read()
        data.append(item)
        self.path.write_text(json.dumps(data[-200:], ensure_ascii=False, indent=2), encoding="utf-8")
        return item

    def list(self) -> List[dict]:
        return self._read()

    def clear(self) -> None:
        self.path.write_text("[]", encoding="utf-8")
