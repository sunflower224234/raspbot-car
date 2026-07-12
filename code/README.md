# RASPBOT-V2 陪伴型智能家居小车

基于树莓派 + RASPBOT-V2 底盘的智能小车综合控制系统。

## 项目结构

```
代码/
├── raspbot_v2_lib.py          # 底层硬件驱动（I2C 电机/传感器/外设）
├── safety_control.py          # 安全控制（紧急停车/信号处理）
├── runtime_guard.py           # 任务互斥锁
│
├── line_follow_obstacle.py    # PID 循迹 + 超声波避障
├── obstacle_avoidance.py      # 连续绕桩避障（麦克纳姆轮横移）
├── route_delivery.py          # 二维码识别 + A* 送餐 + 邮件通知
│
├── face_recognition_only.py   # 人脸识别核心模块
├── FaceRecognition.py         # 人脸识别入口
├── face_delivery.py           # 人脸识别兼容入口
├── face_then_follow.py        # 人脸识别 → 循迹避障
│
├── weather_speaker.py         # 天气查询 + 语音播报
├── raspbot_voice_ai.py        # AI 语音聊天（ASR → AI → TTS）
├── voice_control_car.py       # 固定语音命令控制
├── voice_command_device.py    # 语音命令识别模块
├── speech_output.py           # 语音播报模块
├── voice_hardware_test.py     # 语音硬件测试
├── hardware_check.py          # 完整硬件自检
│
├── route_map.json             # 5×5 地图配置
├── requirements.txt           # Python 依赖
├── .env.example               # 环境变量模板
└── README.md                  # 本文件
```

## 快速开始

### 1. 硬件准备

确保树莓派已连接 RASPBOT-V2 底盘控制板：

```bash
# 开启 I2C
sudo raspi-config  # Interface → I2C → Enable

# 检测底盘
sudo i2cdetect -y 1
# 必须看到 0x2b 地址
```

### 2. 安装依赖

```bash
# 系统工具
sudo apt install -y i2c-tools python3-smbus python3-pip
sudo apt install -y espeak-ng mpg123 mplayer
sudo apt install -y libatlas-base-dev libopenjp2-7

# Python 包
pip3 install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的密钥
```

### 4. 运行功能

```bash
# 硬件自检（先跑这个！）
python3 hardware_check.py

# 循迹避障
python3 line_follow_obstacle.py

# 连续绕桩避障
python3 obstacle_avoidance.py

# 外卖送餐（需摄像头 + 二维码 + 黑线地图）
python3 route_delivery.py

# 人脸识别
python3 FaceRecognition.py

# 人脸识别 → 循迹
python3 face_then_follow.py

# 天气播报
python3 weather_speaker.py

# AI 语音聊天
python3 raspbot_voice_ai.py

# 语音命令控制
python3 voice_control_car.py
```

## 主要功能

| 功能 | 脚本 | 说明 |
|------|------|------|
| 硬件自检 | hardware_check.py | I2C/电机/传感器/摄像头全检 |
| 循迹避障 | line_follow_obstacle.py | PID 循迹 + 超声波避障 |
| 连续绕桩 | obstacle_avoidance.py | 横移绕桩 + 传感器回线 + 自动回正 |
| 外卖送餐 | route_delivery.py | 二维码识别 + A* 路径规划 + 邮件通知 |
| 人脸识别 | FaceRecognition.py | 百度AI/OpenCV 人脸检测 |
| AI 聊天 | raspbot_voice_ai.py | 语音输入 → AI 回复 → 语音播报 |
| 语音控制 | voice_control_car.py | 固定命令语音控制所有功能 |
| 天气播报 | weather_speaker.py | Open-Meteo 天气 + TTS + 钉钉推送 |

## 安全机制

- **任务互斥锁**：同一时间只有一个功能控制小车
- **紧急停车**：Ctrl+C / 停止标志文件 / signal 处理
- **丢线保护**：超时自动停车
- **底盘检测**：启动时验证 I2C 0x2B 在线

## 参数调优

所有参数通过环境变量控制，无需改代码：

```bash
# 降低循迹速度
export RASPBOT_LINE_SPEED=20

# 调整避障触发距离
export RASPBOT_OBSTACLE_DISTANCE_CM=25

# 电机方向修正（如果车原地转圈）
export RASPBOT_MOTOR_RF_POLARITY=-1
export RASPBOT_MOTOR_RR_POLARITY=-1
```
