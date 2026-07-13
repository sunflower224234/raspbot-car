# -*- coding: UTF-8 -*-
"""硬件自检模块。

检查项目：
1. I2C 总线及底盘控制板 0x2B 是否在线
2. 四个电机是否能正常响应
3. 循迹传感器是否可读取
4. 超声波传感器是否可读取
5. 摄像头是否可打开
6. 蜂鸣器和 RGB 灯功能测试
"""

from __future__ import annotations

import time


def run_hardware_check() -> bool:
    """执行完整硬件自检。

    Returns:
        所有硬件是否正常。
    """
    from raspbot_v2_lib import Raspbot, available_i2c_buses, scan_i2c_addresses, format_i2c_scan

    print("=" * 50)
    print("  RASPBOT-V2 硬件自检")
    print("=" * 50)
    all_ok = True

    # 1. I2C 总线检测
    print("\n[1/7] I2C 总线检测...")
    buses = available_i2c_buses()
    if not buses:
        print("  ❌ 未发现 I2C 总线！请检查：")
        print("     - sudo raspi-config → Interface → I2C → Enable")
        print("     - 重启后确认 /dev/i2c-* 存在")
        all_ok = False
    else:
        print(f"  ✅ 发现 I2C 总线：{buses}")
        scan = scan_i2c_addresses(buses)
        print(f"  设备列表：\n{format_i2c_scan(scan)}")

    # 2. 底盘控制板检测
    print("\n[2/7] 底盘控制板 (0x2B) 检测...")
    car = Raspbot()
    try:
        car.require_chassis()
        print(f"  ✅ 底盘在线：{car.backend_name()}")
    except Exception as exc:
        print(f"  ❌ 底盘检测失败：{exc}")
        all_ok = False
        car.close()
        return all_ok

    # 3. 电机测试
    print("\n[3/7] 电机测试（微小转动）...")
    try:
        car.Ctrl_Car(0, 0, 0)
        time.sleep(0.1)
        # 小幅度测试每个电机
        for motor_id, name in [(0, "左前"), (1, "左后"), (2, "右前"), (3, "右后")]:
            car.Ctrl_Muto(motor_id, 15)
            time.sleep(0.1)
            car.Ctrl_Muto(motor_id, 0)
        print("  ✅ 电机控制正常")
    except Exception as exc:
        print(f"  ❌ 电机测试失败：{exc}")
        all_ok = False

    # 4. 循迹传感器
    print("\n[4/7] 循迹传感器检测...")
    try:
        car.Ctrl_IR_Switch(1)
        time.sleep(0.1)
        sensors = car.read_line_sensors()
        print(f"  ✅ 循迹传感器可读取：{sensors}")
    except Exception as exc:
        print(f"  ❌ 循迹传感器异常：{exc}")
        all_ok = False

    # 5. 超声波传感器
    print("\n[5/7] 超声波传感器检测...")
    try:
        car.Ctrl_Ulatist_Switch(1)
        time.sleep(0.2)
        distance = car.read_ultrasonic_cm()
        print(f"  ✅ 超声波可读取，当前距离：{distance:.1f} cm")
    except Exception as exc:
        print(f"  ❌ 超声波传感器异常：{exc}")
        all_ok = False

    # 6. 摄像头检测
    print("\n[6/7] 摄像头检测...")
    try:
        import cv2
        for idx in range(2):
            cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    print(f"  ✅ 摄像头正常（index={idx}）")
                    break
        else:
            print("  ⚠️ 未检测到可用摄像头")
    except ImportError:
        print("  ⚠️ OpenCV 未安装，跳过摄像头检测")
    except Exception as exc:
        print(f"  ❌ 摄像头检测异常：{exc}")

    # 7. 蜂鸣器和 RGB 灯
    print("\n[7/7] 蜂鸣器和 RGB 灯测试...")
    try:
        car.beep(0.1)
        time.sleep(0.1)
        car.Ctrl_WQ2812_brightness_ALL(50, 0, 0)
        time.sleep(0.2)
        car.Ctrl_WQ2812_brightness_ALL(0, 50, 0)
        time.sleep(0.2)
        car.Ctrl_WQ2812_brightness_ALL(0, 0, 0)
        print("  ✅ 蜂鸣器和 RGB 灯正常")
    except Exception as exc:
        print(f"  ❌ 蜂鸣器/RGB 灯异常：{exc}")
        all_ok = False

    # 清理
    car.emergency_stop(repeats=3, interval=0.02)
    car.close()

    print("\n" + "=" * 50)
    print(f"  自检结果：{'✅ 全部正常' if all_ok else '❌ 存在异常，请排查'}")
    print("=" * 50)
    return all_ok


if __name__ == "__main__":
    run_hardware_check()
