#!/usr/bin/python3
# -*- coding: UTF-8 -*-
import sys
sys.path.append('/home/pi/project_demo/lib')
#导入麦克纳姆小车驱动库 Import Mecanum Car Driver Library
from McLumk_Wheel_Sports import *
​
# Constants related to the ultrasonic sensor
NEAR_DISTANCE = 200  # Define near distance threshold (millimeters)
FAR_DISTANCE = 425   # Define far distance threshold (millimeters)
​
def car_avoid():
​
​
    # 读取超声波传感器的距离 Reading distance from ultrasonic sensor
    diss_H =bot.read_data_array(0x1b,1)[0]
    diss_L =bot.read_data_array(0x1a,1)[0]
    dis = diss_H << 8 | diss_L 
​
    # 打印距离 Printing distance
    #print(f"Ultrasonic Distance: {dis} mm")
    time.sleep(0.05)  # 每隔1秒读取一次距离 Read the distance every 1 second
​
    if dis < NEAR_DISTANCE:
        print(f"Obstacle is very close, distance: {dis} mm")
        move_backward(speed)
        time.sleep(0.1)
    elif NEAR_DISTANCE <= dis <= FAR_DISTANCE:
        print(f"Obstacle is at medium distance, distance: {dis} mm")
        stop_robot()
        time.sleep(0.2)
        rotate_left(speed)
        time.sleep(0.15)
    elif FAR_DISTANCE < dis:
        print(f"No obstacle, distance: {dis} mm")
        move_forward(speed)
​
    else:
        print("Unknown situation, stopping")
        stop_robot()
        time.sleep(0.2)
​
speed = 20  # Set vehicle speed
​
try:
    # 打开超声波测距功能 Turn on the ultrasonic ranging function
    bot.Ctrl_Ulatist_Switch(1)
    time.sleep(0.1)  # 给超声波传感器一点时间来测量 Give the ultrasonic sensor some time to measure
    while True:
        car_avoid()
​
except KeyboardInterrupt:
    # When the user interrupts the program, ensure all motors stop
    bot.Ctrl_Ulatist_Switch(0)
    time.sleep(0.1)
    stop_robot()
    print("Ending")