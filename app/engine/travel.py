"""点到点通勤耗时：排程器与校验器只通过这里取通勤时间。

引擎保持纯 Python、不访问网络：默认实现 TravelTimes 按直线距离估算（geo.estimate_transit）；
真实路网数据（高德步行/驾车）由数据层子类注入，见 app/_data/routing.py。
大型景区有出入口数据时，路段按两地最近的一对出入口计算（geo.gate_pair），并在描述里写明从哪个门进出。

排程时的「候选筛选」（挑哪家餐厅、哪个空档景点）用 estimate，避免为每个候选都请求一次路网；
真正写进时间轴的路段、返程赶车判断与校验器复核用 leg，二者取同一份数据，口径一致。
"""

from dataclasses import dataclass

from .models import LocationPoint
from .geo import haversine_distance, estimate_transit, gate_pair, WALKING_MAX_ROAD_KM


# 路网距离是直线的 3 倍以上、且多出 2km 以上时视为「绕行明显」：多半隔着海峡、江河、湖泊或封闭区域。
# 城区正常路网约为直线的 1.3~1.6 倍。实测厦门中山路 → 鼓浪屿店餐厅直线 1.5km、驾车 9.3km / 85 分钟，
# 挑候选时按直线估算会当成「就近」，时间轴里却要来回过海。
DETOUR_FACTOR = 3.0
DETOUR_MIN_EXTRA_KM = 2.0


def is_roundabout(straight_km: float, road_km: float) -> bool:
    return road_km >= straight_km * DETOUR_FACTOR and road_km - straight_km >= DETOUR_MIN_EXTRA_KM


def gate_note(a: LocationPoint, x: LocationPoint, b: LocationPoint, y: LocationPoint) -> str:
    """x、y 是 gate_pair(a, b) 选出的进出位置；是有名字的出入口时写进路段描述，如「，从「玄武门」进」。"""
    parts = []
    if x is not a and x.name:
        parts.append(f"从「{x.name}」出")
    if y is not b and y.name:
        parts.append(f"从「{y.name}」进")
    return "".join("，" + s for s in parts)


@dataclass(frozen=True)
class Leg:
    minutes: int
    desc: str  # 如「步行约 12 分钟 (0.9 km)」
    road_km: float
    estimated: bool  # True = 按直线距离估算，非路网实测
    roundabout: bool = False  # 路网实测比直线绕行明显（见 is_roundabout）


class TravelTimes:
    """默认实现：全部按直线距离估算。"""

    # 道路距离不超过它的按步行，否则打车；同行有老人等行动不便时由排程器调低（见 TripScheduler 的 mobility）
    walk_max_km: float = WALKING_MAX_ROAD_KM

    def estimate(self, a: LocationPoint, b: LocationPoint, mode: str = "auto") -> Leg:
        x, y = gate_pair(a, b)
        leg = self.estimate_between(x, y, mode)
        return Leg(leg.minutes, leg.desc + gate_note(a, x, b, y), leg.road_km, True)

    def estimate_between(self, x: LocationPoint, y: LocationPoint, mode: str = "auto") -> Leg:
        """两个确定坐标之间的直线估算（不再挑出入口）。"""
        dist = haversine_distance(x.lng, x.lat, y.lng, y.lat)
        minutes, desc = estimate_transit(dist, mode=mode, walk_max_km=self.walk_max_km)
        return Leg(minutes, desc, round(dist * 1.3, 2), True)

    def leg(self, a: LocationPoint, b: LocationPoint, mode: str = "auto") -> Leg:
        return self.estimate(a, b, mode)

    def notes(self) -> list:
        """需要告知用户的数据来源说明（如部分路段未取到路网数据）。"""
        return []
