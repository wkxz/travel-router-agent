"""高德路网通勤数据：给排程引擎提供真实的步行 / 驾车耗时（engine.travel.TravelTimes 的实现）。

接口 /v3/distance（2026-10-22 实测）：
- type=3 步行、type=1 驾车，返回路网距离（米）与耗时（秒）；
- 一次请求可带多个起点（origins 用 | 分隔）、一个终点；
- 起点最多 100 个：传 101 个时只返回前 100 条，超出的静默丢弃，所以按 100 个一批请求。

请求策略：按「终点」取一整列（所有已知地点 → 该终点），同一终点后续的查询直接命中缓存；
直线距离已超过步行上限的，步行路程只会更长，跳过步行请求直接查驾车。
取不到数据（限流、网络、Key 问题、超过请求上限）的路段回退为直线估算，并在描述和行程备注里标明。
"""

import math
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .amap import amap_get
from ..engine.geo import haversine_distance, WALKING_MAX_ROAD_KM
from ..engine.models import LocationPoint
from ..engine.travel import Leg, TravelTimes, is_roundabout

Fetch = Callable[[str, dict], dict]

WALK, DRIVE = "3", "1"
MAX_ORIGINS = 100
MAX_REQUESTS = 80     # 单次排程的路网请求上限（节流 0.35s/次，约 30~40 秒），超出的路段按估算
TAXI_WAIT_MIN = 5     # 打车的等车/上下车时间，与直线估算口径一致
MIN_WALK_MIN = 3
MIN_DRIVE_MIN = 8
ROUNDABOUT_NOTE = "；路程远超直线距离，可能隔水或需绕行（如需轮渡），以地图导航为准"


class AmapTravel(TravelTimes):
    """单次排程用的路网缓存；points 为本次行程可能用到的全部地点，用来批量取一整列。"""

    def __init__(self, points: Iterable[LocationPoint], fetch: Optional[Fetch] = None):
        self._fetch = fetch
        self._points: List[str] = list(dict.fromkeys(p.location_str for p in points))
        self._matrix: Dict[Tuple[str, str, str], Optional[Tuple[float, float]]] = {}
        self._legs: Dict[Tuple[str, str, str], Leg] = {}
        self.requests = 0
        self._estimated_legs = 0
        self._errors: List[str] = []

    # ---------- 路网矩阵 ----------

    def _lookup(self, type_: str, origin: str, dest: str) -> Optional[Tuple[float, float]]:
        """(路网距离 m, 耗时 s)；取不到时返回 None。"""
        key = (type_, origin, dest)
        if key not in self._matrix:
            self._fetch_column(type_, origin, dest)
        return self._matrix.get(key)

    def _fetch_column(self, type_: str, origin: str, dest: str) -> None:
        batch = [origin] + [p for p in self._points
                            if p not in (origin, dest) and (type_, p, dest) not in self._matrix]
        batch = batch[:MAX_ORIGINS]
        if self.requests >= MAX_REQUESTS:
            self._matrix[(type_, origin, dest)] = None
            self._note_error(f"路网请求超过 {MAX_REQUESTS} 次上限")
            return
        self.requests += 1
        data = (self._fetch or amap_get)("/distance", {
            "origins": "|".join(batch), "destination": dest, "type": type_,
        })
        if "error" in data:
            self._matrix[(type_, origin, dest)] = None  # 只标记本段，其他起点以后仍可重试
            self._note_error(data["error"])
            return
        for p in batch:
            self._matrix.setdefault((type_, p, dest), None)
        for r in data.get("results") or []:
            try:
                idx = int(r.get("origin_id")) - 1
                value = (float(r["distance"]), float(r["duration"]))
            except (TypeError, ValueError, KeyError):
                continue  # 单条无结果（如步行无路线）时高德返回 code/info，按取不到处理
            if 0 <= idx < len(batch):
                self._matrix[(type_, batch[idx], dest)] = value

    def _note_error(self, msg: str) -> None:
        if msg not in self._errors:
            self._errors.append(msg)

    # ---------- TravelTimes 接口 ----------

    def leg(self, a: LocationPoint, b: LocationPoint, mode: str = "auto") -> Leg:
        o, d = a.location_str, b.location_str
        key = (o, d, mode)
        if key not in self._legs:
            self._legs[key] = self._compute(a, b, o, d, mode)
        return self._legs[key]

    def _compute(self, a: LocationPoint, b: LocationPoint, o: str, d: str, mode: str) -> Leg:
        if o == d:
            return self.estimate(a, b, mode)  # 同一坐标（如景区内的餐厅），不必请求
        straight_km = haversine_distance(a.lng, a.lat, b.lng, b.lat)

        if mode == "walking" or (mode == "auto" and straight_km <= WALKING_MAX_ROAD_KM):
            walk = self._lookup(WALK, o, d)
            if walk and (mode == "walking" or walk[0] / 1000 <= WALKING_MAX_ROAD_KM):
                km = walk[0] / 1000
                minutes = max(MIN_WALK_MIN, math.ceil(walk[1] / 60))
                return Leg(minutes, f"步行约 {minutes} 分钟 ({km:.1f} km)", round(km, 2), False)

        if mode != "walking":
            drive = self._lookup(DRIVE, o, d)
            if drive:
                km = drive[0] / 1000
                minutes = max(MIN_DRIVE_MIN, math.ceil(drive[1] / 60) + TAXI_WAIT_MIN)
                odd = is_roundabout(straight_km, km)
                desc = f"打车/驾车约 {minutes} 分钟 ({km:.1f} km)" + (ROUNDABOUT_NOTE if odd else "")
                return Leg(minutes, desc, round(km, 2), False, roundabout=odd)

        self._estimated_legs += 1
        est = self.estimate(a, b, mode)
        return Leg(est.minutes, est.desc + "（估算）", est.road_km, True)

    def notes(self) -> list:
        if not self._estimated_legs:
            return []
        reason = "；".join(self._errors) or "高德未返回该路段的路线"
        return [f"有 {self._estimated_legs} 段路程未取到高德路网数据（{reason}），已按直线距离估算并标注「估算」，"
                f"实际耗时可能更长。"]
