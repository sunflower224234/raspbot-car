# -*- coding: UTF-8 -*-
"""A* 路径规划模块。

支持两种地图模式：
1. graph 模式（Web 控制台默认）：预定义节点图 S→P1→P2→P3→A/B/C
2. grid 模式（5×5 网格）：route_delivery.py 使用的网格地图
"""

from __future__ import annotations

import heapq
import json
import os
from typing import Dict, Iterable, List, Optional, Set, Tuple

# ======================== 图模式（Web 控制台） ========================
GRAPH: Dict[str, Dict[str, int]] = {
    "S":  {"P1": 1, "P2": 2},
    "P1": {"S": 1, "P3": 2, "A": 2},
    "P2": {"S": 2, "P3": 1, "C": 3},
    "P3": {"P1": 2, "P2": 1, "B": 1, "C": 2},
    "A":  {"P1": 2},
    "B":  {"P3": 1},
    "C":  {"P2": 3, "P3": 2},
}

COORDS = {
    "S":  {"x": 8,  "y": 70},
    "P1": {"x": 30, "y": 42},
    "P2": {"x": 32, "y": 82},
    "P3": {"x": 58, "y": 58},
    "A":  {"x": 70, "y": 26},
    "B":  {"x": 84, "y": 58},
    "C":  {"x": 78, "y": 86},
}

# ======================== 网格模式（5×5 地图） ========================
GRID_ROWS = 5
GRID_COLS = 5
GRID_OBSTACLES: Set[Tuple[int, int]] = set()
GRID_BLOCKED: Set[frozenset] = set()
ROUTE_MAP_FILE = os.environ.get("RASPBOT_ROUTE_MAP", "route_map.json")

DIR_DELTAS = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # 下, 右, 上, 左


