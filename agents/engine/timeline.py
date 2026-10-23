"""一天时间轴的公共常数，以及分天阶段用的简化时间线模拟。

排程器（scheduler.py）按真实路段逐项排时间轴；分天（calendar_pack.py）在此之前就要判断
「这几个景点放在同一天、按这个顺序走，时间上走不走得通」。两边共用这里的常数，口径一致。
所有时刻都是「当日 00:00 起的分钟数」。
"""

from typing import Callable, List, Optional

from .geo import find_open_slot, first_open_minute
from .models import LocationPoint, Spot

DAY_START = 9 * 60            # 每天 09:00 出发
LUNCH_EARLIEST = 11 * 60 + 30
LUNCH_DURATION = 75
LUNCH_TRIGGER = 13 * 60 + 30  # 若下一个景点会拖过这个时间，先吃午饭
DINNER_DEFAULT = 18 * 60
DINNER_DURATION = 80
SPOT_LATEST_END = 18 * 60 + 30  # 白天景点最晚结束时间，之后留给晚餐
EVENING_LATEST_END = 22 * 60 + 30  # 夜游景点最晚结束时间
EARLIEST_DEPART = 7 * 60      # 「宜早去」景点最早 07:00 出发
MORNING_ARRIVE_DEFAULT = 8 * 60  # 「宜早去」但营业时间未知时，按 08:00 到达
OPEN_MAX_WAIT = 90            # 到早了最多等开门 90 分钟（与排程器 find_open_slot 默认值一致）
NIGHT_MAX_WAIT = 30
DINNER_TO_NIGHT_SPOT = 15     # 排程器把晚餐选在第一个夜游景点旁边，吃完步行过去

Leg = Callable[[LocationPoint, LocationPoint], float]


def day_is_feasible(hotel: LocationPoint, daytime: List[Spot], night: List[Spot], leg: Leg) -> bool:
    """按给定顺序粗排一天，检查每个景点都能在营业时间内游完、白天景点不晚于 18:30、夜游不晚于 22:30。

    leg(a, b) 返回通勤分钟数（调用方已含安全系数）。午餐按排程器的规则插入（只计时长，不计去餐厅的路）。
    营业时间缺失或无法解析的视为可行（排程器会标注「需现场确认」）。
    """
    t, cur = DAY_START, hotel
    if daytime and daytime[0].best_period == "morning":  # 与排程器 start_early_for 一致：宜早去的按开门时间到
        opening = first_open_minute(daytime[0].open_time)
        arrive = opening if opening is not None else MORNING_ARRIVE_DEFAULT
        t = min(DAY_START, max(EARLIEST_DEPART, arrive - leg(hotel, daytime[0])))
    lunch_done = False
    for s in daytime:
        travel = leg(cur, s)
        if not lunch_done and t >= 11 * 60 and t + travel + s.duration_minutes > LUNCH_TRIGGER:
            t = max(t, LUNCH_EARLIEST) + LUNCH_DURATION
            lunch_done = True
        status, start, _ = find_open_slot(t + travel, s.duration_minutes, s.open_time, max_wait=OPEN_MAX_WAIT)
        if status is False or start + s.duration_minutes > SPOT_LATEST_END:
            return False
        t, cur = start + s.duration_minutes, s
    t = DINNER_DEFAULT + DINNER_DURATION
    for i, s in enumerate(night):
        travel = DINNER_TO_NIGHT_SPOT if i == 0 else leg(cur, s)
        status, start, _ = find_open_slot(t + travel, s.duration_minutes, s.open_time, max_wait=NIGHT_MAX_WAIT)
        if status is False or start + s.duration_minutes > EVENING_LATEST_END:
            return False
        t, cur = start + s.duration_minutes, s
    return True
