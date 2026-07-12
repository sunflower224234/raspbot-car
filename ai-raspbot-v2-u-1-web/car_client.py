from __future__ import annotations

"""小车 HTTP 客户端 —— PC 控制台通过它把命令转发到树莓派上的 car_server.py。"""

import os
import threading
from pathlib import Path
from typing import Optional

try:
    import requests
except Exception:
    requests = None  # type: ignore

try:
    from dotenv import load_dotenv
    _HERE = Path(__file__).resolve().parent
    for _env in (_HERE / ".env", _HERE.parent / ".env"):
        if _env.exists():
            load_dotenv(_env, override=False)
except Exception:
    pass


class CarClient:
    """线程安全的小车 HTTP 客户端，断线时静默失败并标记离线。"""

    def __init__(self) -> None:
        self.mode = os.getenv("RASPBOT_HARDWARE_MODE", "simulated").lower()
        self.base_url = os.getenv("RASPBOT_CAR_URL", "").rstrip("/")
        self.timeout = float(os.getenv("RASPBOT_CAR_TIMEOUT", "3"))
        self.online = False
        self.last_error = ""
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.mode == "remote" and bool(self.base_url) and requests is not None

    def _request(self, method: str, path: str, json: Optional[dict] = None) -> Optional[dict]:
        if not self.enabled:
            return None
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(method, url, json=json, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            with self._lock:
                self.online = True
                self.last_error = ""
            return data
        except Exception as exc:
            with self._lock:
                self.online = False
                self.last_error = str(exc)
            return None

    def get(self, path: str) -> Optional[dict]:
        return self._request("GET", path)

    def post(self, path: str, json: Optional[dict] = None) -> Optional[dict]:
        return self._request("POST", path, json=json)


_client: Optional[CarClient] = None
_client_lock = threading.Lock()


def get_car_client() -> CarClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = CarClient()
    return _client
