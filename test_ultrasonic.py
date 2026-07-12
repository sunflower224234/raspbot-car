# -*- coding: UTF-8 -*-
"""超声波传感器诊断 —— 对比官方方式和库方式"""
import time
from raspbot_v2_lib import Raspbot

car = Raspbot()
car.require_chassis()
print(f"底盘: {car.backend_name()}")

# 开启超声波 + 等待初始化（和官方代码一致）
print("\n[1] 开启超声波 + 延时 0.15s...")
car.Ctrl_Ulatist_Switch(1)
time.sleep(0.15)

# 方式1: 库方法
print("\n[2] read_ultrasonic_cm() / read_ultrasonic_mm():")
for i in range(5):
    try:
        cm = car.read_ultrasonic_cm()
        mm = car.read_ultrasonic_mm()
        print(f"  [{i+1}] cm={cm}, mm={mm}")
    except Exception as e:
        print(f"  [{i+1}] 异常: {e}")
    time.sleep(0.3)

# 方式2: 直接读 I2C 寄存器（和官方代码一样）
print("\n[3] 直接读 I2C 寄存器 0x1A/0x1B:")
for i in range(5):
    try:
        diss_L = car.read_data_array(0x1a, 1)[0]
        diss_H = car.read_data_array(0x1b, 1)[0]
        dis = (diss_H << 8) | diss_L
        print(f"  [{i+1}] H=0x{diss_H:02X} L=0x{diss_L:02X} → {dis}mm = {dis/10:.1f}cm")
    except Exception as e:
        print(f"  [{i+1}] 异常: {e}")
    time.sleep(0.3)

# 方式3: 导入官方库测试
print("\n[4] 尝试官方库:")
try:
    from McLumk_Wheel_Sports import *
    bot.Ctrl_Ulatist_Switch(1)
    time.sleep(0.15)
    for i in range(5):
        try:
            diss_H = bot.read_data_array(0x1b, 1)[0]
            diss_L = bot.read_data_array(0x1a, 1)[0]
            dis = (diss_H << 8) | diss_L
            print(f"  [{i+1}] 官方 H=0x{diss_H:02X} L=0x{diss_L:02X} → {dis}mm")
        except Exception as e:
            print(f"  [{i+1}] 官方异常: {e}")
        time.sleep(0.3)
except ImportError:
    print("  McLumk_Wheel_Sports 未安装")
except Exception as e:
    print(f"  官方库初始化失败: {e}")

print("\n诊断完成。")