def _load_grid_map() -> None:
    """加载 5×5 网格地图配置。"""
    global GRID_OBSTACLES, GRID_BLOCKED
    GRID_OBSTACLES = set()
    GRID_BLOCKED = set()
    if not os.path.exists(ROUTE_MAP_FILE):
        return
    try:
        with open(ROUTE_MAP_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        for item in data.get("obstacles", []):
            GRID_OBSTACLES.add((int(item[0]), int(item[1])))
        for item in data.get("blocked_edges", []):
            a = (int(item[0][0]), int(item[0][1]))
            b = (int(item[1][0]), int(item[1][1]))
            GRID_BLOCKED.add(frozenset((a, b)))
    except Exception:
        pass


def _grid_neighbors(node: Tuple[int, int]) -> List[Tuple[int, int]]:
    neighbors = []
    for dr, dc in DIR_DELTAS:
        nr, nc = node[0] + dr, node[1] + dc
        if 0 <= nr < GRID_ROWS and 0 <= nc < GRID_COLS:
            nxt = (nr, nc)
            if nxt not in GRID_OBSTACLES and frozenset((node, nxt)) not in GRID_BLOCKED:
                neighbors.append(nxt)
    return neighbors


def _grid_heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _grid_astar(start_str: str, target_str: str) -> dict:
    """5×5 网格 A*。输入格式如 "0,0" "4,2"。"""
    _load_grid_map()
    try:
        sr, sc = map(int, start_str.split(","))
        tr, tc = map(int, target_str.split(","))
    except ValueError:
        return {"success": False, "path": [], "cost": None,
                "message": "网格坐标格式错误，请使用 row,col 格式"}

    start = (sr, sc)
    goal = (tr, tc)

    if start in GRID_OBSTACLES or goal in GRID_OBSTACLES:
        return {"success": False, "path": [], "cost": None,
                "message": "起点或目标点被障碍阻塞"}

    queue = [(_grid_heuristic(start, goal), 0, start, [start])]
    best_cost = {start: 0}

    while queue:
        _, cost, node, path = heapq.heappop(queue)
        if node == goal:
            return {"success": True,
                    "path": [f"{r},{c}" for r, c in path],
                    "cost": cost,
                    "message": "网格路径规划成功"}

        for nxt in _grid_neighbors(node):
            edge_cost = 10
            new_cost = cost + edge_cost
            if new_cost < best_cost.get(nxt, 10**9):
                best_cost[nxt] = new_cost
                priority = new_cost + _grid_heuristic(nxt, goal)
                heapq.heappush(queue, (priority, new_cost, nxt, path + [nxt]))

    return {"success": False, "path": [], "cost": None,
            "message": "网格路径不可达"}


# ======================== 图 A*（Web 控制台） ========================
def _graph_heuristic(node: str, target: str) -> int:
    a = COORDS[node]
    b = COORDS[target]
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def _graph_astar(start: str, target: str, blocked: Optional[Iterable[str]] = None) -> dict:
    blocked_set = set(blocked or [])
    if start in blocked_set or target in blocked_set:
        return {"success": False, "path": [], "cost": None,
                "message": "起点或目标点被障碍阻塞"}

    queue: List[Tuple[int, int, str, List[str]]] = [
        (_graph_heuristic(start, target), 0, start, [start])
    ]
    best_cost = {start: 0}

    while queue:
        _, cost, node, path = heapq.heappop(queue)
        if node == target:
            return {"success": True, "path": path, "cost": cost,
                    "message": "路径规划成功"}

        for next_node, edge_cost in GRAPH.get(node, {}).items():
            if next_node in blocked_set:
                continue
            new_cost = cost + edge_cost
            if new_cost < best_cost.get(next_node, 10**9):
                best_cost[next_node] = new_cost
                priority = new_cost + _graph_heuristic(next_node, target)
                heapq.heappush(queue, (priority, new_cost, next_node, path + [next_node]))

    return {"success": False, "path": [], "cost": None,
            "message": "路径不可达，请重新选择目标或移除障碍节点"}


# ======================== 路径导航指令 ========================
def get_path_directions(path: List[str]) -> List[dict]:
    """将路径节点序列转换为路口导航指令。

    返回列表，每个元素对应一个路径节点：
        {"node": "P1", "action": "straight"|"left"|"right"|"stop"}
    - 起点 S 固定为 "straight"
    - 终点固定为 "stop"
    - 中间节点根据前驱-当前-后继的位置关系计算转弯方向
    """
    import math

    if len(path) < 2:
        return []

    directions = []

    for i, node in enumerate(path):
        if i == 0:
            # 起点：直行出发
            directions.append({"node": node, "action": "straight"})
        elif i == len(path) - 1:
            # 终点：停车
            directions.append({"node": node, "action": "stop"})
        else:
            prev = path[i - 1]
            nxt = path[i + 1]
            # 计算转向角
            action = _compute_turn(prev, node, nxt)
            directions.append({"node": node, "action": action})

    return directions


def _compute_turn(from_node: str, at_node: str, to_node: str) -> str:
    """根据三个节点的坐标计算转弯方向。"""
    import math

    c_from = COORDS.get(from_node)
    c_at = COORDS.get(at_node)
    c_to = COORDS.get(to_node)

    if not all([c_from, c_at, c_to]):
        return "straight"

    # 进入方向：from → at
    entry_angle = math.atan2(
        c_at["y"] - c_from["y"],
        c_at["x"] - c_from["x"]
    )
    # 离开方向：at → to
    exit_angle = math.atan2(
        c_to["y"] - c_at["y"],
        c_to["x"] - c_at["x"]
    )

    # 转向角（弧度转角度）
    turn_deg = math.degrees(exit_angle - entry_angle)
    # 归一化到 -180 ~ 180
    while turn_deg > 180:
        turn_deg -= 360
    while turn_deg < -180:
        turn_deg += 360

    if abs(turn_deg) < 45:
        return "straight"
    elif turn_deg > 45:
        return "right"
    else:
        return "left"


# ======================== 统一入口 ========================
def astar(start: str, target: str, blocked: Optional[Iterable[str]] = None,
          mode: str = "auto") -> dict:
    """A* 路径规划统一入口。

    Args:
        start: 起点（图模式: "S"；网格模式: "0,0"）
        target: 目标点（图模式: "A"/"B"/"C"；网格模式: "4,2"）
        blocked: 阻塞节点列表（仅图模式使用）
        mode: "graph" / "grid" / "auto"（默认自动检测）

    Returns:
        {"success", "path", "cost", "message"}
    """
    if mode == "auto":
        # 自动检测：包含逗号 → 网格模式
        if "," in start or "," in target:
            mode = "grid"
        else:
            mode = "graph"

    if mode == "grid":
        return _grid_astar(start, target)
    return _graph_astar(start, target, blocked)
