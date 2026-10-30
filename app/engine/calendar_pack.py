import itertools
import re
from functools import lru_cache
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
from .models import LocationPoint, Spot
from .optimizer import optimize_spots_route
from .geo import (all_points, area_distance, cluster_spots_spatially, entry_points, estimate_transit, exit_points,
                  haversine_distance, place_distance)
from .timeline import day_is_feasible
from .hours import (_WEEKDAY_CHARS, _D, _WD, _RANGE, _WEEKDAY_SPEC_RE, CLOSED_MARK, _resolve, _spec_days, _weekday_part, strip_holiday_notes,  # 星期正则共用
                    day_span as _day_span, hours_on_date)

# 默认闭馆规则：常见特大博物馆逢周一闭馆
MONDAY_CLOSED_KEYWORDS = ["博物馆", "博物院", "纪念馆", "美术馆", "科技馆"]


_CLOSED_WORDS = r"\s*(?:全天)?(?:闭馆|休馆|闭园|休息|休业|不开放|停止开放|歇业)"
# 「周一至周三闭馆」
_CLOSED_RANGE_RE = re.compile(_RANGE + _CLOSED_WORDS)
# 「周一闭馆」「每周一、二休息」
_CLOSED_LIST_RE = re.compile(rf"(?:周|星期|礼拜)({_D}(?:[、,，和及]?(?:周|星期|礼拜)?{_D})*)" + _CLOSED_WORDS)


# 「墓室仅周二至周五开放」「地宫仅周末开放」：只说某个子设施，不代表整个景区的开放日。
# 实测中山陵营业时间原文含这句，旧逻辑把它当成全景区只在周二至周五开放，周六日被误判闭馆。
_PARTIAL_ONLY_RE = re.compile(rf"([一-龥]{{1,8}})仅(?:在)?(?:每)?{_WD}[^；;，,。]*")
_WHOLE_SITE_SUBJECTS = ("景区", "本馆", "全馆", "园区", "场馆", "本景区", "全园", "景点")


def _strip_partial_clauses(text: str) -> str:
    def repl(m: re.Match) -> str:
        subject = m.group(1)
        return m.group(0) if any(subject.endswith(w) for w in _WHOLE_SITE_SUBJECTS) else " "
    return _PARTIAL_ONLY_RE.sub(repl, text)


def parse_closed_days(open_time: str) -> Optional[List[int]]:
    """从高德营业时间文本里解析每周闭馆日（0=周一）。

    - 「周一闭馆」「每周一、二休息」「周一至周三闭馆」→ 明确的闭馆日
    - 「周二至周日 09:00-17:00」→ 开放日之外的都算闭馆 → [0]
    - 文本里没有可识别的星期信息 → None（调用方再走名称关键词兜底）
    """
    text = strip_holiday_notes(_strip_partial_clauses(open_time or ""))
    closed: set = set()
    for a, b in _CLOSED_RANGE_RE.findall(text):
        closed.update(_day_span(a, b))
    text = _CLOSED_RANGE_RE.sub(" ", text)
    for chars in _CLOSED_LIST_RE.findall(text):
        closed.update(_WEEKDAY_CHARS[c] for c in chars if c in _WEEKDAY_CHARS)
    text = _CLOSED_LIST_RE.sub(" ", text)

    # 某天开不开直接用 hours_on_date 的同一判定（_weekday_part）：条款不覆盖当天时，先看条款前不带星期的通用时刻。
    # 只按「列出的开放日」反推会误判：南京古鸡鸣寺「07:00-17:00；月初周一、15开放时间为:05:30-17:00」被当成只有周一开放
    specs = list(_WEEKDAY_SPEC_RE.finditer(text))
    bodies = [text[m.end():specs[i + 1].start() if i + 1 < len(specs) else len(text)] for i, m in enumerate(specs)]
    if any(_resolve(body) not in (None, CLOSED_MARK) for body in bodies):
        closed |= {wd for wd in range(7) if _weekday_part(text, wd) == CLOSED_MARK}
    elif not closed:
        return None
    return sorted(closed)


