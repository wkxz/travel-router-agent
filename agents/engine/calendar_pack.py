import itertools
import re
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
from .models import LocationPoint, Spot
from .optimizer import optimize_spots_route
from .geo import cluster_spots_spatially, estimate_transit, haversine_distance

# 默认闭馆规则：常见特大博物馆逢周一闭馆
MONDAY_CLOSED_KEYWORDS = ["博物馆", "博物院", "纪念馆", "美术馆", "科技馆"]


_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_D = "[一二三四五六日天]"
_WD = rf"(?:周|星期|礼拜)({_D})"
_RANGE = _WD + r"\s*(?:至|到|-|–|~|—)\s*" + _WD
_CLOSED_WORDS = r"\s*(?:全天)?(?:闭馆|休馆|闭园|休息|休业|不开放|停止开放|歇业)"
# 「周一至周三闭馆」
_CLOSED_RANGE_RE = re.compile(_RANGE + _CLOSED_WORDS)
# 「周一闭馆」「每周一、二休息」
_CLOSED_LIST_RE = re.compile(rf"(?:周|星期|礼拜)({_D}(?:[、,，和及]?(?:周|星期|礼拜)?{_D})*)" + _CLOSED_WORDS)
# 「周二至周日 09:00-17:00」
_OPEN_RANGE_RE = re.compile(_RANGE)
# 单个开放日必须紧跟时刻或「开放/营业」，避免把别处提到的星期误当开放日
_OPEN_SINGLE_RE = re.compile(_WD + r"\s*(?=\d|开放|营业)")


def _day_span(a: str, b: str) -> List[int]:
    start, end = _WEEKDAY_CHARS[a], _WEEKDAY_CHARS[b]
    return list(range(start, end + 1)) if start <= end else list(range(start, 7)) + list(range(0, end + 1))


def parse_closed_days(open_time: str) -> Optional[List[int]]:
    """从高德营业时间文本里解析每周闭馆日（0=周一）。

    - 「周一闭馆」「每周一、二休息」「周一至周三闭馆」→ 明确的闭馆日
    - 「周二至周日 09:00-17:00」→ 开放日之外的都算闭馆 → [0]
    - 文本里没有可识别的星期信息 → None（调用方再走名称关键词兜底）
    """
    text = open_time or ""
    closed: set = set()
    for a, b in _CLOSED_RANGE_RE.findall(text):
        closed.update(_day_span(a, b))
    text = _CLOSED_RANGE_RE.sub(" ", text)
    for chars in _CLOSED_LIST_RE.findall(text):
        closed.update(_WEEKDAY_CHARS[c] for c in chars if c in _WEEKDAY_CHARS)
    text = _CLOSED_LIST_RE.sub(" ", text)

    open_days: set = set()
    for a, b in _OPEN_RANGE_RE.findall(text):
        open_days.update(_day_span(a, b))
    for c in _OPEN_SINGLE_RE.findall(_OPEN_RANGE_RE.sub(" ", text)):
        open_days.add(_WEEKDAY_CHARS[c])

    if open_days:
        closed |= set(range(7)) - open_days
    elif not closed:
        return None
    return sorted(closed)


def is_spot_closed_on_date(spot: Spot, target_date: date) -> Tuple[bool, str]:
    """判断景点在指定日期是否闭馆。

    周一 weekday() == 0。
    优先级：spot.closed_days > 营业时间文本里的星期信息 > 名称含博物馆等关键词时默认周一闭馆。
    """
    weekday = target_date.weekday()
    closed_list = list(spot.closed_days)
    if not closed_list:
        parsed = parse_closed_days(spot.open_time)
        if parsed is not None:
            closed_list = parsed
        elif any(kw in spot.name for kw in MONDAY_CLOSED_KEYWORDS):
            closed_list = [0]  # 默认周一闭馆

    if weekday in closed_list:
        day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return True, f"【闭馆预警】{spot.name} 逢{day_names[weekday]}闭馆，{target_date.strftime('%Y-%m-%d')} 不开放！"
    return False, ""


def estimate_route_minutes(start: LocationPoint, spots: List[Spot]) -> int:
    """估算从 start 依次游览 spots 的总耗时（游玩时长 + 点间通勤，不含用餐）。"""
    total = 0
    cur = start
    for s in spots:
        t_min, _ = estimate_transit(haversine_distance(cur.lng, cur.lat, s.lng, s.lat))
        total += t_min + s.duration_minutes
        cur = s
    return total


def _centroid(spots: List[Spot], fallback: LocationPoint) -> Tuple[float, float]:
    if not spots:
        return fallback.lng, fallback.lat
    return sum(s.lng for s in spots) / len(spots), sum(s.lat for s in spots) / len(spots)


