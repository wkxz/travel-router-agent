"""点到点通勤耗时：排程器与校验器只通过这里取通勤时间。

引擎保持纯 Python、不访问网络：默认实现 TravelTimes 按直线距离估算（geo.estimate_transit）；
真实路网数据（高德步行/驾车）由数据层子类注入，见 agents/_data/routing.py。

排程时的「候选筛选」（挑哪家餐厅、哪个空档景点）用 estimate，避免为每个候选都请求一次路网；
真正写进时间轴的路段、返程赶车判断与校验器复核用 leg，二者取同一份数据，口径一致。
"""

from dataclasses import dataclass

from .models import LocationPoint
from .geo import haversine_distance, estimate_transit


@dataclass(frozen=True)
class Leg:
    minutes: int
    desc: str  # 如「步行约 12 分钟 (0.9 km)」
    road_km: float
    estimated: bool  # True = 按直线距离估算，非路网实测


class TravelTimes:
    """默认实现：全部按直线距离估算。"""

    def estimate(self, a: LocationPoint, b: LocationPoint, mode: str = "auto") -> Leg:
        dist = haversine_distance(a.lng, a.lat, b.lng, b.lat)
        minutes, desc = estimate_transit(dist, mode=mode)
        return Leg(minutes, desc, round(dist * 1.3, 2), True)

    def leg(self, a: LocationPoint, b: LocationPoint, mode: str = "auto") -> Leg:
        return self.estimate(a, b, mode)

    def notes(self) -> list:
        """需要告知用户的数据来源说明（如部分路段未取到路网数据）。"""
        return []
