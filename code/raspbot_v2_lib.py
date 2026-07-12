# -*- coding: UTF-8 -*-
"""RASPBOT-V2 硬件兼容层。

本文件将三轴控制接口：
    Raspbot().Ctrl_Car(forward, lateral, turn)
转换为 RASPBOT-V2 官方底盘的四电机 I2C 控制。

特性：
1. 启动时自动扫描 /dev/i2c-*，检测底盘控制板地址 0x2B。
2. 优先使用 smbus2 直连官方协议，避免反复打 I2C error。
3. 如果检测不到 0x2B，运动功能会给出明确错误提示。

环境变量：
    export RASPBOT_I2C_BUS=1
    export RASPBOT_I2C_ADDRESS=0x2b
    export RASPBOT_BACKEND=smbus          # smbus / official / auto
    export RASPBOT_MOTOR_POLARITY_PRESET=raspbot_v2
    export RASPBOT_LINE_ACTIVE_LOW=1      # LOW=黑线
    export RASPBOT_I2C_STRICT=0
"""

from __future__ import annotations

from dataclasses import dataclass
import glob
import os
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from Raspbot_Lib import Raspbot as OfficialRaspbot
except Exception:  # pragma: no cover
    OfficialRaspbot = None

try:
    from smbus2 import SMBus  # type: ignore
except Exception:  # pragma: no cover
    try:
        from smbus import SMBus  # type: ignore
    except Exception:  # pragma: no cover
        SMBus = None  # type: ignore


I2C_BUS = int(os.environ.get("RASPBOT_I2C_BUS", "1"))
I2C_ADDRESS = int(os.environ.get("RASPBOT_I2C_ADDRESS", "0x2b"), 16)
BACKEND_MODE = os.environ.get("RASPBOT_BACKEND", "smbus").strip().lower()
AUTO_SCAN = os.environ.get("RASPBOT_AUTO_SCAN", "1") != "0"
STRICT_I2C = os.environ.get("RASPBOT_I2C_STRICT", "0") == "1"
MOTOR_FORWARD_DIR = int(os.environ.get("RASPBOT_MOTOR_FORWARD_DIR", "0")) & 0x01

# 电机物理方向修正。RASPBOT-V2 右侧电机安装方向与左侧相反。
MOTOR_POLARITY_PRESET = os.environ.get(
    "RASPBOT_MOTOR_POLARITY_PRESET", "raspbot_v2"
).strip().lower()
if MOTOR_POLARITY_PRESET in {"legacy", "raw", "all_positive"}:
    _DEFAULT_MOTOR_POLARITY = {0: 1, 1: 1, 2: 1, 3: 1}
else:
    _DEFAULT_MOTOR_POLARITY = {0: 1, 1: 1, 2: -1, 3: -1}

MOTOR_POLARITY = {
    0: int(os.environ.get("RASPBOT_MOTOR_LF_POLARITY",
          str(_DEFAULT_MOTOR_POLARITY[0]))),
    1: int(os.environ.get("RASPBOT_MOTOR_LR_POLARITY",
          str(_DEFAULT_MOTOR_POLARITY[1]))),
    2: int(os.environ.get("RASPBOT_MOTOR_RF_POLARITY",
          str(_DEFAULT_MOTOR_POLARITY[2]))),
    3: int(os.environ.get("RASPBOT_MOTOR_RR_POLARITY",
          str(_DEFAULT_MOTOR_POLARITY[3]))),
}
MOTOR_ID_MAP = {
    0: int(os.environ.get("RASPBOT_MOTOR_ID_LF", "0")),
    1: int(os.environ.get("RASPBOT_MOTOR_ID_LR", "1")),
    2: int(os.environ.get("RASPBOT_MOTOR_ID_RF", "2")),
    3: int(os.environ.get("RASPBOT_MOTOR_ID_RR", "3")),
}

LINE_ACTIVE_LOW = os.environ.get("RASPBOT_LINE_ACTIVE_LOW", "1") != "0"

