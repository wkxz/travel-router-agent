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

    @property
    def location_str(self) -> str:
        return f"{self.lng:.6f},{self.lat:.6f}"


@dataclass
class Spot(LocationPoint):
    """旅游景点"""
    duration_minutes: int = 120  # 预计建议游玩时长
    open_time: str = "08:30-17:30"
    closed_days: List[int] = field(default_factory=list)  # 0=周一, 6=周日，常见如博物馆周一闭馆
    tags: List[str] = field(default_factory=list)
    rating: float = 4.5
    cost: float = 0.0  # 门票预算


@dataclass
class Restaurant(LocationPoint):
    """餐饮/小吃点"""
    meal_type: str = "meal"  # meal(正餐) | snack(特色小吃) | night_market(夜市)
    rating: float = 4.5
    cost: float = 50.0  # 人均预算
    open_time: str = "10:00-22:00"
    cuisine: str = ""  # 菜系/品类，如川菜、北京小吃、火锅
    recommend_reason: str = ""


@dataclass
class ScheduleItem:
    """行程日程单项"""
    time_slot: str  # "09:00 - 11:30"
    category: str  # "spot" | "meal" | "snack" | "transit" | "hotel"
    title: str
    description: str
    location: Optional[LocationPoint] = None
    transit_info: Optional[str] = None  # 如 "驾车约 15 分钟 (4.2 km)"
    cost: float = 0.0
    open_time: str = ""
    rating: Optional[float] = None
    warnings: List[str] = field(default_factory=list)  # 如 "营业时间接近打烊，请注意"


@dataclass
class DayItinerary:
    """单日行程单"""
    day_number: int  # 第 1 天、第 2 天
    theme: str  # 本日主题，如 "历史人文与老北京小吃漫游"
    items: List[ScheduleItem] = field(default_factory=list)
    total_spots: int = 0
    total_cost: float = 0.0
    estimated_distance_km: float = 0.0


@dataclass
class FullTripPlan:
    """整套行程编排成果"""
    city: str
    total_days: int
    hotel: Optional[LocationPoint]
    days: List[DayItinerary] = field(default_factory=list)
    unassigned_spots: List[str] = field(default_factory=list)
    summary: str = ""