EXACT_MAX_SPOTS = 10      # 景点数不超过它时穷举所有分天方案（状态压缩 DP，10 个点约百万次运算，<1s）
UNASSIGNED_PENALTY = 10_000  # 少排一个景点的代价，远大于任何通勤差：先保证排进去的最多，再比路程
BALANCE_WEIGHT = 0.25     # 每天负荷偏离平均值 1 分钟的代价（相当于多 0.25 分钟通勤），避免某天塞满、某天空着
_PERIOD_RANK = {"morning": 0, "evening": 2}


def _best_day_route(hotel: LocationPoint, spots: List[Spot]) -> Tuple[List[Spot], int]:
    """当天景点的最短游览顺序（住宿出发、回到住宿）及往返通勤分钟数。

    遵守时段约束：宜早去的排最前、宜傍晚的排最后（与排程器 order_by_period 一致）。
    每天至多 4 个景点，全排列最多 24 种，直接穷举。
    """
    def leg(a, b) -> int:
        return estimate_transit(haversine_distance(a.lng, a.lat, b.lng, b.lat))[0]

    best, best_cost = list(spots), None
    for perm in itertools.permutations(spots):
        ranks = [_PERIOD_RANK.get(s.best_period, 1) for s in perm]
        if ranks != sorted(ranks):
            continue
        pts = [hotel, *perm, hotel]
        cost = sum(leg(a, b) for a, b in zip(pts, pts[1:]))
        if best_cost is None or cost < best_cost:
            best, best_cost = list(perm), cost
    return best, best_cost or 0


def _exact_split(
    hotel: LocationPoint,
    spots: List[Spot],
    date_list: List[date],
    budgets: List[int],
    max_spots_per_day: int,
) -> List[List[Spot]]:
    """穷举所有「景点 → 天」的分配，选出：排进去的景点最多 → 总通勤最短 → 每天负荷最均衡。

    每天的景点子集必须满足：当天不闭馆、不超过每日景点数、游玩+通勤不超过当天预算。
    """
    n, days = len(spots), len(date_list)
    mean_load = sum(s.duration_minutes for s in spots) / days

    # 每天可行的子集：mask -> (代价, 游览顺序)
    options: List[Dict[int, Tuple[float, List[Spot]]]] = []
    route_cache: Dict[int, Tuple[List[Spot], int]] = {}
    for d in range(days):
        opts: Dict[int, Tuple[float, List[Spot]]] = {0: (BALANCE_WEIGHT * mean_load, [])}
        open_idx = [i for i in range(n) if not is_spot_closed_on_date(spots[i], date_list[d])[0]]
        for k in range(1, min(max_spots_per_day, len(open_idx)) + 1):
            for combo in itertools.combinations(open_idx, k):
                mask = sum(1 << i for i in combo)
                if mask not in route_cache:
                    route_cache[mask] = _best_day_route(hotel, [spots[i] for i in combo])
                order, travel = route_cache[mask]
                if estimate_route_minutes(hotel, order) > budgets[d]:
                    continue
                load = travel + sum(s.duration_minutes for s in order)
                opts[mask] = (travel + BALANCE_WEIGHT * abs(load - mean_load), order)
        options.append(opts)

    # DP：第 d 天之前已用掉的景点集合 -> (最小代价, 每天的子集)
    states: Dict[int, Tuple[float, List[int]]] = {0: (0.0, [])}
    for d in range(days):
        nxt: Dict[int, Tuple[float, List[int]]] = {}
        for used, (cost, picks) in states.items():
            for mask, (c, _) in options[d].items():
                if mask & used:
                    continue
                key, total = used | mask, cost + c
                if key not in nxt or total < nxt[key][0]:
                    nxt[key] = (total, picks + [mask])
        states = nxt

    def final_cost(item):
        used, (cost, _) = item
        return cost + UNASSIGNED_PENALTY * (n - bin(used).count("1"))

    _, (_, picks) = min(states.items(), key=final_cost)
    return [options[d][mask][1] for d, mask in enumerate(picks)]


