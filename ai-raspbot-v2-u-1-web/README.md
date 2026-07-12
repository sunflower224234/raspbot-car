# RASPBOT-V2 多模态视觉 AI 小车控制台

这是一个面向计算机工程实践验收的 Flask Web 控制台原型。系统定位不是普通后台，而是 RASPBOT-V2 小车的统一调度中心，包含身份认证、任务下发、A* 路径规划、状态监控、异常处理、二维码校验和任务日志。

## 运行方式

```powershell
python -m pip install -r requirements.txt
python app.py
```

浏览器打开：

```text
http://127.0.0.1:5000/login
```

## 当前实现

- 人脸识别登录页：成功后进入控制台，失败触发 RGB 红灯和蜂鸣器报警状态。
- 主控制台：视频识别、任务下发、A* 地图、手动控制、传感器状态、任务日志。
- A* 节点地图：支持目标点 A/B/C，支持 blocked 节点重新规划。
- 任务状态机：`AUTH_REQUIRED -> IDLE -> PLANNING -> READY -> LINE_FOLLOW -> OBSTACLE_STOP -> ARRIVAL_CHECK -> DONE`。
- 模拟硬件接口：电机、超声波、巡线、RGB、蜂鸣器、人脸、二维码、手势、语音均有对应接口。
- 本地日志：任务过程写入 `data/tasks.json`。

## 真实硬件接入思路

官方 RASPBOT-V2 资料页包含 Python 编程、通信协议、Python 驱动库、电机、RGB、蜂鸣器、超声波、巡线、摄像头、人脸识别、二维码、手势识别等课程入口。当前 Web 已预留适配层：

- `robot_controller.py`：替换为官方电机/运动控制 API。
- `sensor_service.py`：替换为超声波、巡线传感器读取。
- `vision_service.py`：替换为摄像头、人脸、二维码、手势识别结果。
- `feedback_service.py`：替换为 RGB 灯和蜂鸣器控制。
- `task_manager.py`：保留状态机，负责统一调度。

推荐部署方式：

1. 把本项目放到树莓派或小车控制主机上运行。
2. 下载 Yahboom 官方资料包，把 Python 驱动库放到项目环境可导入的位置。
3. 在 `robot_controller.py` 中把 `command()` 映射到官方运动函数，例如前进、后退、左移、右移、转向、停止。
4. 在 `sensor_service.py` 中把 `distance_cm` 和 `line_bits` 改为真实传感器数据。
5. 在 `vision_service.py` 中把模拟识别结果改为 OpenCV/官方例程返回值。
6. 手机或电脑访问树莓派 IP，例如 `http://树莓派IP:5000`。

如果 Web 运行在电脑上、小车程序运行在树莓派上，建议让 Flask 后端通过 HTTP、WebSocket 或 TCP 向树莓派小车服务发送控制命令；不要让前端浏览器直接控制硬件。