def is_spot_closed_on_date(spot: Spot, target_date: date, ignore_pin: bool = False) -> Tuple[bool, str]:
    """判断景点在指定日期是否闭馆（去不了）。

    周一 weekday() == 0。
    用户指定了日期（spot.fixed_date）的，其他日子一律按去不了处理：分天、对调、空档补点、校验器
    都沿用这一个判定，不必各自再写一遍。ignore_pin=True 时只看营业时间（用于闭馆提示文案）。
    优先级：spot.closed_days > 营业时间文本里的星期信息 > 名称含博物馆等关键词时默认周一闭馆。
    """
    if spot.fixed_date and not ignore_pin and target_date != spot.fixed_date:
        return True, f"【{spot.name}】按要求安排在 {spot.fixed_date.strftime('%m-%d')}，其他日子不排"
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
        t_min, _ = estimate_transit(place_distance(cur, s))
        total += t_min + s.duration_minutes
        cur = s
    return total


def _centroid(spots: List[Spot], fallback: LocationPoint) -> Tuple[float, float]:
    if not spots:
        return fallback.lng, fallback.lat
    return sum(s.lng for s in spots) / len(spots), sum(s.lat for s in spots) / len(spots)


# 景点数不超过它时穷举所有分天方案（状态压缩 DP）。紧凑档 3 天、模型按「天数 × 上限」补满就是 12 个，
# 旧值 10 让这种常见情况落到启发式：随机 11~12 个点的测试里平均少排 1.4 个点、丢 1.4 个点名景点、每天多空 50 分钟。
# 12 个点 4 天约 0.4 秒，含夜游与连锁分店的最坏情况约 3.6 秒
EXACT_MAX_SPOTS = 12
UNASSIGNED_PENALTY = 10_000  # 少排一个景点的代价，远大于任何通勤差：先保证排进去的最多，再比路程
# 用户点名（must_visit）的景点额外的舍弃代价：放不下时先舍弃模型补充的。实测广州「把陈家祠换成长隆」，
# 5 个点放不下时引擎舍弃了最远的长隆——恰恰是用户这轮点名要加的——模型只好自己删别的景点重排。
MUST_VISIT_PENALTY = 100_000
# 每天负荷（游玩+通勤，分钟）平方和的权重：总量一定时平方和越小越均衡。旧版按「偏离平均值」计，平均值把排不进的
# 景点也算了进去（上海用例里迪士尼 540 分钟把平均值抬到 570），反而鼓励把一天塞满、另一天空 5 小时。
# 0.001：把 90 分钟从 470 分钟的一天挪到 210 分钟的一天，均衡项约省 30，抵得过约 30 分钟的额外通勤。
BALANCE_WEIGHT_SQ = 0.001
# 判断一天放不放得下时，通勤按直线估算的 1.25 倍算：实测高德驾车耗时普遍比直线估算长约 30%，
# 按估算刚好放得下的一天，排时间轴时会超时（上海用例武康路「拖到晚餐之后」）。只用于可行性判断，不影响路程比较。
FEASIBILITY_TRAVEL_FACTOR = 1.25
EVENING_WINDOW_MIN = 175  # 晚餐后到 22:30 可用于夜游的时间：同一天的夜游景点总时长不能超过它
EVENING_DAYTIME_PENALTY = 90  # 宜傍晚的景点只能放在晚上不可用的那天（如返程日）时的代价：能避开就避开
# 同片区：place_distance 不超过 SAME_AREA_KM 的两个景点步行可达，拆到两天时每对计 SAME_AREA_SPLIT_PENALTY。
# 南京实测：老门东与夫子庙相距 1.07km、中山陵与音乐台 0.91km，旧版只看通勤与均衡，把它们拆到不同天，
# 第二天又专程打车回同一片区。60 约等于多 1 小时通勤，压得过均衡项，又不至于为凑片区而丢景点。
SAME_AREA_KM = 1.2
SAME_AREA_SPLIT_PENALTY = 60
_PERIOD_RANK = {"morning": 0, "evening": 2}
# 估算分钟数相差不超过 ROUTE_TIE_MIN 的路线视为打平，选总公里数少的：步行段按 13 分钟/km、打车段另加 5 分钟起步，
# 分钟数会把「多打两段车」和「走两段路」算成差不多。南京实测第 3 天「老门东 → 总统府 → 夫子庙」71 分钟 14.5km，
# 「总统府 → 老门东 → 夫子庙」72 分钟 9.4km，旧版选了前者，来回坐车
ROUTE_TIE_MIN = 5


