"""数据模型定义：纯 Python 排程引擎的数据结构。

无需第三方复杂依赖，纯标准库 dataclasses。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class LocationPoint:
    """经纬度坐标与基础信息"""
    name: str
    lng: float
    lat: float
    address: str = ""
    poi_id: str = ""
    # 大型景区（玄武湖、西湖、圆明园等）的出入口；非空时通勤一律按最近的出入口算，不用几何中心点。
    # 几何中心可能在湖心或山里，离实际进出的门区几公里（实测西湖中心点与导航入口相距约 3km）
    gates: List["LocationPoint"] = field(default_factory=list)

    @property
    def location_str(self) -> str:
        return f"{self.lng:.6f},{self.lat:.6f}"


@dataclass
class Spot(LocationPoint):
    """旅游景点"""
    duration_minutes: int = 120  # 预计建议游玩时长
    open_time: str = ""  # 空串 = 高德未提供，排程时标注「需现场确认」，不编造默认值
    closed_days: List[int] = field(default_factory=list)  # 0=周一, 6=周日，常见如博物馆周一闭馆
    tags: List[str] = field(default_factory=list)
    rating: Optional[float] = None  # None = 无评分数据
    cost: Optional[float] = None  # 门票参考；None = 无数据（≠ 免费）
    is_indoor: bool = False  # 是否为室内场馆（博物馆、科技馆等）
    best_period: str = ""  # 最佳游览时段："" 不限 | "morning" 宜早去 | "evening" 宜傍晚/夜间
    must_visit: bool = False  # 用户点名要去的：放不下时最后才舍弃（模型补充的景点不标）
    # 同品牌的其他分店（用户只说了「先锋书店」、没指定哪家时）：分天时按当天路线换成最顺路且走得通的一家。
    # 各分店本身的 branches 为空，branch_of 记原来那家的名称，用于在行程里说明换了店
    branches: List["Spot"] = field(default_factory=list)
    branch_of: str = ""
    tips: str = ""  # 模型基于常识给的游览建议（预约、门票、最佳时段等），只作附注展示，未经数据核实


@dataclass
class Restaurant(LocationPoint):
    """餐饮/小吃点"""
    meal_type: str = "meal"  # meal(正餐) | snack(特色小吃) | night_market(夜市)
    rating: Optional[float] = None  # None = 无评分数据
    cost: Optional[float] = None  # 人均；None = 无数据
    open_time: str = ""
    cuisine: str = ""  # 高德返回的品类，如「川菜」「中餐厅」
    category: str = ""  # 高德完整分类，如「餐饮服务;快餐厅;xx」，用于识别快餐
    recommend_reason: str = ""
    dish_key: str = ""  # 命中的用户必吃美食（来自 must_eat 参数）；非空时全程只安排一次


@dataclass
class ScheduleItem:
    """行程日程单项"""
    time_slot: str  # "09:00 - 11:30"
    category: str  # "spot" | "meal" | "snack" | "transit" | "hotel"
    title: str
    description: str
    location: Optional[LocationPoint] = None
    transit_info: Optional[str] = None  # 如 "驾车约 15 分钟 (4.2 km)"
    cost: Optional[float] = None  # None = 无数据或不适用
    open_time: str = ""
    rating: Optional[float] = None
    warnings: List[str] = field(default_factory=list)  # 如 "营业时间接近打烊，请注意"
    tips: str = ""  # 景点的经验建议（来自模型常识，未经核实），见 Spot.tips


@dataclass
class DayItinerary:
    """单日行程单"""
    day_number: int  # 第 1 天、第 2 天
    theme: str  # 本日主题，由当天景点名拼成
    date: str = ""  # "YYYY-MM-DD"，校验闭馆日用
    items: List[ScheduleItem] = field(default_factory=list)
    total_spots: int = 0
    total_cost: float = 0.0
    estimated_distance_km: float = 0.0
    has_unknown_cost: bool = False  # 当日有项目缺价格数据，total_cost 未计入


@dataclass
class FullTripPlan:
    """整套行程编排成果"""
    city: str
    total_days: int
    hotel: Optional[LocationPoint]
    days: List[DayItinerary] = field(default_factory=list)
    unassigned_spots: List[str] = field(default_factory=list)  # 「名称（未安排原因）」
    unmet_must_eat: List[str] = field(default_factory=list)  # 「美食（没排进行程的原因）」
    gaps: List[Dict[str, Any]] = field(default_factory=list)  # 给模型的结构化缺口清单（缺候选的饭点等），不展示给用户
    summary: str = ""
    departure_min: Optional[int] = None  # 返程车次/航班时刻（当日分钟数），校验到站缓冲用
