"""排程引擎对外暴露的核心接口与类。"""

from .models import (
    LocationPoint,
    Spot,
    Restaurant,
    ScheduleItem,
    DayItinerary,
    FullTripPlan,
)
from .geo import haversine_distance, estimate_transit, is_time_in_range, cluster_spots_spatially
from .optimizer import optimize_spots_route
from .travel import TravelTimes, Leg
from .scheduler import TripScheduler, today_cn
from .renderer import render_markdown, render_dict
from .validator import validate_plan, InfeasiblePlanError

__all__ = [
    "LocationPoint",
    "Spot",
    "Restaurant",
    "ScheduleItem",
    "DayItinerary",
    "FullTripPlan",
    "haversine_distance",
    "estimate_transit",
    "is_time_in_range",
    "cluster_spots_spatially",
    "optimize_spots_route",
    "TravelTimes",
    "Leg",
    "TripScheduler",
    "today_cn",
    "render_markdown",
    "render_dict",
    "validate_plan",
    "InfeasiblePlanError",
]
