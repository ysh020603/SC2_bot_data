"""Pure Python fallback for the optional sc2pathlib native extension.

The upstream project normally ships a compiled ``sc2pathlib`` module.  This
repository does not include a Windows build, so tests on Windows need a small
compatible implementation.  It favors interface coverage over runtime speed.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np


Point = Tuple[int, int]
FloatPoint = Tuple[float, float]


def _point(value: Sequence[float]) -> Point:
    return int(round(value[0])), int(round(value[1]))


def _float_point(value: Sequence[float]) -> FloatPoint:
    return float(value[0]), float(value[1])


@dataclass
class VisionUnit:
    detector: bool
    flying: bool
    position: FloatPoint
    sight_range: float


class PathFind:
    def __init__(self, maze):
        self._original = self._coerce_map(maze)
        self._map = self._original.copy()
        self._influence = np.zeros_like(self._map, dtype=float)

    @staticmethod
    def _coerce_map(maze) -> np.ndarray:
        data = np.array(maze, dtype=np.int16)
        if data.ndim != 2:
            raise ValueError("PathFind map must be two-dimensional")
        return data.copy()

    @property
    def width(self) -> int:
        return int(self._map.shape[0])

    @property
    def height(self) -> int:
        return int(self._map.shape[1])

    @property
    def map(self) -> List[List[int]]:
        return self._map.tolist()

    @map.setter
    def map(self, data) -> None:
        self._map = self._coerce_map(data)
        self._original = self._map.copy()
        self._influence = np.zeros_like(self._map, dtype=float)

    def reset(self) -> None:
        self._map = self._original.copy()
        self._influence.fill(0)

    def normalize_influence(self, value: int) -> None:
        self._influence.fill(float(value))

    def create_block(self, center, size: Tuple[int, int]) -> None:
        self._set_block(center, size, 0)

    def create_blocks(self, centers, size: Tuple[int, int]) -> None:
        for center in centers:
            self.create_block(center, size)

    def remove_block(self, center, size: Tuple[int, int]) -> None:
        cx, cy = _point(center)
        for x, y in self._block_points(cx, cy, size):
            self._map[x, y] = self._original[x, y]

    def remove_blocks(self, centers, size: Tuple[int, int]) -> None:
        for center in centers:
            self.remove_block(center, size)

    def _set_block(self, center, size: Tuple[int, int], value: int) -> None:
        cx, cy = _point(center)
        for x, y in self._block_points(cx, cy, size):
            self._map[x, y] = value

    def _block_points(self, cx: int, cy: int, size: Tuple[int, int]) -> Iterable[Point]:
        sx, sy = max(1, int(size[0])), max(1, int(size[1]))
        left = cx - sx // 2
        top = cy - sy // 2
        for x in range(left, left + sx):
            for y in range(top, top + sy):
                if self._inside((x, y)):
                    yield x, y

    def _inside(self, point: Point) -> bool:
        x, y = point
        return 0 <= x < self.width and 0 <= y < self.height

    def _passable(self, point: Point, large: bool = False) -> bool:
        if not self._inside(point) or self._map[point] <= 0:
            return False
        if not large:
            return True
        x, y = point
        for nx in (x, x + 1):
            for ny in (y, y + 1):
                if not self._inside((nx, ny)) or self._map[nx, ny] <= 0:
                    return False
        return True

    @staticmethod
    def _heuristic(a: Point, b: Point) -> float:
        dx = abs(a[0] - b[0])
        dy = abs(a[1] - b[1])
        return max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)

    def _neighbors(self, point: Point, large: bool, window) -> Iterable[Tuple[Point, float]]:
        x, y = point
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nxt = (x + dx, y + dy)
                if window and not self._in_window(nxt, window):
                    continue
                if self._passable(nxt, large):
                    yield nxt, math.sqrt(2) if dx and dy else 1.0

    @staticmethod
    def _in_window(point: Point, window) -> bool:
        (x1, y1), (x2, y2) = window
        x_low, x_high = sorted((x1, x2))
        y_low, y_high = sorted((y1, y2))
        return x_low <= point[0] <= x_high and y_low <= point[1] <= y_high

    def find_path(
        self,
        start,
        end,
        large: bool = False,
        influence: bool = False,
        heuristic_accuracy: float = 1,
        window=None,
        distance_from_target: Optional[float] = None,
    ) -> Tuple[List[Point], float]:
        start_p = _point(start)
        end_p = _point(end)
        if not self._inside(start_p) or not self._inside(end_p):
            return [], math.inf
        if not self._passable(start_p, large):
            start_p = self._nearest_passable(start_p, large) or start_p
        if not self._passable(end_p, large):
            end_p = self._nearest_passable(end_p, large) or end_p

        frontier = [(0.0, start_p)]
        came_from = {start_p: None}
        cost_so_far = {start_p: 0.0}
        target = end_p

        while frontier:
            _, current = heapq.heappop(frontier)
            if current == end_p:
                target = current
                break
            if distance_from_target is not None and self._heuristic(current, end_p) <= distance_from_target:
                target = current
                break
            for nxt, step_cost in self._neighbors(current, large, window):
                extra = max(0.0, float(self._influence[nxt])) if influence else 0.0
                new_cost = cost_so_far[current] + step_cost + extra
                if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                    cost_so_far[nxt] = new_cost
                    priority = new_cost + self._heuristic(nxt, end_p) * max(0.01, heuristic_accuracy)
                    heapq.heappush(frontier, (priority, nxt))
                    came_from[nxt] = current
        else:
            return [], math.inf

        path: List[Point] = []
        current = target
        while current is not None:
            path.append(current)
            current = came_from[current]
        path.reverse()
        return path, cost_so_far.get(target, math.inf)

    def _nearest_passable(self, start: Point, large: bool) -> Optional[Point]:
        for radius in range(1, max(self.width, self.height)):
            for x in range(start[0] - radius, start[0] + radius + 1):
                for y in range(start[1] - radius, start[1] + radius + 1):
                    point = (x, y)
                    if self._passable(point, large):
                        return point
        return None

    def add_influence(self, points, value: float, distance: float) -> None:
        self._add_influence(points, value, distance, flat=False)

    def add_influence_flat(self, points, value: float, distance: float) -> None:
        self._add_influence(points, value, distance, flat=True)

    def add_walk_influence(self, points, value: float, distance: float) -> None:
        self._add_influence(points, value, distance, flat=False)

    def add_walk_influence_flat(self, points, value: float, distance: float) -> None:
        self._add_influence(points, value, distance, flat=True)

    def _add_influence(self, points, value: float, distance: float, flat: bool) -> None:
        radius = int(math.ceil(distance))
        for raw in points:
            cx, cy = _point(raw)
            for x in range(cx - radius, cx + radius + 1):
                for y in range(cy - radius, cy + radius + 1):
                    if not self._inside((x, y)):
                        continue
                    dist = math.hypot(x - cx, y - cy)
                    if dist <= distance:
                        factor = 1.0 if flat or distance == 0 else max(0.0, 1.0 - dist / distance)
                        self._influence[x, y] += float(value) * factor

    def lowest_influence(self, destination, radius: int) -> Tuple[Point, float]:
        center = _point(destination)
        best = center
        best_value = math.inf
        for x in range(center[0] - radius, center[0] + radius + 1):
            for y in range(center[1] - radius, center[1] + radius + 1):
                point = (x, y)
                if self._passable(point) and math.hypot(x - center[0], y - center[1]) <= radius:
                    value = float(self._influence[point])
                    if value < best_value:
                        best = point
                        best_value = value
        return best, best_value

    def lowest_influence_walk(self, destination, walk_distance: float) -> Tuple[Point, float]:
        return self.lowest_influence(destination, int(math.ceil(walk_distance)))

    def find_low_inside_walk(self, start, target, distance: float) -> Tuple[Point, float]:
        target_p = _point(target)
        start_p = _point(start)
        best = target_p
        best_score = math.inf
        radius = int(math.ceil(distance))
        for x in range(target_p[0] - radius, target_p[0] + radius + 1):
            for y in range(target_p[1] - radius, target_p[1] + radius + 1):
                point = (x, y)
                if self._passable(point):
                    score = abs(math.hypot(x - target_p[0], y - target_p[1]) - distance)
                    score += math.hypot(x - start_p[0], y - start_p[1]) * 0.01
                    score += max(0.0, float(self._influence[point]))
                    if score < best_score:
                        best = point
                        best_score = score
        return best, best_score


class Map:
    def __init__(self, pathing_grid, placement_grid, height_map, x1: int, y1: int, x2: int, y2: int):
        self.ground = PathFind(pathing_grid)
        self.reaper = PathFind(pathing_grid)
        self.colossus = PathFind(pathing_grid)
        self.air = PathFind(np.ones_like(np.array(pathing_grid, dtype=np.int16)))
        self.ground_pathing = self.ground.map
        self.reaper_pathing = self.reaper.map
        self.colossus_pathing = self.colossus.map
        self.air_pathing = self.air.map
        self.height_map = np.array(height_map)
        self.vision_map = np.zeros_like(np.array(pathing_grid, dtype=np.int16)).tolist()
        self.overlord_spots: List[FloatPoint] = []
        self.chokes: List[object] = []
        self.influence_colossus_map = False
        self.influence_reaper_map = False
        self._zones = {}
        self._connected = set()

    def _pf(self, map_type) -> PathFind:
        value = int(map_type)
        if value == 1:
            return self.reaper
        if value == 2:
            return self.colossus
        if value == 3:
            return self.air
        return self.ground

    def reset(self) -> None:
        for pf in (self.ground, self.reaper, self.colossus, self.air):
            pf.reset()

    def normalize_influence(self, value: int) -> None:
        for pf in (self.ground, self.reaper, self.colossus, self.air):
            pf.normalize_influence(value)

    def create_block(self, center, size: Tuple[int, int]) -> None:
        for pf in (self.ground, self.reaper, self.colossus):
            pf.create_block(center, size)

    def create_blocks(self, centers, size: Tuple[int, int]) -> None:
        for center in centers:
            self.create_block(center, size)

    def remove_block(self, center, size: Tuple[int, int]) -> None:
        for pf in (self.ground, self.reaper, self.colossus):
            pf.remove_block(center, size)

    def remove_blocks(self, centers, size: Tuple[int, int]) -> None:
        for center in centers:
            self.remove_block(center, size)

    def find_path(self, map_type, start, end, large=False, influence=False, heuristic_accuracy=1, window=None, distance_from_target=None):
        return self._pf(map_type).find_path(
            _point(start), _point(end), large, influence, heuristic_accuracy, window, distance_from_target
        )

    def lowest_influence(self, map_type, destination, radius: int):
        return self._pf(map_type).lowest_influence(destination, radius)

    def lowest_influence_walk(self, map_type, destination, walk_distance: float):
        return self._pf(map_type).lowest_influence_walk(destination, walk_distance)

    def find_low_inside_walk(self, map_type, start, target, distance: float):
        return self._pf(map_type).find_low_inside_walk(start, target, distance)

    def add_influence_walk(self, points, value: float, distance: float) -> None:
        self.ground.add_walk_influence(points, value, distance)

    def add_influence_flat_hollow(self, points, value: float, min_range: float, max_range: float) -> None:
        self.ground.add_influence_flat(points, value, max_range)

    def add_influence_fading(self, maps_type, points, value: float, full_range: float, fade_max_range: float) -> None:
        for pf in self._pfs_for_maps_type(maps_type):
            pf.add_influence(points, value, fade_max_range)

    def _pfs_for_maps_type(self, maps_type):
        value = int(maps_type)
        if value == 0:
            return (self.ground,)
        if value == 2:
            return (self.air,)
        return (self.ground, self.air)

    def current_influence(self, map_type, position) -> float:
        pf = self._pf(map_type)
        point = _point(position)
        if not pf._inside(point):
            return math.inf
        return float(pf._influence[point])

    def add_influence_without_zones(self, zones, value: int) -> None:
        zone_set = set(zones)
        for (x, y), zone in self._zones.items():
            if zone not in zone_set and self.ground._inside((x, y)):
                self.ground._influence[x, y] += value

    def calculate_zones(self, sorted_base_locations) -> None:
        self._zones = {_point(position): index + 1 for index, position in enumerate(sorted_base_locations)}

    def get_zone(self, position) -> int:
        point = _point(position)
        if point in self._zones:
            return self._zones[point]
        if not self._zones:
            return 0
        return min(self._zones.items(), key=lambda item: math.hypot(item[0][0] - point[0], item[0][1] - point[1]))[1]

    def calculate_connections(self, start) -> None:
        start_p = _point(start)
        self._connected = set()
        frontier = [start_p]
        while frontier:
            current = frontier.pop()
            if current in self._connected or not self.ground._passable(current):
                continue
            self._connected.add(current)
            for nxt, _ in self.ground._neighbors(current, False, None):
                if nxt not in self._connected:
                    frontier.append(nxt)

    def is_connected(self, start) -> bool:
        return _point(start) in self._connected

    def remove_connection(self, start) -> bool:
        point = _point(start)
        if point in self._connected:
            self._connected.remove(point)
            return True
        return False

    def clear_vision(self) -> None:
        self.vision_map = np.zeros_like(np.array(self.vision_map, dtype=np.int16)).tolist()

    def add_vision_unit(self, vision_unit: VisionUnit) -> None:
        cx, cy = _point(vision_unit.position)
        radius = int(math.ceil(vision_unit.sight_range))
        arr = np.array(self.vision_map, dtype=np.int16)
        for x in range(cx - radius, cx + radius + 1):
            for y in range(cy - radius, cy + radius + 1):
                if 0 <= x < arr.shape[0] and 0 <= y < arr.shape[1] and math.hypot(x - cx, y - cy) <= radius:
                    arr[x, y] = 2 if vision_unit.detector else 1
        self.vision_map = arr.tolist()

    def calculate_vision_map(self) -> None:
        return None

    def add_influence_to_vision(self, map_type, seen_value: int, detection_value: int) -> None:
        pf = self._pf(map_type)
        for x, row in enumerate(self.vision_map):
            for y, status in enumerate(row):
                if status == 1:
                    pf._influence[x, y] += seen_value
                elif status == 2:
                    pf._influence[x, y] += detection_value

    def vision_status(self, position) -> int:
        point = _point(position)
        arr = np.array(self.vision_map, dtype=np.int16)
        if 0 <= point[0] < arr.shape[0] and 0 <= point[1] < arr.shape[1]:
            return int(arr[point])
        return 0

    def draw_climbs(self):
        return self.ground.map

    def draw_chokes(self):
        return self.ground.map

    def draw_zones(self):
        arr = np.zeros_like(self.ground._map)
        for point, zone in self._zones.items():
            if self.ground._inside(point):
                arr[point] = zone
        return arr.tolist()
