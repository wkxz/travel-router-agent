"""一天时间轴的公共常数，以及分天阶段用的简化时间线模拟。

排程器（scheduler.py）按真实路段逐项排时间轴；分天（calendar_pack.py）在此之前就要判断
「这几个景点放在同一天、按这个顺序走，时间上走不走得通」。两边共用这里的常数，口径一致。
所有时刻都是「当日 00:00 起的分钟数」。
"""

from datetime import date
from typing import Callable, List, Optional, Tuple

from .geo import find_open_slot, first_open_minute, sunset_minutes
from .models import LocationPoint, Spot
from .hours import hours_on_date

DAY_START = 9 * 60            # 每天 09:00 出发
LUNCH_EARLIEST = 11 * 60 + 30
LUNCH_DURATION = 75
LUNCH_TRIGGER = 13 * 60 + 30  # 若下一个景点会拖过这个时间，先吃午饭
# 下一个景点会拖过 LUNCH_TRIGGER 时，此刻不早于它就先吃午饭（等到 LUNCH_EARLIEST 开饭）。旧版要求 11:00 以后，
# 南京实测 10:54 游完一处、下一处要 2.5 小时，午饭被拖到 13:49；多等半小时开饭好过饿到下午两点
LUNCH_FIRST_FROM = LUNCH_EARLIEST - 60
DINNER_DEFAULT = 18 * 60
DINNER_DURATION = 80
SPOT_LATEST_END = 18 * 60 + 30  # 白天景点最晚结束时间，之后留给晚餐
EVENING_LATEST_END = 22 * 60 + 30  # 夜游景点最晚结束时间
EARLIEST_DEPART = 7 * 60      # 「宜早去」景点最早 07:00 出发
MORNING_ARRIVE_DEFAULT = 8 * 60  # 「宜早去」但营业时间未知时，按 08:00 到达
OPEN_MAX_WAIT = 90            # 到早了最多等开门 90 分钟（与排程器 find_open_slot 默认值一致）
NIGHT_MAX_WAIT = 30
DINNER_TO_NIGHT_SPOT = 15     # 排程器把晚餐选在第一个夜游景点旁边，吃完步行过去
# 分天挑顺序时，户外景点在日落后这么久之后才游完算「天黑」。比 eval/plan_checks 的 30 分钟严：这里的时间模拟较粗
# （午饭不算去餐厅的路），留出余量，免得模拟以为换个顺序能避开、实际仍天黑却白绕了路。
# 2026-10-30 随机 300 例对比（按 30 / 10 分钟）：天黑 33→26 / 29、回头路 30→36 / 32；
# 时间轴按接近真实车程排时：天黑 49→40 / 40、回头路 19→22 / 20
DARK_GRACE_MIN = 10

Leg = Callable[[LocationPoint, LocationPoint], float]


def day_is_feasible(hotel: LocationPoint, daytime: List[Spot], night: List[Spot], leg: Leg,
                    day: Optional[date] = None) -> bool:
    """按给定顺序粗排一天，检查每个景点都能在营业时间内游完、白天景点不晚于 18:30、夜游不晚于 22:30。

    leg(a, b) 返回通勤分钟数（调用方已含安全系数）。午餐按排程器的规则插入（只计时长，不计去餐厅的路）。
    营业时间缺失或无法解析的视为可行（排程器会标注「需现场确认」）。day 给定时按当天那段营业时间判断。
    """
    return simulate_day(hotel, daytime, night, leg, day)[0]


def simulate_day(hotel: LocationPoint, daytime: List[Spot], night: List[Spot], leg: Leg,
                 day: Optional[date] = None) -> Tuple[bool, int]:
    """同 day_is_feasible，另返回游到「日落 + DARK_GRACE_MIN」之后的白天户外景点个数（day 为空时不算）。"""
    t, cur, dark = DAY_START, hotel, 0
    if daytime and daytime[0].best_period == "morning":  # 与排程器 start_early_for 一致：宜早去的按开门时间到
        opening = first_open_minute(hours_on_date(daytime[0].open_time, day))
        arrive = opening if opening is not None else MORNING_ARRIVE_DEFAULT
        t = min(DAY_START, max(EARLIEST_DEPART, arrive - leg(hotel, daytime[0])))
    lunch_done = False
    for s in daytime:
        travel = leg(cur, s)
        if not lunch_done and t >= LUNCH_FIRST_FROM and t + travel + s.duration_minutes > LUNCH_TRIGGER:
            t = max(t, LUNCH_EARLIEST) + LUNCH_DURATION
            lunch_done = True
        status, start, _ = find_open_slot(t + travel, s.duration_minutes, hours_on_date(s.open_time, day),
                                          max_wait=OPEN_MAX_WAIT)
        if status is False or start + s.duration_minutes > SPOT_LATEST_END:
            return False, dark
        t, cur = start + s.duration_minutes, s
        if day is not None and not s.is_indoor:
            dark += t > sunset_minutes(s.lat, s.lng, day) + DARK_GRACE_MIN
    t = DINNER_DEFAULT + DINNER_DURATION
    for i, s in enumerate(night):
        travel = DINNER_TO_NIGHT_SPOT if i == 0 else leg(cur, s)
        status, start, _ = find_open_slot(t + travel, s.duration_minutes, hours_on_date(s.open_time, day),
                                          max_wait=NIGHT_MAX_WAIT)
        if status is False or start + s.duration_minutes > EVENING_LATEST_END:
            return False, dark
        t, cur = start + s.duration_minutes, s
    return True, dark
