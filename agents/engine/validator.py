"""行程可行性校验器：与排程器独立实现，只看最终产出的 FullTripPlan。

排程器在生成过程中做局部判断，难免有遗漏；这里对成品逐项复核，
任何「走不通」的情况都记为 error，由调用方拒绝输出。
"""

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

from .models import FullTripPlan, Restaurant, Spot
from .geo import parse_time_to_minutes, minutes_to_hhmm, check_open_window
from .calendar_pack import is_spot_closed_on_date
from .travel import TravelTimes
from .hours import hours_on_date


@dataclass
class Issue:
    level: str  # "error" = 走不通，必须拦下 | "warning" = 可行但需提醒
    day: int
    title: str
    message: str

    def __str__(self) -> str:
        return f"第 {self.day} 天「{self.title}」：{self.message}"


class InfeasiblePlanError(ValueError):
    """排程结果未通过可行性校验。"""

    def __init__(self, issues: List[Issue]):
        self.issues = issues
        super().__init__("排程结果未通过可行性校验：" + "；".join(str(i) for i in issues))


def _parse_slot(slot: str) -> Tuple[Optional[int], Optional[int]]:
    """'09:00 - 11:30' → (540, 690)；单个时刻 '19:40' → (1180, 1180)。"""
    parts = [p.strip() for p in slot.split(" - ")]
    start = parse_time_to_minutes(parts[0])
    end = parse_time_to_minutes(parts[1]) if len(parts) == 2 else start
    return start, end


def validate_plan(plan: FullTripPlan, station_buffer: int = 60,
                  travel: Optional[TravelTimes] = None) -> List[Issue]:
    """travel：通勤数据源，应与排程时相同（接了高德路网时，按路网耗时复核每一段）。"""
    travel = travel or TravelTimes()
    issues: List[Issue] = []
    used_names: dict = {}
    used_dishes: dict = {}

    for day in plan.days:
        day_date = date.fromisoformat(day.date) if day.date else None
        prev_end: Optional[int] = None
        prev_loc = None
        prev_title = ""

        def err(title: str, msg: str) -> None:
            issues.append(Issue("error", day.day_number, title, msg))

        for item in day.items:
            start, end = _parse_slot(item.time_slot)
            if start is None or end is None:
                err(item.title, f"时间格式无法解析：{item.time_slot!r}")
                continue
            if end < start:
                err(item.title, f"结束时刻早于开始时刻（{item.time_slot}），可能跨过了午夜")
            if prev_end is not None and start < prev_end:
                err(item.title, f"与上一项「{prev_title}」时间重叠（上一项 {minutes_to_hhmm(prev_end)} 结束）")

            loc = item.location
            if loc and prev_loc and prev_end is not None:
                t_min = travel.leg(prev_loc, loc).minutes
                arrive = end if item.category == "transit" else start
                if arrive - prev_end < t_min:
                    err(item.title, f"从【{prev_loc.name}】过来约需 {t_min} 分钟，只留了 {arrive - prev_end} 分钟")

            if item.category in ("spot", "meal", "snack") and loc and item.open_time:
                if check_open_window(start, end, hours_on_date(item.open_time, day_date))[0] is False:
                    err(item.title, f"{item.time_slot} 不在营业时间内（{item.open_time}）")

            if item.category == "spot" and isinstance(loc, Spot) and day_date:
                closed, msg = is_spot_closed_on_date(loc, day_date)
                if closed:
                    err(item.title, msg)

            if isinstance(loc, Restaurant):
                if loc.name in used_names:
                    err(item.title, f"餐厅与第 {used_names[loc.name]} 天重复")
                used_names[loc.name] = day.day_number
                if loc.dish_key:
                    if loc.dish_key in used_dishes:
                        err(item.title, f"必吃「{loc.dish_key}」与第 {used_dishes[loc.dish_key]} 天重复")
                    used_dishes[loc.dish_key] = day.day_number

            prev_end, prev_title = end, item.title
            if loc:
                prev_loc = loc

    if plan.departure_min is not None and plan.days:
        last = plan.days[-1]
        transits = [i for i in last.items if i.category == "transit"]
        if transits:
            _, arrive = _parse_slot(transits[-1].time_slot)
            if arrive is not None and plan.departure_min - arrive < station_buffer:
                issues.append(Issue(
                    "error", last.day_number, transits[-1].title,
                    f"预计 {minutes_to_hhmm(arrive)} 到站，距离发车 {minutes_to_hhmm(plan.departure_min)} "
                    f"只剩 {plan.departure_min - arrive} 分钟，不足 {station_buffer} 分钟",
                ))

    return issues