def split_spots_by_time_and_calendar(
    hotel: LocationPoint,
    spots: List[Spot],
    total_days: int,
    start_date: date,
    target_daily_budget_min: int = 420,  # 单日游玩+通勤预算（不含用餐）
    max_spots_per_day: int = 3,
    day_budgets_min: Optional[List[int]] = None,  # 逐日预算，覆盖 target_daily_budget_min（如返程日更短）
) -> Tuple[List[List[Spot]], List[Tuple[Spot, str]], List[str]]:
    """空间聚类 + 闭馆避让 + 按数量上限与时间预算装箱。

    返回 (每日景点, [(未安排景点, 原因)], 日历提示)。
    1. 空间层次聚类：同片区景点归到同一天；
    2. 闭馆防呆交换：遇闭馆日与其他天对调；
    3. 容量装箱：超出 max_spots_per_day 或单日时间预算的景点，
       优先挪到「有余量且离得最近」的其他天，仍放不下的如实列入未安排。
    """
    if not spots:
        return [[] for _ in range(total_days)], [], []

    budgets = list(day_budgets_min or [])
    budgets += [target_daily_budget_min] * (total_days - len(budgets))

    calendar_warnings: List[str] = []
    unassigned: List[Tuple[Spot, str]] = []
    date_list = [start_date + timedelta(days=d) for d in range(total_days)]

    def unassigned_reason(s: Spot) -> str:
        if all(is_spot_closed_on_date(s, d)[0] for d in date_list):
            return "行程日期内均可能闭馆"
        need = estimate_route_minutes(hotel, [s])
        if all(need > budgets[i] for i, d in enumerate(date_list) if not is_spot_closed_on_date(s, d)[0]):
            return f"可用游玩时间不足（含路程约需 {need} 分钟）"
        return f"每日景点数（≤{max_spots_per_day}）或游玩时长已排满"

    if len(spots) <= EXACT_MAX_SPOTS:
        chunks = _exact_split(hotel, spots, date_list, budgets, max_spots_per_day)
        placed = {id(s): d for d, chunk in enumerate(chunks) for s in chunk}
        for s in spots:
            if id(s) not in placed:
                unassigned.append((s, unassigned_reason(s)))
                continue
            closed = [d for d in date_list if is_spot_closed_on_date(s, d)[0]]
            if closed:
                d = placed[id(s)]
                calendar_warnings.append(
                    f"【{s.name}】在 {'、'.join(x.strftime('%m-%d') for x in closed)} 可能闭馆，"
                    f"已安排在第 {d + 1} 天（{date_list[d].strftime('%m-%d')}）。"
                )
        return chunks, unassigned, calendar_warnings

    # 景点太多时穷举太慢，退回启发式：聚类 → 闭馆对调 → 装箱

    # 1. 纯数学空间层次聚类：按地理距离将 M 个景点聚为 total_days 个紧密簇
    raw_chunks = cluster_spots_spatially(hotel, spots, total_days, max_cluster_minutes=max(budgets))

    # 2. 闭馆防呆交换：如果某天分到的景点在当天闭馆，尝试与其他天的点调换
    for day_idx in range(total_days):
        curr_date = date_list[day_idx]
        chunk = raw_chunks[day_idx]
        for s in list(chunk):
            is_closed, _ = is_spot_closed_on_date(s, curr_date)
            if not is_closed:
                continue
            for other_day in range(total_days):
                if other_day == day_idx or not raw_chunks[other_day]:
                    continue
                other_closed, _ = is_spot_closed_on_date(s, date_list[other_day])
                candidate = raw_chunks[other_day][-1]
                cand_closed, _ = is_spot_closed_on_date(candidate, curr_date)
                if not other_closed and not cand_closed:
                    chunk.remove(s)
                    raw_chunks[other_day].remove(candidate)
                    chunk.append(candidate)
                    raw_chunks[other_day].append(s)
                    calendar_warnings.append(
                        f"动态日程调度：【{s.name}】在 {curr_date.strftime('%Y-%m-%d')} 可能闭馆，"
                        f"已与第 {other_day + 1} 天行程对调。"
                    )
                    break

    # 3. 容量装箱：先把超额 / 超时 / 仍闭馆的点拿出来
    pool: List[Spot] = []
    for day_idx in range(total_days):
        chunk = raw_chunks[day_idx]
        for s in list(chunk):
            if is_spot_closed_on_date(s, date_list[day_idx])[0]:
                chunk.remove(s)
                pool.append(s)
        chunk[:] = optimize_spots_route(hotel, chunk)[0]
        while chunk and (
            len(chunk) > max_spots_per_day
            or estimate_route_minutes(hotel, chunk) > budgets[day_idx]
        ):
            c_lng, c_lat = _centroid(chunk, hotel)
            # 优先拿出离本日重心最远的点（最可能造成折返）
            far = max(chunk, key=lambda x: haversine_distance(c_lng, c_lat, x.lng, x.lat))
            chunk.remove(far)
            pool.append(far)
            chunk[:] = optimize_spots_route(hotel, chunk)[0]

    # 再把拿出来的点放回有余量的天，时长长的先放
    for s in sorted(pool, key=lambda x: -x.duration_minutes):
        best_day, best_dist = None, float("inf")
        for day_idx in range(total_days):
            chunk = raw_chunks[day_idx]
            if len(chunk) >= max_spots_per_day:
                continue
            if is_spot_closed_on_date(s, date_list[day_idx])[0]:
                continue
            trial = optimize_spots_route(hotel, chunk + [s])[0]
            if estimate_route_minutes(hotel, trial) > budgets[day_idx]:
                continue
            c_lng, c_lat = _centroid(chunk, hotel)
            d = haversine_distance(c_lng, c_lat, s.lng, s.lat)
            if d < best_dist:
                best_day, best_dist = day_idx, d
        if best_day is not None:
            raw_chunks[best_day][:] = optimize_spots_route(hotel, raw_chunks[best_day] + [s])[0]
        else:
            unassigned.append((s, unassigned_reason(s)))

    return raw_chunks, unassigned, calendar_warnings