# 子景点：名称里含另一个所选景点的名称（去掉「景区」等后缀），且相距不超过 SUB_SPOT_MAX_KM。
# 南京实测：「钟山风景名胜区中山陵景区音乐台」就在中山陵景区里，模型没给时长时按独立景点 120 分钟算，
# 和中山陵一起放不下，被拆到另一天 07:00 专程打车去。识别出来后时长按 SUB_SPOT_DEFAULT_MIN（trip_input）。
# 距离上限 1.5km：音乐台离中山陵 0.91km；取 3km 时「西湖文化广场」这类只是名字带景区名的地点也会被认成子景点
SUB_SPOT_MAX_KM = 1.5
_AREA_SUFFIX_RE = re.compile(r"(?:风景名胜区|风景区|旅游区|景区|景点)$")
_BRACKET_RE = re.compile(r"[（(][^）)]*[）)]")


def is_sub_spot(child: Spot, parent: Spot) -> bool:
    """child 是不是 parent 景区里的一个点。括号里的内容（多为分店名，如「先锋书店(玄武湖分店)」）不参与比较。"""
    return _is_sub_spot(child.name, parent.name, _points_key(child, all_points), _points_key(parent, all_points))


def cap_spots(spots: List[Spot]) -> List[Spot]:
    """计入每日景点名额的景点：所属景区也在同一天的子景点不算（凤凰古城里的虹桥、沱江泛舟是顺带游的；
    第 4 轮测评虹桥因「名额已满」没排上，当天下午却空着 2 小时）。两个互相包含（「中山陵」「中山陵景区」）时都算。"""
    return [s for s in spots
            if not any(o is not s and is_sub_spot(s, o) and not is_sub_spot(o, s) for o in spots)]


@lru_cache(maxsize=10_000)
def _is_sub_spot(child: str, parent: str, child_pts: tuple, parent_pts: tuple) -> bool:
    # 按名称与坐标缓存：分天穷举时每种组合都要排一次路线
    core = _AREA_SUFFIX_RE.sub("", _BRACKET_RE.sub("", parent)).strip()
    if len(core) < 2 or child == parent or core not in _BRACKET_RE.sub("", child):
        return False
    return min(haversine_distance(x[0], x[1], y[0], y[1]) for x in child_pts for y in parent_pts) <= SUB_SPOT_MAX_KM


def _points_key(p, points=all_points) -> tuple:
    return tuple((g.lng, g.lat) for g in points(p))


@lru_cache(maxsize=200_000)
def _leg_between(ka: tuple, kb: tuple) -> int:
    # 与 place_distance 相同：取最近的一对出入口（ka 是出发地能离开的位置，kb 是目的地能进入的位置）
    return estimate_transit(min(haversine_distance(x[0], x[1], y[0], y[1]) for x in ka for y in kb))[0]


def _leg(a, b) -> int:
    """分天阶段的估算车程。按坐标值缓存（不按对象 id，多线程、对象回收后都不会串）：
    12 个景点穷举时同一对地点要算上百万次，不缓存时这里占分天耗时的一半以上。"""
    return _leg_between(_points_key(a, exit_points), _points_key(b, entry_points))


def _safe_leg(a, b) -> float:
    return _leg(a, b) * FEASIBILITY_TRAVEL_FACTOR


