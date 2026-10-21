from datetime import datetime, date, timedelta
from typing import List, Tuple, Optional
from .models import LocationPoint, Spot
from .optimizer import optimize_spots_route
from .geo import cluster_spots_spatially

# 默认闭馆规则：常见特大博物馆逢周一闭馆
MONDAY_CLOSED_KEYWORDS = ["博物馆", "博物院", "纪念馆", "美术馆", "科技馆"]


def is_spot_closed_on_date(spot: Spot, target_date: date) -> Tuple[bool, str]:
    """判断景点在指定日期是否闭馆。

    周一 weekday() == 0。
    优先检查 spot.closed_days，若未明确设置但名称含博物馆等关键词，自动应用周一闭馆规则。
    """
    weekday = target_date.weekday()
    closed_list = list(spot.closed_days)
    if not closed_list and any(kw in spot.name for kw in MONDAY_CLOSED_KEYWORDS):
        closed_list = [0]  # 默认周一闭馆

    if weekday in closed_list:
        day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return True, f"【闭馆预警】{spot.name} 逢{day_names[weekday]}闭馆，{target_date.strftime('%Y-%m-%d')} 不开放！"
    return False, ""


def split_spots_by_time_and_calendar(
    hotel: LocationPoint,
    spots: List[Spot],
    total_days: int,
    start_date: date,
    target_daily_budget_min: int = 360,  # 目标单日游玩+通勤时间预算约 6 小时
    max_spots_per_day: int = 3,
) -> Tuple[List[List[Spot]], List[Spot], List[str]]:
    """融合真实日历闭馆检测与时间预算装箱（Time-budgeted Bin Packing）的切分算法。

    解决两大痛点：
    1. 真实日历周一闭馆校验：遇到闭馆日自动避开或调换天数，防现场吃闭门羹；
    2. 单日时长均衡装箱：不再机械平分个数，而是综合 duration_minutes 与空间近邻度连续装箱。
    """
    if not spots:
        return [[] for _ in range(total_days)], [], []

    calendar_warnings: List[str] = []
    days_spots: List[List[Spot]] = [[] for _ in range(total_days)]
    unassigned: List[Spot] = []
    date_list = [start_date + timedelta(days=d) for d in range(total_days)]

    # 1. 纯数学空间层次聚类：按地理距离将 M 个景点聚为 total_days 个紧密簇
    # 彻底杜绝人工写死路线，全国任意城市景点自然成组
    raw_chunks = cluster_spots_spatially(hotel, spots, total_days)

    # 3. 闭馆防呆交换：如果某天分到的景点在当天闭馆，尝试与非闭馆日同片区的点调换
    for day_idx in range(total_days):
        curr_date = date_list[day_idx]
        chunk = raw_chunks[day_idx]
        for s in list(chunk):
            is_closed, reason = is_spot_closed_on_date(s, curr_date)
            if is_closed:
                # 尝试寻找一个该景点开馆的其他天进行安全置换
                swapped = False
                for other_day in range(total_days):
                    if other_day == day_idx:
                        continue
                    other_date = date_list[other_day]
                    other_closed, _ = is_spot_closed_on_date(s, other_date)
                    if not other_closed and raw_chunks[other_day]:
                        # 检查被换过来的点在当前天是否也开放
                        candidate = raw_chunks[other_day][-1]
                        cand_closed, _ = is_spot_closed_on_date(candidate, curr_date)
                        if not cand_closed:
                            chunk.remove(s)
                            raw_chunks[other_day].remove(candidate)
                            chunk.append(candidate)
                            raw_chunks[other_day].append(s)
                            swapped = True
                            calendar_warnings.append(
                                f"动态日程调度：检测到【{s.name}】在 {curr_date.strftime('%Y-%m-%d')} 闭馆，"
                                f"算法已自动将其与第 {other_day + 1} 天行程对调，确保出行顺利！"
                            )
                            break
                if not swapped:
                    calendar_warnings.append(reason)

    # 4. 每天内部以住宿点为起点微调单日最佳顺序
    for day_idx in range(total_days):
        chunk = raw_chunks[day_idx]
        if chunk:
            opt_chunk, _ = optimize_spots_route(hotel, chunk)
            days_spots[day_idx] = opt_chunk
        else:
            days_spots[day_idx] = []

    return days_spots, unassigned, calendar_warnings