# 底盘寄存器定义（与官方 Raspbot_Lib 一致）
MOTOR_REG = 0x01
SERVO_REG = 0x02
RGB_ALL_REG = 0x03
RGB_ONE_REG = 0x04
IR_SWITCH_REG = 0x05
BEEP_SWITCH_REG = 0x06
ULTRASONIC_SWITCH_REG = 0x07
RGB_BRIGHTNESS_ALL_REG = 0x08
RGB_BRIGHTNESS_ONE_REG = 0x09
LINE_SENSOR_REG = 0x0A
ULTRASONIC_LOW_REG = 0x1A
ULTRASONIC_HIGH_REG = 0x1B


class RaspbotI2CError(RuntimeError):
    """底盘 I2C 通信异常。"""


@dataclass
class _MotorState:
    left_front: int = 0
    left_rear: int = 0
    right_front: int = 0
    right_rear: int = 0


def available_i2c_buses() -> List[int]:
    """返回系统中存在的 I2C 总线编号。"""
    buses: List[int] = []
    for path in glob.glob("/dev/i2c-*"):
        match = re.search(r"/dev/i2c-(\d+)$", path)
        if match:
            buses.append(int(match.group(1)))
    return sorted(set(buses))


def _probe_once(bus_id: int, address: int) -> bool:
    if SMBus is None:
        return False
    bus = None
    try:
        bus = SMBus(bus_id)
        try:
            bus.write_quick(address)
            return True
        except Exception:
            try:
                bus.read_byte(address)
                return True
            except Exception:
                return False
    except Exception:
        return False
    finally:
        if bus is not None:
            close = getattr(bus, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def scan_i2c_addresses(
    buses: Optional[Sequence[int]] = None,
) -> Dict[int, List[int]]:
    """扫描 I2C 设备地址。"""
    if buses is None:
        buses = available_i2c_buses()
    result: Dict[int, List[int]] = {}
    if SMBus is None:
        return {int(bus): [] for bus in buses}

    for bus_id in buses:
        found: List[int] = []
        for addr in range(0x03, 0x78):
            if _probe_once(int(bus_id), addr):
                found.append(addr)
        result[int(bus_id)] = found
    return result


def find_i2c_device(
    address: int = I2C_ADDRESS, preferred_bus: int = I2C_BUS
) -> Optional[int]:
    """在 I2C 总线中查找指定地址设备。"""
    buses = available_i2c_buses()
    ordered: List[int] = []
    for item in [preferred_bus, 1, 0, 11, 12, *buses]:
        if item not in ordered:
            ordered.append(item)
    for bus_id in ordered:
        if bus_id in buses and _probe_once(bus_id, address):
            return bus_id
    return None


def format_i2c_scan(scan: Dict[int, List[int]]) -> str:
    lines = []
    for bus_id in sorted(scan):
        addrs = " ".join(f"0x{addr:02x}" for addr in scan[bus_id]) or "无设备"
        lines.append(f"/dev/i2c-{bus_id}: {addrs}")
    return "\n".join(lines)


class Raspbot:
    """RASPBOT-V2 底盘硬件控制类。

    使用示例：
        car = Raspbot()
        car.Ctrl_Car(30, 0, 0)   # 前进
        car.Ctrl_Car(0, 20, 0)   # 右移
        car.Ctrl_Car(0, 0, 30)   # 右转
        car.stop()               # 停车
        car.close()              # 关闭
    """

    def __init__(self, bus: int = I2C_BUS, address: int = I2C_ADDRESS):
        self.requested_bus = int(bus)
        self.bus_id = int(bus)
        self.address = int(address)
        self._motor = _MotorState()
        self._bus = None
        self._official: Optional[object] = None
        self._last_error: Optional[str] = None
        self._error_count = 0
        self._device_present_cache: Optional[bool] = None

        found_bus = (
            find_i2c_device(self.address, self.requested_bus) if AUTO_SCAN else None
        )
        if found_bus is not None:
            self.bus_id = found_bus

        prefer_official = BACKEND_MODE == "official"
        prefer_smbus = BACKEND_MODE in {"smbus", "auto", ""}

        if prefer_smbus and SMBus is not None:
            try:
                self._bus = SMBus(self.bus_id)
            except Exception as exc:
                self._remember_error(f"打开 /dev/i2c-{self.bus_id} 失败：{exc}")
                self._bus = None

        if (self._bus is None or prefer_official) and OfficialRaspbot is not None:
            try:
                self._official = OfficialRaspbot()
            except Exception as exc:
                self._remember_error(f"官方 Raspbot_Lib 初始化失败：{exc}")
                self._official = None

    @staticmethod
    def _clamp_100(value: int) -> int:
        return max(-100, min(100, int(value)))

    @staticmethod
    def _to_official_speed(value: int) -> int:
        return max(-255, min(255, int(round(int(value) * 2.55))))

    @staticmethod
    def _speed_to_dir_pwm(speed: int) -> Tuple[int, int]:
        pwm = abs(max(-255, min(255, int(speed))))
        if int(speed) >= 0:
            motor_dir = MOTOR_FORWARD_DIR
        else:
            motor_dir = 1 - MOTOR_FORWARD_DIR
        return motor_dir & 0x01, pwm & 0xFF

    def _remember_error(self, message: str) -> None:
        self._last_error = message
        self._error_count += 1
        if self._error_count <= 3 or self._error_count in {10, 20, 50}:
            print(f"[RASPBOT I2C] {message}")

    def last_error(self) -> Optional[str]:
        return self._last_error

    def i2c_error_count(self) -> int:
        return self._error_count

    def is_chassis_present(self, refresh: bool = False) -> bool:
        if self._device_present_cache is not None and not refresh:
            return self._device_present_cache
        found = _probe_once(self.bus_id, self.address)
        self._device_present_cache = bool(found)
        return bool(found)

    def require_chassis(self) -> None:
        if self.is_chassis_present(refresh=True):
            return
        buses = available_i2c_buses()
        scan_text = (
            format_i2c_scan(scan_i2c_addresses(buses))
            if buses
            else "系统没有发现 /dev/i2c-*"
        )
        raise RaspbotI2CError(
            "未检测到 RASPBOT-V2 底盘控制板 I2C 地址 0x2B。\n"
            f"当前使用：/dev/i2c-{self.bus_id}, address=0x{self.address:02x}\n"
            "扫描结果：\n"
            f"{scan_text}\n"
            "处理建议：先确认底盘电源开关、电池、树莓派与扩展板排针/连接线、"
            "语音模块是否插反；然后执行 sudo i2cdetect -y 1，必须看到 2b。"
        )

    # -------------------- I2C 读写 --------------------
    def _write_block(self, register: int, data: List[int]) -> bool:
        payload = [int(item) & 0xFF for item in data]
        if self._bus is not None:
            try:
                self._bus.write_i2c_block_data(
                    self.address, int(register) & 0xFF, payload
                )
                return True
            except Exception as exc:
                self._remember_error(
                    f"write_i2c_block_data 失败：bus={self.bus_id}, "
                    f"addr=0x{self.address:02x}, reg=0x{register:02x}, "
                    f"data={payload}, err={exc}"
                )
                if STRICT_I2C:
                    raise RaspbotI2CError(str(exc)) from exc

        if self._official is not None and hasattr(self._official, "write_array"):
            try:
                self._official.write_array(register, payload)
                return True
            except Exception as exc:
                self._remember_error(
                    f"官方 write_array 失败：reg=0x{register:02x}, "
                    f"data={payload}, err={exc}"
                )
                if STRICT_I2C:
                    raise RaspbotI2CError(str(exc)) from exc

        return False

    def _read_block(self, register: int, length: int = 1) -> List[int]:
        if self._bus is not None:
            try:
                return list(
                    self._bus.read_i2c_block_data(
                        self.address, int(register) & 0xFF, int(length)
                    )
                )
            except Exception as exc:
                self._remember_error(
                    f"read_i2c_block_data 失败：bus={self.bus_id}, "
                    f"addr=0x{self.address:02x}, reg=0x{register:02x}, "
                    f"len={length}, err={exc}"
                )
                if STRICT_I2C:
                    raise RaspbotI2CError(str(exc)) from exc

        if self._official is not None and hasattr(self._official, "read_data_array"):
            try:
                data = self._official.read_data_array(register, length)
                if data is not None:
                    return list(data)
            except Exception as exc:
                self._remember_error(
                    f"官方 read_data_array 失败：reg=0x{register:02x}, "
                    f"len={length}, err={exc}"
                )
                if STRICT_I2C:
                    raise RaspbotI2CError(str(exc)) from exc

        return [0] * int(length)

    # -------------------- 电机控制 --------------------
    def Ctrl_Muto(self, motor_id: int, speed: int):
        """控制单个电机。motor_id: 0=左前, 1=左后, 2=右前, 3=右后。
        speed: -100~100。"""
        logical_motor_id = int(motor_id)
        speed = self._clamp_100(speed)
        if logical_motor_id == 0:
            self._motor.left_front = speed
        elif logical_motor_id == 1:
            self._motor.left_rear = speed
        elif logical_motor_id == 2:
            self._motor.right_front = speed
        elif logical_motor_id == 3:
            self._motor.right_rear = speed
        else:
            raise ValueError(f"Unsupported motor index: {motor_id}")

        physical_motor_id = MOTOR_ID_MAP.get(logical_motor_id, logical_motor_id)
        polarity = 1 if MOTOR_POLARITY.get(logical_motor_id, 1) >= 0 else -1
        corrected_speed = speed * polarity

        official_speed = self._to_official_speed(corrected_speed)
        motor_dir, pwm = self._speed_to_dir_pwm(official_speed)

        if self._write_block(MOTOR_REG, [physical_motor_id & 0xFF, motor_dir, pwm]):
            return

        if self._official is not None and hasattr(self._official, "Ctrl_Car"):
            try:
                self._official.Ctrl_Car(physical_motor_id, motor_dir, pwm)
                return
            except Exception as exc:
                self._remember_error(
                    f"官方 Ctrl_Car 失败：logical_motor={logical_motor_id}, "
                    f"physical_motor={physical_motor_id}, dir={motor_dir}, "
                    f"pwm={pwm}, err={exc}"
                )
                if STRICT_I2C:
                    raise RaspbotI2CError(str(exc)) from exc

        if STRICT_I2C:
            raise RaspbotI2CError("没有可用的 RASPBOT-V2 电机控制后端。")

    def Ctrl_Car(self, forward: int, lateral: int, turn: int):
        """麦克纳姆轮三轴控制。

        Args:
            forward: 前进/后退，-100~100（正=前进）
            lateral: 左移/右移，-100~100（正=右移）
            turn: 左转/右转，-100~100（正=右转）
        """
        forward = self._clamp_100(forward)
        lateral = self._clamp_100(lateral)
        turn = self._clamp_100(turn)

        left_front = self._clamp_100(forward + lateral + turn)
        left_rear = self._clamp_100(forward - lateral + turn)
        right_front = self._clamp_100(forward - lateral - turn)
        right_rear = self._clamp_100(forward + lateral - turn)

        self.Ctrl_Muto(0, left_front)
        self.Ctrl_Muto(1, left_rear)
        self.Ctrl_Muto(2, right_front)
        self.Ctrl_Muto(3, right_rear)

    # -------------------- 外设接口 --------------------
    def Ctrl_Servo(self, servo_id: int, angle: int):
        """控制舵机角度 0~180。"""
        angle = max(0, min(180, int(angle)))
        if (
            self._official is not None
            and hasattr(self._official, "Ctrl_Servo")
            and self._bus is None
        ):
            try:
                self._official.Ctrl_Servo(servo_id, angle)
                return
            except Exception as exc:
                self._remember_error(f"官方 Ctrl_Servo 失败：{exc}")
        self._write_block(SERVO_REG, [servo_id & 0xFF, angle & 0xFF])

    def Ctrl_WQ2812_brightness_ALL(self, red: int, green: int, blue: int):
        """设置全部 RGB LED 颜色 (0-255)。"""
        red = max(0, min(255, int(red)))
        green = max(0, min(255, int(green)))
        blue = max(0, min(255, int(blue)))
        self._write_block(RGB_BRIGHTNESS_ALL_REG, [red, green, blue])

    def Ctrl_WQ2812_brightness_ONE(
        self, led_index: int, red: int, green: int, blue: int
    ):
        """设置单个 RGB LED 颜色。"""
        self._write_block(
            RGB_BRIGHTNESS_ONE_REG,
            [led_index & 0xFF, red & 0xFF, green & 0xFF, blue & 0xFF],
        )

    def Ctrl_BEEP_Switch(self, on: int):
        """蜂鸣器开关。"""
        self._write_block(BEEP_SWITCH_REG, [1 if on else 0])

    def Ctrl_Ulatist_Switch(self, on: int):
        """超声波模块开关。"""
        self._write_block(ULTRASONIC_SWITCH_REG, [1 if on else 0])

    def Ctrl_IR_Switch(self, on: int):
        """红外循迹模块开关。"""
        self._write_block(IR_SWITCH_REG, [1 if on else 0])

    # -------------------- 传感器读取 --------------------
    def read_data_array(self, register: int, length: int = 1):
        """读取指定寄存器数据。"""
        return self._read_block(register, length)

    def read_line_sensor_mask(self) -> int:
        """读取循迹传感器原始掩码。"""
        data = self.read_data_array(LINE_SENSOR_REG, 1)
        return int(data[0]) & 0x0F

    def read_line_sensors(self):
        """读取四路循迹状态。约定：False=黑线, True=白底。"""
        mask = self.read_line_sensor_mask()

        def convert(bit: int) -> bool:
            raw_high = bool(mask & bit)
            return raw_high if LINE_ACTIVE_LOW else (not raw_high)

        return {
            "left_1": convert(0x08),
            "left_2": convert(0x04),
            "right_1": convert(0x02),
            "right_2": convert(0x01),
        }

    def read_ultrasonic_cm(self) -> float:
        """读取超声波距离（厘米）。"""
        low = self.read_data_array(ULTRASONIC_LOW_REG, 1)[0]
        high = self.read_data_array(ULTRASONIC_HIGH_REG, 1)[0]
        raw_distance = (int(high) << 8) | int(low)
        return float(raw_distance) if raw_distance > 0 else 0.0

    def read_ultrasonic_mm(self) -> int:
        """读取超声波距离（毫米）。"""
        low = self.read_data_array(ULTRASONIC_LOW_REG, 1)[0]
        high = self.read_data_array(ULTRASONIC_HIGH_REG, 1)[0]
        raw_distance = (int(high) << 8) | int(low)
        return int(raw_distance) if raw_distance > 0 else 0

    # -------------------- 状态与控制 --------------------
    def backend_name(self) -> str:
        if self._bus is not None:
            return f"smbus direct /dev/i2c-{self.bus_id} addr=0x{self.address:02x}"
        if self._official is not None:
            return "Official Raspbot_Lib"
        return "no hardware backend"

    def motor_config_text(self) -> str:
        return (
            f"polarity={{LF:{MOTOR_POLARITY[0]}, LR:{MOTOR_POLARITY[1]}, "
            f"RF:{MOTOR_POLARITY[2]}, RR:{MOTOR_POLARITY[3]}}}, "
            f"id_map={{LF:{MOTOR_ID_MAP[0]}, LR:{MOTOR_ID_MAP[1]}, "
            f"RF:{MOTOR_ID_MAP[2]}, RR:{MOTOR_ID_MAP[3]}}}, "
            f"line_active_low={1 if LINE_ACTIVE_LOW else 0}"
        )

    def stop(self):
        """立即停车。"""
        try:
            self.Ctrl_Muto(0, 0)
            self.Ctrl_Muto(1, 0)
            self.Ctrl_Muto(2, 0)
            self.Ctrl_Muto(3, 0)
        except Exception as exc:
            self._remember_error(f"stop 停车失败：{exc}")
            if STRICT_I2C:
                raise

    def emergency_stop(self, repeats: int = 5, interval: float = 0.03):
        """加强停车：连续多次写 0。"""
        for _ in range(max(1, int(repeats))):
            self.stop()
            if interval > 0:
                time.sleep(interval)

    def beep(self, duration: float = 0.1):
        """蜂鸣器响一声。"""
        self.Ctrl_BEEP_Switch(1)
        if duration > 0:
            time.sleep(duration)
        self.Ctrl_BEEP_Switch(0)

    def close(self):
        """安全关闭：先停车，再释放 I2C 资源。"""
        try:
            self.emergency_stop(repeats=5, interval=0.03)
        finally:
            if self._bus is not None:
                try:
                    self._bus.close()
                except Exception:
                    pass
                self._bus = None
