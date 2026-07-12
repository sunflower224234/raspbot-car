from __future__ import annotations

import heapq
from typing import Dict, Iterable, List, Optional, Tuple


Graph = Dict[str, Dict[str, int]]


GRAPH: Graph = {
    "S": {"P1": 1, "P2": 2},
    "P1": {"S": 1, "P3": 2, "A": 2},
    "P2": {"S": 2, "P3": 1, "C": 3},
    "P3": {"P1": 2, "P2": 1, "B": 1, "C": 2},
    "A": {"P1": 2},
    "B": {"P3": 1},
    "C": {"P2": 3, "P3": 2},
}


COORDS = {
    "S": {"x": 8, "y": 70},
    "P1": {"x": 30, "y": 42},
    "P2": {"x": 32, "y": 82},
    "P3": {"x": 58, "y": 58},
    "A": {"x": 70, "y": 26},
    "B": {"x": 84, "y": 58},
    "C": {"x": 78, "y": 86},
}


def heuristic(node: str, target: str) -> int:
    a = COORDS[node]
    b = COORDS[target]
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def astar(start: str, target: str, blocked: Optional[Iterable[str]] = None) -> dict:
    blocked_set = set(blocked or [])
    if start in blocked_set or target in blocked_set:
        return {
            "success": False,
            "path": [],
            "cost": None,
            "message": "起点或目标点被障碍阻塞",
        }

    queue: List[Tuple[int, int, str, List[str]]] = [(heuristic(start, target), 0, start, [start])]
    best_cost = {start: 0}

    while queue:
        _, cost, node, path = heapq.heappop(queue)
        if node == target:
            return {
                "success": True,
                "path": path,
                "cost": cost,
                "message": "路径规划成功",
            }

        for next_node, edge_cost in GRAPH.get(node, {}).items():
            if next_node in blocked_set:
                continue
            new_cost = cost + edge_cost
            if new_cost < best_cost.get(next_node, 10**9):
                best_cost[next_node] = new_cost
                priority = new_cost + heuristic(next_node, target)
                heapq.heappush(queue, (priority, new_cost, next_node, path + [next_node]))

    return {
        "success": False,
        "path": [],
        "cost": None,
        "message": "路径不可达，请重新选择目标或移除障碍节点",
    }
