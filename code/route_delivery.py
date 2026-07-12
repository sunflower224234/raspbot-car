# -*- coding: UTF-8 -*-
"""RASPBOT-V2 无人外卖送餐车。

功能：二维码识别坐标 → 5×5 网格 A* 路径规划 → 循迹执行 → 到达拍照 → 邮件通知 → 返回仓库。

环境变量：
    RASPBOT_CAMERA_INDEX          摄像头编号（默认 0）
    RASPBOT_ROUTE_MAP             地图配置文件路径
    RASPBOT_LINE_SPEED            循迹速度
    DELIVERY_SENDER_EMAIL         发件邮箱
    DELIVERY_SENDER_PASSCODE      发件邮箱授权码
    DELIVERY_RECEIVER_EMAIL       收件邮箱
    QR_COORDINATE_STARTS_AT_ONE   二维码坐标是否从1开始（默认 0=从0开始）
    QR_SWAP_XY                    是否交换二维码行列
"""

from __future__ import annotations

import base64
import heapq
import json
import os
import smtplib
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Sequence, Set, Tuple

import cv2
import requests

from raspbot_v2_lib import Raspbot
from runtime_guard import RaspbotTaskLock
from safety_control import (
    bind_car, clear_stop_request, install_signal_handlers,
    stop_requested, unbind_car,
)

# ======================== 地图配置 ========================
GRID_ROWS = 5
GRID_COLS = 5
START_NODE = (0, 0)
RETURN_NODE = (0, 0)
QR_COORDINATE_STARTS_AT_ONE = os.environ.get("QR_COORDINATE_STARTS_AT_ONE", "0") == "1"
QR_SWAP_XY = os.environ.get("QR_SWAP_XY", "0") == "1"

# 方向编码：1=下, 2=右, 3=上, 4=左
DOWN, RIGHT, UP, LEFT = 1, 2, 3, 4
DIR_DELTAS: Dict[int, Tuple[int, int]] = {
    DOWN: (1, 0), RIGHT: (0, 1), UP: (-1, 0), LEFT: (0, -1),
}

ROUTE_MAP_FILE = os.environ.get("RASPBOT_ROUTE_MAP", "route_map.json")
OBSTACLES: Set[Tuple[int, int]] = set()
BLOCKED_EDGES: Set[frozenset] = set()

CAMERA_INDEX = int(os.environ.get("RASPBOT_CAMERA_INDEX", "0"))

# ======================== 动作参数 ========================
LINE_SPEED = int(os.environ.get("RASPBOT_LINE_SPEED", "20"))
TURN_SPEED = int(os.environ.get("RASPBOT_TURN_SPEED", "35"))
ALIGN_TURN_SPEED = int(os.environ.get("RASPBOT_ALIGN_TURN_SPEED", "20"))
CORRECTION_TURN_SPEED = int(os.environ.get("RASPBOT_CORRECTION_TURN_SPEED", "15"))
SEARCH_LINE_SPEED = int(os.environ.get("RASPBOT_SEARCH_LINE_SPEED", "12"))
TURN_PENALTY = int(os.environ.get("RASPBOT_ASTAR_TURN_PENALTY", "4"))

NODE_LEAVE_TIME = float(os.environ.get("RASPBOT_NODE_LEAVE_TIME", "0.28"))
NODE_CONFIRM_COUNT = int(os.environ.get("RASPBOT_NODE_CONFIRM_COUNT", "3"))
NODE_RELEASE_TIMEOUT = float(os.environ.get("RASPBOT_NODE_RELEASE_TIMEOUT", "0.85"))
NODE_CENTERING_TIME = float(os.environ.get("RASPBOT_NODE_CENTERING_TIME", "0.04"))
RIGHT_ANGLE_TIME = float(os.environ.get("RASPBOT_RIGHT_ANGLE_TIME", "0.20"))
U_TURN_TIME = float(os.environ.get("RASPBOT_U_TURN_TIME", "0.45"))
ALIGN_TIMEOUT = float(os.environ.get("RASPBOT_ALIGN_TIMEOUT", "1.20"))
SEGMENT_TIMEOUT = float(os.environ.get("RASPBOT_SEGMENT_TIMEOUT", "8.0"))
LOST_LINE_TIMEOUT = float(os.environ.get("RASPBOT_LOST_LINE_TIMEOUT", "1.20"))