def _route_with_night(hotel: LocationPoint, spots: List[Spot], night: set,
                      day: Optional[date] = None) -> Tuple[List[Spot], int, bool]:
    """night（景点 id 集合）里的放在最后（晚上），宜早去的放最前，其余在中间。

    返回 (顺序, 往返通勤分钟, 时间上是否走得通)：在营业时间都对得上的顺序里取最短的；
    一个也走不通时返回最短顺序并标记 False（上海用例：田子坊艺术中心 13:00 才开，排在上午就放不下）。
    """
    # 只枚举满足「早 → 中 → 晚」的顺序：各组内部全排列再拼接（与「全排列后过滤」结果和先后次序完全相同，
    # 5 个点含 2 个夜游时只需 12 种而不是 120 种）
    rank = lambda s: 2 if id(s) in night else 0 if s.best_period == "morning" else 1
    groups = [[s for s in spots if rank(s) == r] for r in range(3)]
    # 两两车程先算好查表：place_distance 要比较各门区，逐个排列现算是分天阶段的主要耗时
    pts_all = [hotel, *spots]
    legs = {(id(a), id(b)): _leg(a, b) for a in pts_all for b in pts_all}
    leg = lambda a, b: legs[id(a), id(b)]
    routes = []
    for parts in itertools.product(*(itertools.permutations(g) for g in groups)):
        perm = [s for part in parts for s in part]
        pts = [hotel, *perm, hotel]
        routes.append((sum(leg(a, b) for a, b in zip(pts, pts[1:])), list(perm)))
    if not routes:
        return list(spots), 0, True
    # 子景点与所属景区紧挨着游（中山陵 → 南博 → 音乐台 等于出了景区再进一次）；怎么排都挨不上时不强求
    pairs = [(a, b) for a in spots for b in spots if is_sub_spot(a, b)]
    if pairs:
        adjacent = [r for r in routes
                    if all(abs([id(x) for x in r[1]].index(id(a)) - [id(x) for x in r[1]].index(id(b))) == 1
                           for a, b in pairs)]
        routes = adjacent or routes
    routes.sort(key=lambda r: r[0])

    def timely(perm: List[Spot]) -> bool:
        daytime = [s for s in perm if id(s) not in night]
        return day_is_feasible(hotel, daytime, [s for s in perm if id(s) in night],
                               lambda a, b: leg(a, b) * FEASIBILITY_TRAVEL_FACTOR, day)

    def km(perm: List[Spot]) -> float:
        pts = [hotel, *perm, hotel]
        return sum(place_distance(a, b) for a, b in zip(pts, pts[1:]))

    first = next((i for i, (_, perm) in enumerate(routes) if timely(perm)), None)
    if first is None:
        return routes[0][1], routes[0][0], False
    shortest = routes[first][0]
    best_cost, best = routes[first]
    best_km = km(best)
    for cost, perm in routes[first + 1:]:
        if cost > shortest + ROUTE_TIE_MIN:
            break
        k = km(perm)
        if k < best_km - 0.5 and timely(perm):
            best_cost, best, best_km = cost, perm, k
    return best, best_cost, True


def split_evening(spots: List[Spot], evening_ok: bool, hotel: Optional[LocationPoint] = None,
                  day: Optional[date] = None) -> Tuple[List[Spot], List[Spot]]:
    """当天宜傍晚的景点里，哪些真的放在晚上、哪些只能改为白天去。返回 (晚上游, 改白天)。

    晚上不可用（返程日赶车）时全部改白天。否则在「总时长放得进晚餐后 EVENING_WINDOW_MIN」的组合里选：
    时间上走得通的优先，其次改白天的越少越好，再次全天路程最短（给了 hotel 时；广州用例里留广州塔在晚上会造成
    花城广场 → 大佛寺 → 广州塔 的往返，留大佛寺在晚上则顺路回住处），再次留时长长的。
    分天算法与排程器共用这个函数（传同一个 hotel 与日期），保证两边对「谁在晚上」的判断一致。
    """
    evening = [s for s in spots if s.best_period == "evening"]
    if not evening_ok:
        return [], evening
    best_key, best_kept = None, []
    for k in range(len(evening), -1, -1):
        for combo in itertools.combinations(evening, k):
            if sum(s.duration_minutes for s in combo) > EVENING_WINDOW_MIN:
                continue
            if hotel:
                _, travel, ok = _route_with_night(hotel, spots, {id(s) for s in combo}, day)
            else:
                travel, ok = 0, True
            key = (not ok, len(evening) - k, travel, -sum(s.duration_minutes for s in combo),
                   sorted(s.name for s in combo))
            if best_key is None or key < best_key:
                best_key, best_kept = key, list(combo)
    kept = [s for s in evening if s in best_kept]  # 保持原顺序
    return kept, [s for s in evening if s not in kept]


