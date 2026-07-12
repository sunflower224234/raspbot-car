# -*- coding: UTF-8 -*-
"""
RASPBOT-V2 无人外卖送餐车：二维码坐标识别 + 5×5 网格 A* 路径规划 + 四路循迹执行。

本版重点修复：
1. A* 从“只看最短步数”改成“最短步数 + 少转弯”，路线更适合黑线网格。
2. 运动执行从“检测到节点就立刻计数”改成“转向 → 离开当前路口 → 循迹到下一个路口 → 停车决策”。
3. 增加段超时、丢线保护、Ctrl+C/StopCar.py/stop flag 紧急停车。
4. 支持 route_map.json：可以声明哪些边没有贴黑线，避免 A* 规划到不存在的线。

二维码内容默认为 0~4 坐标，例如：4,2。
如果二维码写 1~5，请把 QR_COORDINATE_STARTS_AT_ONE 改成 True。
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
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import cv2
import requests

from raspbot_v2_lib import Raspbot
from runtime_guard import RaspbotTaskLock
from safety_control import (
    bind_car,
    clear_stop_request,
    install_signal_handlers,
    stop_requested,
    unbind_car,
)

# ======================== 地图与坐标配置 ========================
GRID_ROWS = 5
GRID_COLS = 5
START_NODE = (0, 0)
RETURN_NODE = (0, 0)
QR_COORDINATE_STARTS_AT_ONE = False
# 如果二维码写的是“列,行”，而程序内部需要“行,列”，把这个改成 True。
QR_SWAP_XY = False

# 方向编码：1 下，2 右，3 上，4 左。默认小车从仓库出发时车头朝下。
DOWN, RIGHT, UP, LEFT = 1, 2, 3, 4
DIR_DELTAS: Dict[int, Tuple[int, int]] = {
    DOWN: (1, 0),
    RIGHT: (0, 1),
    UP: (-1, 0),
    LEFT: (0, -1),
}

# route_map.json 可选格式：
# {
#   "obstacles": [[2,2]],
#   "blocked_edges": [[[0,1],[0,2]], [[3,3],[4,3]]]
# }
# 如果某两点之间没有贴黑线，把这条边写入 blocked_edges。
ROUTE_MAP_FILE = os.environ.get("RASPBOT_ROUTE_MAP", "route_map.json")
OBSTACLES: Set[Tuple[int, int]] = set()
BLOCKED_EDGES: Set[frozenset[Tuple[int, int]]] = set()

CAMERA_INDEX = int(os.environ.get("RASPBOT_CAMERA_INDEX", "0"))
SHOW_CAMERA_WINDOW = os.environ.get("RASPBOT_SHOW_CAMERA", "1") == "1" and bool(os.environ.get("DISPLAY"))

# ======================== 小车动作参数 ========================
# 之前速度偏高且转向完全靠时间，容易冲出黑线。先用保守值，跑稳后再加速。
LINE_SPEED = int(os.environ.get("RASPBOT_LINE_SPEED", "20"))
TURN_SPEED = int(os.environ.get("RASPBOT_TURN_SPEED", "35"))
ALIGN_TURN_SPEED = int(os.environ.get("RASPBOT_ALIGN_TURN_SPEED", "20"))
CORRECTION_TURN_SPEED = int(os.environ.get("RASPBOT_CORRECTION_TURN_SPEED", "15"))
SEARCH_LINE_SPEED = int(os.environ.get("RASPBOT_SEARCH_LINE_SPEED", "12"))

NODE_LEAVE_TIME = float(os.environ.get("RASPBOT_NODE_LEAVE_TIME", "0.28"))
# 点位识别不要一看到黑线就算到点：先离开旧路口，再看到普通直线，最后才允许判定新路口。
NODE_CONFIRM_COUNT = int(os.environ.get("RASPBOT_NODE_CONFIRM_COUNT", "3"))
NODE_IGNORE_TIME = float(os.environ.get("RASPBOT_NODE_IGNORE_TIME", "0.05"))
NODE_NORMAL_CONFIRM_COUNT = int(os.environ.get("RASPBOT_NODE_NORMAL_CONFIRM_COUNT", "3"))
NODE_RELEASE_TIMEOUT = float(os.environ.get("RASPBOT_NODE_RELEASE_TIMEOUT", "0.85"))
NODE_FORWARD_TIME = float(os.environ.get("RASPBOT_NODE_FORWARD_TIME", "0.05"))
NODE_CENTERING_TIME = float(os.environ.get("RASPBOT_NODE_CENTERING_TIME", "0.04"))
DEBUG_LINE_SENSOR = os.environ.get("RASPBOT_DEBUG_LINE", "0") == "1"
RIGHT_ANGLE_TIME = float(os.environ.get("RASPBOT_RIGHT_ANGLE_TIME", "0.20"))
U_TURN_TIME = float(os.environ.get("RASPBOT_U_TURN_TIME", "0.45"))
ALIGN_TIMEOUT = float(os.environ.get("RASPBOT_ALIGN_TIMEOUT", "1.20"))
SEGMENT_TIMEOUT = float(os.environ.get("RASPBOT_SEGMENT_TIMEOUT", "8.0"))
LOST_LINE_TIMEOUT = float(os.environ.get("RASPBOT_LOST_LINE_TIMEOUT", "1.20"))
BRAKE_TIME = float(os.environ.get("RASPBOT_BRAKE_TIME", "0.08"))

# A* 转弯惩罚。越大越倾向“一条线走到底再转弯”，更适合黑线地图。
TURN_PENALTY = int(os.environ.get("RASPBOT_ASTAR_TURN_PENALTY", "4"))


def _edge(a: Tuple[int, int], b: Tuple[int, int]) -> frozenset[Tuple[int, int]]:
    return frozenset((tuple(a), tuple(b)))


def load_route_map() -> None:
    """加载可选地图约束。没有 route_map.json 时默认完整 5×5 网格。"""
    global OBSTACLES, BLOCKED_EDGES
    OBSTACLES = set()
    BLOCKED_EDGES = set()
    if not os.path.exists(ROUTE_MAP_FILE):
        return
    try:
        with open(ROUTE_MAP_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        for item in data.get("obstacles", []):
            node = (int(item[0]), int(item[1]))
            validate_node(node, "route_map obstacle", allow_obstacle=True)
            OBSTACLES.add(node)
        for item in data.get("blocked_edges", []):
            a = (int(item[0][0]), int(item[0][1]))
            b = (int(item[1][0]), int(item[1][1]))
            validate_node(a, "blocked_edge a", allow_obstacle=True)
            validate_node(b, "blocked_edge b", allow_obstacle=True)
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
                raise ValueError(f"blocked_edges 只能写相邻节点：{a} - {b}")
            BLOCKED_EDGES.add(_edge(a, b))
        print(f"已加载路线地图：obstacles={sorted(OBSTACLES)}, blocked_edges={len(BLOCKED_EDGES)} 条")
    except Exception as exc:
        raise RuntimeError(f"读取 {ROUTE_MAP_FILE} 失败：{exc}") from exc


def heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def in_bounds(node: Tuple[int, int]) -> bool:
    return 0 <= node[0] < GRID_ROWS and 0 <= node[1] < GRID_COLS


def validate_node(node: Tuple[int, int], name: str, allow_obstacle: bool = False) -> None:
    if not in_bounds(node):
        raise ValueError(f"{name}={node} 超出地图范围；5×5 节点坐标只能是 0~4。")
    if not allow_obstacle and node in OBSTACLES:
        raise ValueError(f"{name}={node} 被设置为障碍物，不能作为起点或终点。")


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
    dx = nxt[0] - current[0]
    dy = nxt[1] - current[1]
    for head, delta in DIR_DELTAS.items():
        if delta == (dx, dy):
            return head
    raise ValueError(f"路径节点不相邻：{current} -> {nxt}")


def turn_delta(current_head: int, target_head: int) -> int:
    """返回转向角度：0 直行，90 右转，-90 左转，180 掉头。"""
    raw = (current_head - target_head) * 90
    if raw == 270:
        return -90
    if raw == -270:
        return 90
    return raw


def astar(start: Tuple[int, int], goal: Tuple[int, int], start_heading: int = DOWN) -> List[Tuple[int, int]]:
    """A* 路径规划。状态包含车头方向，用转弯惩罚减少拐弯。"""
    load_route_map()
    validate_node(start, "start")
    validate_node(goal, "goal")
    if start == goal:
        return [start]

    start_state = (start[0], start[1], start_heading)
    goal_nodes = set()
    open_heap: List[Tuple[int, int, int, Tuple[int, int, int]]] = []
    heapq.heappush(open_heap, (heuristic(start, goal), 0, 0, start_state))
    came_from: Dict[Tuple[int, int, int], Optional[Tuple[int, int, int]]] = {start_state: None}
    cost: Dict[Tuple[int, int, int], int] = {start_state: 0}
    tie = 0

    while open_heap:
        _, g, _, state = heapq.heappop(open_heap)
        x, y, head = state
        current = (x, y)
        if current == goal:
            path_states: List[Tuple[int, int, int]] = []
            s: Optional[Tuple[int, int, int]] = state
            while s is not None:
                path_states.append(s)
                s = came_from[s]
            path_states.reverse()
            return [(sx, sy) for sx, sy, _ in path_states]

        # 优先当前方向，再下/右/上/左。这样同分时更少 zigzag。
        direction_order = [head, DOWN, RIGHT, UP, LEFT]
        seen_dirs = []
        for d in direction_order:
            if d not in seen_dirs:
                seen_dirs.append(d)
        for new_head in seen_dirs:
            dx, dy = DIR_DELTAS[new_head]
            nxt = (x + dx, y + dy)
            if not road_exists(current, nxt):
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

    raise RuntimeError(f"A* 找不到从 {start} 到 {goal} 的可行路径；请检查 route_map.json 或黑线地图。")


def parse_qr_target(text: str) -> Tuple[int, int]:
    """
    解析二维码坐标。

    程序内部坐标是“行,列”：
    - 向下走：第一个数字 +1
    - 向右走：第二个数字 +1

    如果你们二维码贴纸写的是 1~5 坐标，把 QR_COORDINATE_STARTS_AT_ONE 改成 True。
    如果你们二维码写的是“列,行”，把 QR_SWAP_XY 改成 True。
    """
    import re

    nums = re.findall(r"-?\d+", text.strip())
    if len(nums) < 2:
        raise ValueError(f"二维码内容应包含两个数字，例如 4,2；当前内容是：{text!r}")

    x, y = int(nums[0]), int(nums[1])
    if QR_COORDINATE_STARTS_AT_ONE:
        x -= 1
        y -= 1

    if QR_SWAP_XY:
        target = (y, x)
    else:
        target = (x, y)

    validate_node(target, "二维码目标点")
    print(f"二维码原始内容：{text!r} -> 程序内部目标点：{target}")
    return target


def _run_route_delivery_impl():
    car = Raspbot()
    bind_car(car)
    install_signal_handlers()
    clear_stop_request()

    car_head = DOWN

    def ensure_not_stopped():
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
        ensure_not_stopped()
        car.Ctrl_Car(int(forward), 0, int(turn))

    def run(speed: int = LINE_SPEED):
        drive(speed, 0)

    def spin_left(speed: int = TURN_SPEED):
        drive(0, -speed)

    def spin_right(speed: int = TURN_SPEED):
        drive(0, speed)

    def brake(seconds: float = BRAKE_TIME):
        car.emergency_stop(repeats=3, interval=0.02)
        if seconds > 0:
            time.sleep(seconds)

    def read_line() -> Tuple[bool, bool, bool, bool]:
        line = car.read_line_sensors()
        return (line["left_1"], line["left_2"], line["right_1"], line["right_2"])

    def center_on_line(line_values: Sequence[bool]) -> bool:
        # False=黑线，True=白底。中间两个探头压线认为已对准。
        _, l2, r1, _ = line_values
        return (l2 is False) or (r1 is False)

    def black_count(line_values: Sequence[bool]) -> int:
        return sum(1 for v in line_values if v is False)

    def line_pattern(line_values: Sequence[bool]) -> str:
        # 调试用：黑线显示为 1，白底显示为 0，顺序 L1 L2 R1 R2。
        return "".join("1" if v is False else "0" for v in line_values)

    def is_cross_node(line_values: Sequence[bool]) -> bool:
        """
        点位候选判断。
        这里故意保留“较宽松”的候选规则，避免漏识别真实路口。
        真正防误判不靠把这里改很严，而靠 drive_to_next_node() 里的状态机：
        必须先离开旧路口、看到普通直线，再连续多帧看到候选路口，才算到点。
        """
        l1, l2, r1, r2 = line_values
        bc = black_count(line_values)

        # 典型十字 / T 字：三路或四路黑。
        if bc >= 3:
            return True

        # 有些场地黑线较窄，路口刚进入时可能只有中间两路 + 外侧瞬间触发。
        if (l2 is False and r1 is False and (l1 is False or r2 is False)):
            return True

        return False

    def is_normal_line_after_node(line_values: Sequence[bool]) -> bool:
        """
        离开路口后，应先看到普通直线，再允许识别下一个点。
        普通直线通常是中间一路/两路黑，且外侧不是同时大面积压黑。
        """
        l1, l2, r1, r2 = line_values
        bc = black_count(line_values)

        if bc == 0:
            return False

        # 中间压线，且不是明显路口。
        if not is_cross_node(line_values) and ((l2 is False) or (r1 is False)):
            return True

        # 偏一点的普通线：只有左侧或右侧一两个传感器压线，也算已经离开路口。
        if bc <= 2 and ((l1 is False) or (l2 is False) or (r1 is False) or (r2 is False)):
            return True

        return False

    last_bias = 0  # -1 左，1 右，用于丢线后找回

    def line_follow_step(line_values: Sequence[bool]):
        """更保守的循迹：前进 + 小角度转向，不再用左右平移。"""
        nonlocal last_bias
        l1, l2, r1, r2 = line_values
        left_black = (l1 is False) or (l2 is False)
        right_black = (r1 is False) or (r2 is False)
        black_count = sum(1 for v in line_values if v is False)

        if black_count >= 3:
            run(max(22, int(LINE_SPEED * 0.75)))
        elif l2 is False and r1 is False:
            run(LINE_SPEED)
            last_bias = 0
        elif left_black and not right_black:
            last_bias = -1
            drive(max(18, int(LINE_SPEED * 0.72)), -CORRECTION_TURN_SPEED)
        elif right_black and not left_black:
            last_bias = 1
            drive(max(18, int(LINE_SPEED * 0.72)), CORRECTION_TURN_SPEED)
        elif l1 is False:
            last_bias = -1
            drive(max(12, int(LINE_SPEED * 0.45)), -CORRECTION_TURN_SPEED - 10)
        elif r2 is False:
            last_bias = 1
            drive(max(12, int(LINE_SPEED * 0.45)), CORRECTION_TURN_SPEED + 10)
        else:
            # 全白：不要继续高速冲，按上一次偏差小幅找线。
            if last_bias < 0:
                drive(SEARCH_LINE_SPEED, -CORRECTION_TURN_SPEED)
            elif last_bias > 0:
                drive(SEARCH_LINE_SPEED, CORRECTION_TURN_SPEED)
            else:
                drive(SEARCH_LINE_SPEED, 0)

    def align_to_line(turn_sign: int, timeout: float = ALIGN_TIMEOUT) -> None:
        """转弯后慢速找新方向的黑线。turn_sign: -1 左转，1 右转。"""
        start = time.time()
        confirm = 0
        max_rotation_time = RIGHT_ANGLE_TIME * 0.5  # 限制最多再转45°
        correction_start = time.time()
        
        while time.time() - start < timeout:
            ensure_not_stopped()
            line = read_line()
            if center_on_line(line):
                confirm += 1
                if confirm >= 2:
                    brake(0.05)
                    return
            else:
                confirm = 0
            if turn_sign < 0:
                spin_left(ALIGN_TURN_SPEED)  # 继续左转！
            else:
                spin_right(ALIGN_TURN_SPEED)
            time.sleep(0.02)

    def turn_to(delta: int):
        if delta == 0:
            print("节点决策：直行")
            brake(0.03)
            return
        print({90: "节点决策：右转", -90: "节点决策：左转", 180: "节点决策：掉头", -180: "节点决策：掉头"}.get(delta, f"转向 {delta}"))
        
        run(max(20, int(LINE_SPEED * 0.65)))
        time.sleep(NODE_FORWARD_TIME)
        brake(0.03)
        
        if delta == 90:
            spin_right(TURN_SPEED)
            time.sleep(RIGHT_ANGLE_TIME)
            # 改为：微调找线，而不是继续大角度转
            fine_tune_align(1)  
        elif delta == -90:
            spin_left(TURN_SPEED)
            time.sleep(RIGHT_ANGLE_TIME)
            fine_tune_align(-1)
        elif abs(delta) == 180:
            spin_left(TURN_SPEED)
            time.sleep(U_TURN_TIME)
            fine_tune_align(-1)
        else:
            raise ValueError(f"非法转向角度：{delta}")

    def fine_tune_align(turn_sign: int):
        """微调对准：最多只转0.15秒，避免转太大"""
        start = time.time()
        max_time = 0.15  # 最多微调150ms
        while time.time() - start < max_time:
            line = read_line()
            if center_on_line(line):
                brake(0.03)
                return
            if turn_sign < 0:
                spin_left(int(ALIGN_TURN_SPEED * 0.6))
            else:
                spin_right(int(ALIGN_TURN_SPEED * 0.6))
            time.sleep(0.02)
        brake(0.03)
        print("微调结束，继续循迹")

    def leave_current_node():
        """
        离开当前点位。
        这一步只负责“脱离当前十字路口”，不负责识别下一个点。
        使用宽松路口候选 + 连续普通线确认，避免刚出发就把旧路口算成下一个点。
        """
        start = time.time()
        normal_hits = 0

        while time.time() - start < NODE_RELEASE_TIMEOUT:
            ensure_not_stopped()
            line = read_line()

            if is_normal_line_after_node(line):
                normal_hits += 1
                if normal_hits >= NODE_NORMAL_CONFIRM_COUNT:
                    return
            else:
                normal_hits = 0

            line_follow_step(line)
            time.sleep(0.02)

        # 兜底：如果传感器一直处于路口状态，继续按原来的时间方式离开，避免卡死。
        start = time.time()
        while time.time() - start < NODE_LEAVE_TIME:
            ensure_not_stopped()
            line_follow_step(read_line())
            time.sleep(0.02)
        print("警告：离开当前点位未稳定看到普通线，已用时间兜底继续。")

    def drive_to_next_node(next_node: Tuple[int, int]):
        """
        从当前路口沿黑线走到下一个路口。
        修正点：
        1. 保留宽松路口候选，避免漏识别。
        2. 加入状态机：必须先看到普通直线，才允许开始识别下一个点。
        3. 加入短时间屏蔽，避免刚离开旧路口就误判。
        """
        print(f"开始驶向节点 {next_node}")
        leave_current_node()

        start = time.time()
        last_seen_line = time.time()
        node_hits = 0
        normal_hits = 0
        ready_to_detect_node = False
        last_debug_time = 0.0

        while True:
            ensure_not_stopped()
            now = time.time()

            if now - start > SEGMENT_TIMEOUT:
                brake(0.2)
                raise RuntimeError(f"从上一节点到 {next_node} 超时：可能黑线断开、速度过快或转向没对准。")

            line = read_line()
            if DEBUG_LINE_SENSOR and now - last_debug_time > 0.12:
                print(f"line={line_pattern(line)} normal={is_normal_line_after_node(line)} cross={is_cross_node(line)} ready={ready_to_detect_node} hits={node_hits}")
                last_debug_time = now

            if center_on_line(line) or any(v is False for v in line):
                last_seen_line = now
            elif now - last_seen_line > LOST_LINE_TIMEOUT:
                brake(0.2)
                raise RuntimeError(f"驶向 {next_node} 时长时间丢线，请检查黑线宽度/循迹传感器高度/速度。")

            # 阶段1：刚离开旧路口时，不允许检测新点。
            # 必须先看到连续几帧普通直线，或者至少经过 NODE_IGNORE_TIME。
            if not ready_to_detect_node:
                if is_normal_line_after_node(line):
                    normal_hits += 1
                else:
                    normal_hits = 0

                if normal_hits >= NODE_NORMAL_CONFIRM_COUNT and now - start >= NODE_IGNORE_TIME:
                    ready_to_detect_node = True
                    node_hits = 0

                line_follow_step(line)
                time.sleep(0.02)
                continue

            # 阶段2：已经确认离开旧路口，可以识别下一个点位。
            if is_cross_node(line):
                node_hits += 1
                if node_hits >= NODE_CONFIRM_COUNT:
                    # 稍微前进到路口中心，避免刚碰到横线边缘就停车。
                    if NODE_CENTERING_TIME > 0:
                        run(max(18, int(LINE_SPEED * 0.60)))
                        time.sleep(NODE_CENTERING_TIME)
                    brake(0.08)
                    print(f"已到达节点 {next_node}")
                    return
            else:
                node_hits = 0

            line_follow_step(line)
            time.sleep(0.02)

    def march(path: List[Tuple[int, int]], initial_head: int = DOWN) -> int:
        """按路径执行，返回到达后的车头方向。"""
        nonlocal car_head
        car_head = initial_head
        if len(path) <= 1:
            brake(0.5)
            print("起点就是目标点。")
            return car_head

        print("规划路径：", " -> ".join(str(p) for p in path))
        for i in range(len(path) - 1):
            ensure_not_stopped()
            current_node = path[i]
            next_node = path[i + 1]
            target_head = direction_from_to(current_node, next_node)
            delta = turn_delta(car_head, target_head)
            print(f"当前节点 {current_node} -> 下一节点 {next_node}，车头 {car_head}，目标方向 {target_head}，转向 {delta}°")
            turn_to(delta)
            car_head = target_head
            drive_to_next_node(next_node)
        brake(0.5)
        print("已到达目标点：", path[-1])
        return car_head

    # ------------------------ 二维码识别 ------------------------
    qr_detector = cv2.QRCodeDetector()

    def detect_qr_target() -> Tuple[int, int]:
        print("请展示目标点二维码，格式例如：4,2")
        camera = cv2.VideoCapture(CAMERA_INDEX)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        camera.set(cv2.CAP_PROP_FPS, 30)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc("M", "J", "P", "G"))
        if not camera.isOpened():
            camera.release()
            raise RuntimeError(f"摄像头打开失败：CAMERA_INDEX={CAMERA_INDEX}。可尝试 export RASPBOT_CAMERA_INDEX=1")
        try:
            while True:
                ensure_not_stopped()
                ret, frame = camera.read()
                if not ret:
                    print("摄像头读取失败，继续尝试……")
                    time.sleep(0.1)
                    continue
                barcode_data, points, _ = qr_detector.detectAndDecode(frame)
                if points is not None and barcode_data:
                    print(f"识别到二维码：{barcode_data}")
                    return parse_qr_target(barcode_data)
                if SHOW_CAMERA_WINDOW:
                    cv2.imshow("QR Camera", frame)
                    if cv2.waitKey(10) & 0xFF == ord("q"):
                        raise KeyboardInterrupt("用户按 q 退出二维码识别。")
                else:
                    time.sleep(0.02)
        finally:
            camera.release()
            cv2.destroyAllWindows()

    def key_scan(msg: str = "按下回车继续"):
        ensure_not_stopped()
        try:
            input(msg)
        except EOFError:
            time.sleep(0.2)

    # ------------------------ 邮件/图像识别部分：保留原功能 ------------------------
    API_KEY_1 = os.environ.get("BAIDU_DISH_API_KEY", "GJJRHD1KEV8yH4Zxwci6C7XX")
    SECRET_KEY_1 = os.environ.get("BAIDU_DISH_SECRET_KEY", "YbB60pROnfFFGKSVm2qdVUheB44zKdcD")

    def get_access_token_1():
        if not API_KEY_1 or not SECRET_KEY_1:
            return None
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {"grant_type": "client_credentials", "client_id": API_KEY_1, "client_secret": SECRET_KEY_1}
        response = requests.post(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get("access_token")
        print("Failed to obtain access token", response.text)
        return None

    def recognize_dish(image_path: str):
        token = get_access_token_1()
        if not token:
            return "扬州炒饭"
        url = "https://aip.baidubce.com/rest/2.0/image-classify/v2/dish?access_token=" + token
        with open(image_path, "rb") as file:
            image_data = base64.b64encode(file.read()).decode("utf-8")
        response = requests.post(url, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={"image": image_data}, timeout=10)
        result = response.json()
        if result.get("result"):
            return result["result"][0].get("name", "扬州炒饭")
        return "扬州炒饭"

    def chat_with_gpt():
        return "我是智能外卖小管家，您的外卖已经送达。请及时取餐，祝您用餐愉快。"

    def get_img(image_path: str = "testimage.jpg"):
        camera = cv2.VideoCapture(CAMERA_INDEX)
        if not camera.isOpened():
            camera.release()
            print("拍照失败：摄像头未打开")
            return False
        ret, frame = camera.read()
        if ret:
            cv2.imwrite(image_path, frame)
            print("已经保存外卖照片：", image_path)
        camera.release()
        return bool(ret)

    def send_email(image_path: str = "testimage.jpg"):
        sender_email = os.environ.get("DELIVERY_SENDER_EMAIL", "2516803273@qq.com")
        sender_passcode = os.environ.get("DELIVERY_SENDER_PASSCODE", "gxhryzorpjtsdiej")
        receiver_email = os.environ.get("DELIVERY_RECEIVER_EMAIL", "3477314083@qq.com")
        if not sender_email or not sender_passcode or not receiver_email:
            print("未配置邮件环境变量，跳过邮件发送。")
            return
        email = MIMEMultipart()
        email["From"] = sender_email
        email["To"] = receiver_email
        email["Subject"] = "外卖送达通知"
        content = f"<body><p>{chat_with_gpt()}</p><p>外卖照片：</p><p><img src='cid:testimage'></p></body>"
        email.attach(MIMEText(content, "html", "utf-8"))
        if os.path.exists(image_path):
            with open(image_path, "rb") as fp:
                msg_image = MIMEImage(fp.read())
            msg_image.add_header("Content-ID", "testimage")
            email.attach(msg_image)
        server = smtplib.SMTP("smtp.qq.com", 25, timeout=15)
        server.ehlo()
        server.starttls()
        server.login(sender_email, sender_passcode)
        server.sendmail(sender_email, receiver_email, email.as_string())
        server.close()
        print("邮件发送成功！")

    def plan_and_drive(start: Tuple[int, int], target: Tuple[int, int], heading: int) -> int:
        path = astar(start, target, heading)
        return march(path, heading)

    # ------------------------ 主流程 ------------------------
    try:
        init_car()
        key_scan("按下回车开始二维码识别")
        target = detect_qr_target()
        print("目标点：", target)

        print("开始从仓库出发配送……")
        car_head = plan_and_drive(START_NODE, target, DOWN)

        key_scan("已到达目标点。按下回车拍照并发送邮件")
        if get_img():
            send_email()

        key_scan("准备返回仓库。按下回车继续")
        print("开始返回仓库……")
        car_head = plan_and_drive(target, RETURN_NODE, car_head)
        print("已经到达仓库门口，任务结束。")
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