def _edge(a: Tuple[int, int], b: Tuple[int, int]) -> frozenset:
    return frozenset((tuple(a), tuple(b)))


def load_route_map() -> None:
    """加载可选地图约束。"""
    global OBSTACLES, BLOCKED_EDGES
    OBSTACLES = set()
    BLOCKED_EDGES = set()
    if not os.path.exists(ROUTE_MAP_FILE):
        return
    try:
        with open(ROUTE_MAP_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        for item in data.get("obstacles", []):
            OBSTACLES.add((int(item[0]), int(item[1])))
        for item in data.get("blocked_edges", []):
            a = (int(item[0][0]), int(item[0][1]))
            b = (int(item[1][0]), int(item[1][1]))
            BLOCKED_EDGES.add(_edge(a, b))
        print(f"已加载地图：障碍{len(OBSTACLES)}个, 禁行边{len(BLOCKED_EDGES)}条")
    except Exception as exc:
        raise RuntimeError(f"读取 {ROUTE_MAP_FILE} 失败：{exc}") from exc


def heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def in_bounds(node: Tuple[int, int]) -> bool:
    return 0 <= node[0] < GRID_ROWS and 0 <= node[1] < GRID_COLS


def road_exists(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    if not in_bounds(a) or not in_bounds(b):
        return False
    if a in OBSTACLES or b in OBSTACLES:
        return False
    if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
        return False
    if _edge(a, b) in BLOCKED_EDGES:
        return False
    return True


def direction_from_to(current: Tuple[int, int], nxt: Tuple[int, int]) -> int:
    dx, dy = nxt[0] - current[0], nxt[1] - current[1]
    for head, delta in DIR_DELTAS.items():
        if delta == (dx, dy):
            return head
    raise ValueError(f"节点不相邻：{current} -> {nxt}")


def turn_delta(current_head: int, target_head: int) -> int:
    raw = (current_head - target_head) * 90
    if raw == 270:
        return -90
    if raw == -270:
        return 90
    return raw


def astar(start: Tuple[int, int], goal: Tuple[int, int],
          start_heading: int = DOWN) -> List[Tuple[int, int]]:
    """A* 路径规划（带车头方向和转弯惩罚）。"""
    load_route_map()
    if start == goal:
        return [start]

    start_state = (start[0], start[1], start_heading)
    open_heap: List[tuple] = []
    heapq.heappush(open_heap, (heuristic(start, goal), 0, 0, start_state))
    came_from: Dict = {start_state: None}
    cost: Dict = {start_state: 0}
    tie = 0

    while open_heap:
        _, g, _, state = heapq.heappop(open_heap)
        x, y, head = state
        if (x, y) == goal:
            path_states = []
            s = state
            while s is not None:
                path_states.append(s)
                s = came_from[s]
            path_states.reverse()
            return [(sx, sy) for sx, sy, _ in path_states]

        for new_head in [head, DOWN, RIGHT, UP, LEFT]:
            new_head = new_head
        seen = set()
        for new_head in [head, DOWN, RIGHT, UP, LEFT]:
            if new_head in seen:
                continue
            seen.add(new_head)
            dx, dy = DIR_DELTAS[new_head]
            nxt = (x + dx, y + dy)
            if not road_exists((x, y), nxt):
                continue
            turn_cost = 0 if new_head == head else TURN_PENALTY
            new_cost = g + 10 + turn_cost
            next_state = (nxt[0], nxt[1], new_head)
            if new_cost < cost.get(next_state, 10**9):
                cost[next_state] = new_cost
                came_from[next_state] = state
                tie += 1
                priority = new_cost + heuristic(nxt, goal) * 10
                heapq.heappush(open_heap, (priority, new_cost, tie, next_state))

    raise RuntimeError(f"A* 找不到从 {start} 到 {goal} 的路径。")


def parse_qr_target(text: str) -> Tuple[int, int]:
    """解析二维码坐标文本。"""
    import re
    nums = re.findall(r"-?\d+", text.strip())
    if len(nums) < 2:
        raise ValueError(f"二维码内容应包含两个数字，当前：{text!r}")
    x, y = int(nums[0]), int(nums[1])
    if QR_COORDINATE_STARTS_AT_ONE:
        x -= 1
        y -= 1
    target = (y, x) if QR_SWAP_XY else (x, y)
    print(f"二维码：{text!r} → 目标点：{target}")
    return target


# ==================== 主实现 ====================
def _run_route_delivery_impl():
    car = Raspbot()
    bind_car(car)
    install_signal_handlers()
    clear_stop_request()
    car_head = DOWN

    def check_stop():
        if stop_requested():
            raise KeyboardInterrupt("收到停止请求。")

    def init_car():
        car.require_chassis()
        print("底盘 I2C 正常：", car.backend_name())
        car.Ctrl_IR_Switch(1)
        car.Ctrl_Ulatist_Switch(1)
        car.Ctrl_BEEP_Switch(0)
        car.Ctrl_WQ2812_brightness_ALL(0, 0, 255)
        car.emergency_stop(repeats=3, interval=0.03)

    def drive(forward: int = 0, turn: int = 0):
        check_stop()
        car.Ctrl_Car(int(forward), 0, int(turn))

    def run(speed: int = LINE_SPEED):
        drive(speed, 0)

    def spin_left(speed: int = TURN_SPEED):
        drive(0, -speed)

    def spin_right(speed: int = TURN_SPEED):
        drive(0, speed)

    def brake(seconds: float = 0.08):
        car.emergency_stop(repeats=3, interval=0.02)
        if seconds > 0:
            time.sleep(seconds)

    def read_line() -> Tuple[bool, bool, bool, bool]:
        line = car.read_line_sensors()
        return (line["left_1"], line["left_2"], line["right_1"], line["right_2"])

    def on_line(line_vals: Sequence[bool]) -> bool:
        return (line_vals[1] is False) or (line_vals[2] is False)

    def black_cnt(line_vals: Sequence[bool]) -> int:
        return sum(1 for v in line_vals if v is False)

    def is_cross(line_vals: Sequence[bool]) -> bool:
        if black_cnt(line_vals) >= 3:
            return True
        l1, l2, r1, r2 = line_vals
        if l2 is False and r1 is False and (l1 is False or r2 is False):
            return True
        return False

    def is_normal_line(line_vals: Sequence[bool]) -> bool:
        if black_cnt(line_vals) == 0:
            return False
        if not is_cross(line_vals) and (line_vals[1] is False or line_vals[2] is False):
            return True
        if black_cnt(line_vals) <= 2:
            return True
        return False

    last_bias = 0

    def line_follow(line_vals: Sequence[bool]):
        nonlocal last_bias
        l1, l2, r1, r2 = line_vals
        left_ok = l1 is False or l2 is False
        right_ok = r1 is False or r2 is False
        bc = black_cnt(line_vals)

        if bc >= 3:
            run(max(22, int(LINE_SPEED * 0.75)))
        elif l2 is False and r1 is False:
            run(LINE_SPEED)
            last_bias = 0
        elif left_ok and not right_ok:
            last_bias = -1
            drive(max(18, int(LINE_SPEED * 0.72)), -CORRECTION_TURN_SPEED)
        elif right_ok and not left_ok:
            last_bias = 1
            drive(max(18, int(LINE_SPEED * 0.72)), CORRECTION_TURN_SPEED)
        elif l1 is False:
            last_bias = -1
            drive(max(12, int(LINE_SPEED * 0.45)), -CORRECTION_TURN_SPEED - 10)
        elif r2 is False:
            last_bias = 1
            drive(max(12, int(LINE_SPEED * 0.45)), CORRECTION_TURN_SPEED + 10)
        else:
            if last_bias < 0:
                drive(SEARCH_LINE_SPEED, -CORRECTION_TURN_SPEED)
            elif last_bias > 0:
                drive(SEARCH_LINE_SPEED, CORRECTION_TURN_SPEED)
            else:
                drive(SEARCH_LINE_SPEED, 0)

    def turn_to(delta: int):
        if delta == 0:
            print("节点决策：直行")
            brake(0.03)
            return
        label = {90: "右转", -90: "左转", 180: "掉头", -180: "掉头"}.get(delta, f"转向{delta}°")
        print(f"节点决策：{label}")

        run(max(20, int(LINE_SPEED * 0.65)))
        time.sleep(0.05)
        brake(0.03)

        if delta == 90:
            spin_right(TURN_SPEED)
        elif delta == -90:
            spin_left(TURN_SPEED)
        elif abs(delta) == 180:
            spin_left(TURN_SPEED)
            time.sleep(U_TURN_TIME - RIGHT_ANGLE_TIME)  # 掉头需要更久

        time.sleep(RIGHT_ANGLE_TIME)
        # 微调对准
        for _ in range(10):
            line = read_line()
            if on_line(line):
                break
            if delta > 0:
                spin_right(int(ALIGN_TURN_SPEED * 0.6))
            else:
                spin_left(int(ALIGN_TURN_SPEED * 0.6))
            time.sleep(0.02)
        brake(0.03)

    def leave_node():
        """离开当前路口。"""
        start = time.time()
        normal_hits = 0
        while time.time() - start < NODE_RELEASE_TIMEOUT:
            check_stop()
            line = read_line()
            if is_normal_line(line):
                normal_hits += 1
                if normal_hits >= 2:
                    return
            else:
                normal_hits = 0
            line_follow(line)
            time.sleep(0.02)
        # 兜底
        start = time.time()
        while time.time() - start < NODE_LEAVE_TIME:
            check_stop()
            line_follow(read_line())
            time.sleep(0.02)

    def drive_to_next_node(next_node: Tuple[int, int]):
        """从当前路口行驶到下一个路口。"""
        print(f"驶向节点 {next_node}")
        leave_node()

        start = time.time()
        last_seen = time.time()
        node_hits = 0
        normal_hits = 0
        ready = False

        while True:
            check_stop()
            now = time.time()

            if now - start > SEGMENT_TIMEOUT:
                brake(0.2)
                raise RuntimeError(f"驶向 {next_node} 超时")

            line = read_line()

            if on_line(line) or any(v is False for v in line):
                last_seen = now
            elif now - last_seen > LOST_LINE_TIMEOUT:
                brake(0.2)
                raise RuntimeError(f"驶向 {next_node} 时丢线")

            if not ready:
                if is_normal_line(line):
                    normal_hits += 1
                else:
                    normal_hits = 0
                if normal_hits >= 2 and now - start >= 0.05:
                    ready = True
                    node_hits = 0
                line_follow(line)
                time.sleep(0.02)
                continue

            if is_cross(line):
                node_hits += 1
                if node_hits >= NODE_CONFIRM_COUNT:
                    if NODE_CENTERING_TIME > 0:
                        run(max(18, int(LINE_SPEED * 0.60)))
                        time.sleep(NODE_CENTERING_TIME)
                    brake(0.08)
                    print(f"已到达节点 {next_node}")
                    return
            else:
                node_hits = 0

            line_follow(line)
            time.sleep(0.02)

    def march(path: List[Tuple[int, int]], initial_head: int = DOWN) -> int:
        nonlocal car_head
        car_head = initial_head
        if len(path) <= 1:
            print("起点就是目标点。")
            return car_head

        print("规划路径：", " → ".join(str(p) for p in path))
        for i in range(len(path) - 1):
            check_stop()
            cur, nxt = path[i], path[i + 1]
            target_head = direction_from_to(cur, nxt)
            delta = turn_delta(car_head, target_head)
            print(f"{cur} → {nxt}，车头{car_head}→{target_head}，转向{delta}°")
            turn_to(delta)
            car_head = target_head
            drive_to_next_node(nxt)

        brake(0.5)
        print("已到达目标：", path[-1])
        return car_head

    # ------------------------ 二维码识别 ------------------------
    qr_detector = cv2.QRCodeDetector()

    def detect_qr_target() -> Tuple[int, int]:
        print("请展示目标点二维码（格式如 4,2）...")
        camera = cv2.VideoCapture(CAMERA_INDEX)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        if not camera.isOpened():
            camera.release()
            raise RuntimeError(f"摄像头打开失败：CAMERA_INDEX={CAMERA_INDEX}")
        try:
            while True:
                check_stop()
                ret, frame = camera.read()
                if not ret:
                    time.sleep(0.1)
                    continue
                data, points, _ = qr_detector.detectAndDecode(frame)
                if points is not None and data:
                    print(f"识别到二维码：{data}")
                    target = parse_qr_target(data)
                    # 蜂鸣确认
                    car.Ctrl_BEEP_Switch(1)
                    time.sleep(0.1)
                    car.Ctrl_BEEP_Switch(0)
                    return target
                time.sleep(0.05)
        finally:
            camera.release()

    # ------------------------ 邮件通知 ------------------------
    def get_img(image_path: str = "testimage.jpg") -> bool:
        camera = cv2.VideoCapture(CAMERA_INDEX)
        if not camera.isOpened():
            print("拍照失败：摄像头未打开")
            return False
        ret, frame = camera.read()
        if ret:
            cv2.imwrite(image_path, frame)
            print("已保存照片：", image_path)
        camera.release()
        return bool(ret)

    def send_email(image_path: str = "testimage.jpg"):
        sender = os.environ.get("DELIVERY_SENDER_EMAIL", "")
        passcode = os.environ.get("DELIVERY_SENDER_PASSCODE", "")
        receiver = os.environ.get("DELIVERY_RECEIVER_EMAIL", "")
        if not all([sender, passcode, receiver]):
            print("未配置邮件环境变量，跳过邮件发送。")
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = receiver
            msg["Subject"] = "外卖送达通知"
            body = "<p>您的外卖已送达，请及时取餐！</p><p><img src='cid:delivery_img'></p>"
            msg.attach(MIMEText(body, "html", "utf-8"))
            if os.path.exists(image_path):
                with open(image_path, "rb") as fp:
                    img = MIMEImage(fp.read())
                img.add_header("Content-ID", "delivery_img")
                msg.attach(img)
            server = smtplib.SMTP("smtp.qq.com", 587, timeout=15)
            server.starttls()
            server.login(sender, passcode)
            server.sendmail(sender, receiver, msg.as_string())
            server.quit()
            print("邮件发送成功！")
        except Exception as exc:
            print(f"邮件发送失败：{exc}")

    def plan_and_drive(start: Tuple[int, int], target: Tuple[int, int],
                       heading: int) -> int:
        path = astar(start, target, heading)
        return march(path, heading)

    # ------------------------ 主流程 ------------------------
    try:
        init_car()
        input("按下回车开始二维码识别...")
        target = detect_qr_target()
        print("目标点：", target)

        print("开始从仓库出发配送...")
        car_head = plan_and_drive(START_NODE, target, DOWN)

        input("已到达目标点。按下回车拍照并发送邮件...")
        if get_img():
            send_email()

        input("准备返回仓库。按下回车继续...")
        print("开始返回仓库...")
        car_head = plan_and_drive(target, RETURN_NODE, car_head)
        print("已到达仓库，任务结束！")
    except KeyboardInterrupt as exc:
        print(f"任务已停止：{exc}")
    finally:
        car.emergency_stop(repeats=8, interval=0.02)
        unbind_car(car)
        car.close()


def run_route_delivery():
    with RaspbotTaskLock("route_delivery"):
        return _run_route_delivery_impl()


if __name__ == "__main__":
    run_route_delivery()