def pick_branches(hotel: LocationPoint, spots: List[Spot], evening_ok: bool = True,
                  day: Optional[date] = None) -> List[Spot]:
    """当天景点里有同品牌多家分店的（Spot.branches），逐个换成让当天路线走得通且通勤最短的一家；当天闭店的不选。

    南京实测：用户说「先锋书店」，模型选了五台山总店，从玄武湖打车 4km 过去，而玄武湖分店就在湖边。
    分店由确定性代码按路线挑，不交给模型凭印象选。"""
    chosen = list(spots)
    for i, s in enumerate(spots):
        if not s.branches:
            continue
        best_key, best = None, s
        for v in (s, *s.branches):
            if v is not s and day and is_spot_closed_on_date(v, day)[0]:
                continue
            order, travel, timely = _best_day_route(hotel, chosen[:i] + [v] + chosen[i + 1:], evening_ok, day)
            # 车程估算按分钟分档，两家常打平（南京实测都是 50 分钟），再按路线总公里数比
            pts = [hotel, *order, hotel]
            key = (not timely, travel, sum(place_distance(a, b) for a, b in zip(pts, pts[1:])))
            if best_key is None or key < best_key:
                best_key, best = key, v
        chosen[i] = best
    return chosen


def _insertion_km(v: Spot, others: List[LocationPoint]) -> float:
    """把 v 插进 others 里某两点之间（可以是同一点，如住处 → v → 住处）多走的直线距离，取最小。"""
    return min(place_distance(p, v) + place_distance(v, q) - (place_distance(p, q) if p is not q else 0)
               for p in others for q in others)


def pick_branches_near(hotel: LocationPoint, spots: List[Spot], day: Optional[date] = None) -> List[Spot]:
    """pick_branches 的快速版，供分天穷举用：每家分店只按「插进当天其他地点之间多走多远」比，不逐家重排整天路线。
    12 个景点 4 天、含 2 个连锁店时，逐家重排要 12 秒，这样约 1.5 秒；选出的组合随后仍按完整路线校验能否走通。"""
    chosen = list(spots)
    for i, s in enumerate(spots):
        if not s.branches:
            continue
        others = [hotel, *chosen[:i], *chosen[i + 1:]]
        variants = [v for v in (s, *s.branches) if v is s or not (day and is_spot_closed_on_date(v, day)[0])]
        chosen[i] = min(variants, key=lambda v: _insertion_km(v, others))
    return chosen


def _best_day_route(hotel: LocationPoint, spots: List[Spot], evening_ok: bool = True,
                    day: Optional[date] = None) -> Tuple[List[Spot], int, bool]:
    """当天景点的最短游览顺序（住宿出发、回到住宿）及往返通勤分钟数。

    遵守时段约束：宜早去的排最前、真正放在晚上的（见 split_evening）排最后；改为白天的按白天景点排。
    每天至多 4 个景点，全排列最多 24 种，直接穷举。
    """
    night = {id(s) for s in split_evening(spots, evening_ok, hotel, day)[0]}
    return _route_with_night(hotel, spots, night, day)


def _exact_split(
    hotel: LocationPoint,
    spots: List[Spot],
    date_list: List[date],
    budgets: List[int],
    max_spots_per_day: int,
    evening_ok: List[bool],
    end_points: List[Optional[LocationPoint]],
) -> List[List[Spot]]:
    """穷举所有「景点 → 天」的分配，选出：排进去的景点最多 → 总通勤最短 → 每天负荷最均衡。

    每天的景点子集必须满足：当天不闭馆、白天不超过每日景点数（夜游另加 1 个）、游玩+通勤不超过当天预算。
    步行可达（≤ SAME_AREA_KM）的景点尽量放同一天，拆开计 SAME_AREA_SPLIT_PENALTY。
    宜傍晚的景点优先放在晚上可用的日子；放到晚上不可用的那天（如返程日）时按白天游，并计入额外代价。
    end_points[d] 非空时（返程日的车站），可行性按「最后一个景点 → 车站」的实际估算距离判断。
    """
    n, days = len(spots), len(date_list)
    leg = _leg

    def feasible(order: List[Spot], night: set, d: int) -> bool:
        # 只算白天：晚餐后的夜游不占白天预算（它们另受 EVENING_WINDOW_MIN 约束）。
        # 旧版把夜游时长也算进白天，带一个 2 小时夜游的那天白天少 2 小时容量，多丢景点、下午空 5 小时。
        daytime = [s for s in order if id(s) not in night]
        travel, cur = 0, hotel
        for s in daytime:
            travel += leg(cur, s)
            cur = s
        if end_points[d] is not None:
            travel += leg(cur, end_points[d])
        play = sum(s.duration_minutes for s in daytime)
        return play + travel * FEASIBILITY_TRAVEL_FACTOR <= budgets[d]

    # 每天可行的子集：mask -> (代价, 游览顺序)
    options: List[Dict[int, Tuple[float, List[Spot]]]] = []
    # 键含各景点当天的营业时间：按星期分段营业的景点在不同日子结果不同，其余日子照常命中缓存。
    # 值为 (游览顺序, 通勤分钟, 时间上是否走得通, 晚上游, 改白天)，与 _best_day_route 的算法相同
    route_cache: Dict[tuple, tuple] = {}
    n_sub = n - len(cap_spots(spots))  # 子景点不占名额，一天最多可多排这么多个
    near_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)
                  if area_distance(spots[i], spots[j]) <= SAME_AREA_KM]
    for d in range(days):
        opts: Dict[int, Tuple[float, List[Spot]]] = {0: (0.0, [])}
        open_idx = [i for i in range(n) if not is_spot_closed_on_date(spots[i], date_list[d])[0]]
        # 每日景点数只限白天：晚餐后的夜游另加 1 个，不占白天名额（旧版夜游占名额，南京第 3 天白天只排 2 个、下午空 2.5 小时）
        for k in range(1, min(max_spots_per_day + 1 + n_sub, len(open_idx)) + 1):
            for combo in itertools.combinations(open_idx, k):
                counted = cap_spots([spots[i] for i in combo])
                if (sum(s.best_period != "evening" for s in counted) > max_spots_per_day
                        or len(counted) > max_spots_per_day + 1):
                    continue
                mask = sum(1 << i for i in combo)
                key = (mask, evening_ok[d], tuple(hours_on_date(spots[i].open_time, date_list[d]) for i in combo))
                if any(spots[i].branches for i in combo):
                    key += (date_list[d],)  # 各分店营业时间不同，按日期区分
                if key not in route_cache:
                    chosen = pick_branches_near(hotel, [spots[i] for i in combo], date_list[d])
                    night_, moved_ = split_evening(chosen, evening_ok[d], hotel, date_list[d])
                    route_cache[key] = (*_route_with_night(hotel, chosen, {id(s) for s in night_}, date_list[d]),
                                        night_, moved_)
                order, travel, timely, night, moved = route_cache[key]
                night_ids = {id(s) for s in night}
                if (sum(id(s) not in night_ids for s in cap_spots(order)) > max_spots_per_day or not timely
                        or not feasible(order, {id(s) for s in night}, d)):
                    continue  # 白天超过景点数上限、营业时段对不上（怎么排都走不通），或白天预算放不下
                load = travel + sum(s.duration_minutes for s in order)
                # 宜傍晚却只能白天去的（返程日、或当晚已排满夜游），每个计一笔代价：能避开就避开。
                # 但和当晚的夜游景点步行可达时不算（老门东下午逛、夫子庙晚上逛，正是同一片区的合理安排）
                moved = [m for m in moved if not any(area_distance(m, x) <= SAME_AREA_KM for x in night)]
                # 同片区被拆开：这一天含其中一个、不含另一个；两边的天各计一半，合起来每对一笔
                cut = sum(1 for i, j in near_pairs if (mask >> i & 1) != (mask >> j & 1))
                opts[mask] = (travel + BALANCE_WEIGHT_SQ * load * load + EVENING_DAYTIME_PENALTY * len(moved)
                              + SAME_AREA_SPLIT_PENALTY * cut / 2, order)
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
        dropped = [i for i in range(n) if not used >> i & 1]
        return (cost + UNASSIGNED_PENALTY * len(dropped)
                + MUST_VISIT_PENALTY * sum(spots[i].must_visit for i in dropped))

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
    evening_ok: Optional[List[bool]] = None,  # 逐日晚上是否可用（返程日赶车时不可用），默认都可用
    end_points: Optional[List[Optional[LocationPoint]]] = None,  # 逐日终点（返程日为车站），默认回住宿
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
        if s.fixed_date:
            n = (s.fixed_date - start_date).days + 1
            if is_spot_closed_on_date(s, s.fixed_date, ignore_pin=True)[0]:
                return f"指定的第 {n} 天（{s.fixed_date.strftime('%m-%d')}）可能闭馆"
            return f"指定在第 {n} 天，" + generic_reason(s)
        return generic_reason(s)

    def generic_reason(s: Spot) -> str:
        if all(is_spot_closed_on_date(s, d)[0] for d in date_list):
            return "行程日期内均可能闭馆"
        need = estimate_route_minutes(hotel, [s])
        open_days = [(i, d) for i, d in enumerate(date_list) if not is_spot_closed_on_date(s, d)[0]]
        if all(need > budgets[i] for i, d in open_days):
            return f"可用游玩时间不足（含路程约需 {need} 分钟）"
        # 单独排这一个都对不上营业时间（白天、夜游都试），说清楚是营业时间的问题，而不是笼统的「排满了」
        night_ok = list(evening_ok or [True] * total_days)
        if not any(day_is_feasible(hotel, [s], [], _safe_leg, d)
                   or (s.best_period == "evening" and night_ok[i] and day_is_feasible(hotel, [], [s], _safe_leg, d))
                   for i, d in open_days):
            return f"营业时间（{s.open_time}）内放不下 {s.duration_minutes} 分钟的游览"
        return f"每日白天景点数（≤{max_spots_per_day}，夜游另加 1 个）或游玩时长已排满"

    if len(spots) <= EXACT_MAX_SPOTS:
        chunks = _exact_split(hotel, spots, date_list, budgets, max_spots_per_day,
                              list(evening_ok or [True] * total_days),
                              list(end_points or [None] * total_days))
        placed = {id(s): d for d, chunk in enumerate(chunks) for s in chunk}
        for s in spots:
            # 换成同品牌分店的，按排进去的那家算
            s = next((v for v in (s, *s.branches) if id(v) in placed), s)
            if id(s) not in placed:
                unassigned.append((s, unassigned_reason(s)))
                continue
            closed = [d for d in date_list if is_spot_closed_on_date(s, d, ignore_pin=True)[0]]
            if closed:
                d = placed[id(s)]
                calendar_warnings.append(
                    f"【{s.name}】在 {'、'.join(x.strftime('%m-%d') for x in closed)} 可能闭馆，"
                    f"已安排在第 {d + 1} 天（{date_list[d].strftime('%m-%d')}）。"
                )
        return chunks, unassigned, calendar_warnings

    # 景点太多时穷举太慢，退回启发式：聚类 → 闭馆对调 → 装箱。
    # 名额与预算的口径与穷举一致：白天 ≤ max_spots_per_day，夜游另加 1 个、不占白天预算
    def over_cap(chunk: List[Spot]) -> bool:
        counted = cap_spots(chunk)
        daytime = sum(x.best_period != "evening" for x in counted)
        return daytime > max_spots_per_day or len(counted) > max_spots_per_day + 1

    def day_minutes(chunk: List[Spot]) -> int:
        night = next((x for x in chunk if x.best_period == "evening"), None)
        return estimate_route_minutes(hotel, [x for x in chunk if x is not night])

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
        while chunk and (over_cap(chunk) or day_minutes(chunk) > budgets[day_idx]):
            c_lng, c_lat = _centroid(chunk, hotel)
            # 先拿没点名的，其中离本日重心最远的优先（最可能造成折返）；旧版不看 must，点名的景点常被挤掉
            far = max(chunk, key=lambda x: (not x.must_visit, haversine_distance(c_lng, c_lat, x.lng, x.lat)))
            chunk.remove(far)
            pool.append(far)
            chunk[:] = optimize_spots_route(hotel, chunk)[0]

    def _swap_out_optional(s: Spot, chunks: List[List[Spot]]) -> Optional[Tuple[int, Spot]]:
        """找一天、换掉其中一个没点名的景点后能放下 s；有多种换法时换离 s 最近的那天、时长最长的那个。"""
        best, best_key = None, None
        for day_idx, chunk in enumerate(chunks):
            if is_spot_closed_on_date(s, date_list[day_idx])[0]:
                continue
            c_lng, c_lat = _centroid(chunk, hotel)
            for out in chunk:
                if out.must_visit or out.fixed_date:
                    continue
                rest = [x for x in chunk if x is not out]
                if over_cap(rest + [s]) or day_minutes(optimize_spots_route(hotel, rest + [s])[0]) > budgets[day_idx]:
                    continue
                key = (haversine_distance(c_lng, c_lat, s.lng, s.lat), -out.duration_minutes)
                if best_key is None or key < best_key:
                    best, best_key = (day_idx, out), key
        return best

    # 再把拿出来的点放回有余量的天：指定了日期的先放（只有一天可选），其次点名的，再次时长长的
    for s in sorted(pool, key=lambda x: (x.fixed_date is None, not x.must_visit, -x.duration_minutes)):
        best_day, best_dist = None, float("inf")
        for day_idx in range(total_days):
            chunk = raw_chunks[day_idx]
            if over_cap(chunk + [s]):
                continue
            if is_spot_closed_on_date(s, date_list[day_idx])[0]:
                continue
            trial = optimize_spots_route(hotel, chunk + [s])[0]
            if day_minutes(trial) > budgets[day_idx]:
                continue
            c_lng, c_lat = _centroid(chunk, hotel)
            d = haversine_distance(c_lng, c_lat, s.lng, s.lat)
            if d < best_dist:
                best_day, best_dist = day_idx, d
        if best_day is not None:
            raw_chunks[best_day][:] = optimize_spots_route(hotel, raw_chunks[best_day] + [s])[0]
        elif s.must_visit and (swap := _swap_out_optional(s, raw_chunks)):
            # 点名的放不下时，换掉某天一个没点名的（随机压力测试：必去的博物馆没排上，没点名的景区却占着一整天）
            day_idx, out = swap
            raw_chunks[day_idx][:] = optimize_spots_route(hotel, [x for x in raw_chunks[day_idx] if x is not out] + [s])[0]
            unassigned.append((out, unassigned_reason(out)))
        else:
            unassigned.append((s, unassigned_reason(s)))

    return raw_chunks, unassigned, calendar_warnings
