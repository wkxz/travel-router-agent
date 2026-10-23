"""行程日程编排器：融合日历闭馆检测、时间预算装箱、营业时间可行性校验与返程点接驳。

所有时刻都以「当日 00:00 起的分钟数」推进，每一段通勤都计入时间轴；
任何一项排不进可行时段时，宁可列入「未安排」也不硬塞，避免产出走不通的行程。
"""

from datetime import datetime, date, timedelta, timezone
from typing import List, Optional, Tuple

from .models import (
    LocationPoint,
    Spot,
    Restaurant,
    ScheduleItem,
    DayItinerary,
    FullTripPlan,
)
from .geo import (
    haversine_distance,
    minutes_to_hhmm,
    parse_time_to_minutes,
    find_open_slot,
    check_open_window,
    first_open_minute,
)
from .calendar_pack import (split_spots_by_time_and_calendar, is_spot_closed_on_date, split_evening,
                            EXACT_MAX_SPOTS)
from .validator import validate_plan, InfeasiblePlanError
from .travel import TravelTimes

CHINA_TZ = timezone(timedelta(hours=8))  # 固定 UTC+8，不依赖 tzdata（Windows 上 zoneinfo 缺数据）


def today_cn() -> date:
    """北京时间的今天；部署节点的系统时区可能是 UTC，不能直接用 date.today()。"""
    return datetime.now(CHINA_TZ).date()

DAY_START = 9 * 60            # 每天 09:00 出发
LUNCH_EARLIEST = 11 * 60 + 30
LUNCH_EARLIEST_RUSH = 11 * 60  # 返程日赶车时允许提前到 11:00 吃午饭
LUNCH_DEFAULT = 12 * 60
LUNCH_DURATION = 75
LUNCH_TRIGGER = 13 * 60 + 30  # 若下一个景点会拖过这个时间，先吃午饭
SNACK_EARLIEST = 14 * 60 + 30
SNACK_LATEST_START = 17 * 60
SNACK_DURATION = 40
DINNER_DEFAULT = 18 * 60
DINNER_DURATION = 80
SPOT_LATEST_END = 18 * 60 + 30  # 白天景点最晚结束时间，之后留给晚餐
EVENING_LATEST_END = 22 * 60 + 30  # 夜游景点最晚结束时间
EARLIEST_DEPART = 7 * 60      # 「宜早去」景点最早 07:00 出发
MORNING_ARRIVE_DEFAULT = 8 * 60  # 「宜早去」但营业时间未知时，按 08:00 到达
STATION_BUFFER = 60           # 提前到站/机场的最少缓冲
MAX_MEAL_DIST_KM = 2.5        # 餐厅离当前位置的直线距离上限，超过宁可「周边自选」也不绕路
REPEAT_CUISINE_PENALTY = 6.0  # 同一菜系已吃过一次后的降权
FAST_FOOD_MEAL_PENALTY = 4.0  # 正餐时段降权高德分类为「快餐厅」的店；附近只有它时仍可选
PRICEY_THRESHOLD = 150        # 用户没给预算时，人均超过这个数的店按超出部分降权（实测曾选中人均 ¥331 / ¥406 的店）
PRICEY_PENALTY_PER_100 = 1.5  # 每超出 ¥100 扣的分，约等于评分低 1 分
MUST_EAT_BONUS = 3.0          # 用户点名的必吃美食还没吃到时的加分
ON_THE_WAY_KM = 1.0           # 比直接去下一站多走不超过 1km 的餐厅算「顺路」，即使离当前位置超过 MAX_MEAL_DIST_KM
MUST_EAT_MAX_DETOUR_KM = 1.0  # 必吃在绕路不超过这个距离时强制优先；更远的留给更顺路的饭点（最后一顿除外）。
                              # 实测 2.5km 太宽：广州陈家祠→沙面路上有绕路 0km 的店，却被强制换成绕路 2.1km 的必吃店

# 这些是高德的笼统分类，不代表具体菜系，不参与菜系去重
GENERIC_CUISINES = {"", "中餐厅", "餐饮相关场所", "餐饮服务", "外国餐厅", "快餐厅", "综合酒楼", "特色/地方风味餐厅"}
LEAVE_PREP = 15               # 结束活动到动身前往车站的准备时间
GAP_FILL_MIN = 60             # 空档 ≥ 60 分钟才尝试顺路加游一个景点
GAP_NOTE_MIN = 30             # 空档 ≥ 30 分钟（且没填上）才给出自由活动建议
GAP_SPOT_MAX_KM = 3.0         # 空档加游的景点离当前位置的直线距离上限
GAP_MAX_WAIT = 30             # 空档加游时最多等开门 30 分钟
HOTEL_NEAR_KM = 2.0           # 离住宿点这么近时，建议回酒店休息
EVENING_SPOT_TYPICAL = 120    # 判断返程日晚上能否夜游时，按夜游 2 小时估算
GAP_RETURN_MIN = 150          # 白天行程结束后离晚餐还有 2.5 小时以上、且离住处较远时，先回住处休整

# 节奏档位：每日景点上限 + 单日游玩与通勤预算（分钟，不含用餐）
PACE_PROFILES = {
    "relaxed": (2, 330),
    "balanced": (3, 420),
    "compact": (4, 480),
}


def cuisine_key(r: Restaurant) -> Optional[str]:
    """菜系去重用的归一化键：优先用命中的必吃美食，其次用高德品类；笼统分类返回 None（不去重）。"""
    if r.dish_key:
        return r.dish_key
    c = (r.cuisine or "").strip()
    return None if c in GENERIC_CUISINES else c


def order_by_period(spots: List[Spot]) -> List[Spot]:
    """宜早去的排最前、宜傍晚的排最后，其余保持原有的动线顺序。"""
    rank = {"morning": 0, "evening": 2}
    return sorted(spots, key=lambda x: rank.get(x.best_period, 1))


def _fmt_duration(minutes: int) -> str:
    if minutes < 90:
        return f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    if 15 <= rest < 45:
        return f"{hours} 个半小时"
    return f"{hours + (1 if rest >= 45 else 0)} 小时"


def _fmt_rating(rating: Optional[float]) -> str:
    return f"高德评分 {rating:g} 分" if rating is not None else "评分暂无数据"


def _fmt_cost(cost: Optional[float], label: str) -> str:
    if cost is None:
        return f"{label}暂无数据"
    if cost <= 0:
        return f"{label}: 免费"
    return f"{label}约 ¥{cost:.0f}/人"


class TripScheduler:
    """旅游行程日程编排器。"""

    def __init__(
        self,
        city: str,
        total_days: int,
        hotel: Optional[LocationPoint] = None,
        departure_point: Optional[LocationPoint] = None,  # 返程终点（如高铁站/机场）
        start_date: Optional[str] = None,  # 出行起始日期 "YYYY-MM-DD"
        pace: str = "balanced",
        departure_time: Optional[str] = None,  # 返程车次/航班时间 "HH:MM"
        travel: Optional[TravelTimes] = None,  # 通勤数据源；默认按直线距离估算，工具层注入高德路网
        price_sensitive: bool = True,  # 用户没给预算时为 True：对高价店降权；给了人均上限（已在输入层过滤）时为 False
    ):
        self.price_sensitive = price_sensitive
        self.city = city
        self.travel = travel or TravelTimes()
        self.total_days = max(1, total_days)
        self.hotel = hotel
        self.departure_point = departure_point
        self.pace = pace if pace in PACE_PROFILES else "balanced"
        self.max_spots_per_day, self.daily_budget_min = PACE_PROFILES[self.pace]
        self.notes: List[str] = []

        self.start_date = today_cn()
        if start_date:
            try:
                self.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            except ValueError:
                self.notes.append(f"出行日期「{start_date}」无法解析，已按今天 {self.start_date} 推算闭馆日。")

        self.departure_min: Optional[int] = None
        if departure_time:
            self.departure_min = parse_time_to_minutes(departure_time)
            if self.departure_min is None:
                self.notes.append(f"返程时间「{departure_time}」无法解析（需 HH:MM），已按晚餐后出发处理。")

    # ---------- 工具方法 ----------

    def _pick_best_restaurant(
        self,
        anchor: LocationPoint,
        meal_types: Tuple[str, ...],
        restaurants: List[Restaurant],
        used_names: set,
        origin: LocationPoint,
        now: int,
        earliest: int,
        duration: int,
        used_cuisines: Optional[set] = None,
        exclude: Optional[set] = None,
        next_stop: Optional[LocationPoint] = None,
        last_chance: bool = False,
    ) -> Optional[Restaurant]:
        """在指定类型中就近挑选营业中的餐厅；类型严格匹配，不拿小吃顶替正餐。只挑选，不登记为已用。

        - 只考虑 MAX_MEAL_DIST_KM 以内的候选：附近没有就返回 None，由调用方给出「周边自选」，
          绝不为了凑一家店打车绕路；
        - 按「从 origin 出发、now 时刻动身」估算的到店时刻校验营业时间，确定不营业的直接排除；
          这里只用估算筛选，调用方再按真实路段复核，不营业就把它放进 exclude 重新挑；
        - 权重因子：距离近 > 评分高 > 营业状态。缺评分时按中性值参与排序，但不对外展示；
        - 传入 next_stop（吃完要去的下一站）时，按「绕路距离」排序：origin → 餐厅 → next_stop
          比直接去 next_stop 多走多少。只看离当前位置多近，会选出「近但方向相反」的店，造成折返；
        - 传入 used_cuisines 时，用户点名且还没吃到的必吃美食（dish_key）在顺路（绕路 ≤ MUST_EAT_MAX_DETOUR_KM）
          时优先于其他所有候选；不顺路就留给后面更顺路的饭点，到全程最后一顿（last_chance）时无条件优先。
          必吃全程只排一次；其他已吃过的菜系降权。
        """
        def detour(r: Restaurant) -> float:
            if next_stop is None:
                return haversine_distance(anchor.lng, anchor.lat, r.lng, r.lat)
            return (haversine_distance(origin.lng, origin.lat, r.lng, r.lat)
                    + haversine_distance(r.lng, r.lat, next_stop.lng, next_stop.lat)
                    - haversine_distance(origin.lng, origin.lat, next_stop.lng, next_stop.lat))

        def open_status(r: Restaurant) -> Optional[bool]:
            start = max(now + self.travel.estimate(origin, r).minutes, earliest)
            return check_open_window(start, start + duration, r.open_time)[0]

        candidates = [
            r for r in restaurants
            if r.name not in used_names
            and not (exclude and r.name in exclude)
            and r.meal_type in meal_types
            # 离当前位置近，或者就在去下一站的路上（如回住处途中），都不算专程绕路
            and (haversine_distance(anchor.lng, anchor.lat, r.lng, r.lat) <= MAX_MEAL_DIST_KM
                 or (next_stop is not None and detour(r) <= ON_THE_WAY_KM))
            and not (used_cuisines is not None and r.dish_key and r.dish_key in used_cuisines)
            and open_status(r) is not False
        ]
        if not candidates:
            return None

        def score(r: Restaurant) -> float:
            dist = detour(r)
            open_bonus = 2.0 if open_status(r) else 0.0
            type_bonus = 0.5 if r.meal_type == meal_types[0] else 0.0
            key = cuisine_key(r)
            repeat_penalty = REPEAT_CUISINE_PENALTY if used_cuisines and key and key in used_cuisines else 0.0
            must_eat_bonus = MUST_EAT_BONUS if r.dish_key else 0.0  # 已吃过的必吃项在上面已被排除
            fast_food = FAST_FOOD_MEAL_PENALTY if "meal" in meal_types and "快餐厅" in r.category else 0.0
            pricey = (PRICEY_PENALTY_PER_100 * max(0.0, r.cost - PRICEY_THRESHOLD) / 100
                      if self.price_sensitive and r.cost is not None else 0.0)
            return ((r.rating if r.rating is not None else 4.0) * 1.5 - dist * 2.0
                    + open_bonus + type_bonus - repeat_penalty + must_eat_bonus - fast_food - pricey)

        # 还没吃到的必吃优先：只靠加分时，评分高又更近的普通餐厅会把它挤掉；但不顺路时不强求，避免专程折返
        def prioritized(r: Restaurant) -> bool:
            return (used_cuisines is not None and bool(r.dish_key)
                    and (last_chance or detour(r) <= MUST_EAT_MAX_DETOUR_KM))
        return max(candidates, key=lambda r: (prioritized(r), score(r)))

    def _infer_hotel(self, spots: List[Spot]) -> LocationPoint:
        if self.hotel:
            return self.hotel
        if not spots:
            raise ValueError("既没有住宿坐标也没有景点坐标，无法确定行程起点")
        lng = sum(s.lng for s in spots) / len(spots)
        lat = sum(s.lat for s in spots) / len(spots)
        self.notes.append("未提供住宿地点，已按景点地理中心估算起点，建议告知实际住宿位置后重新生成。")
        return LocationPoint(name=f"{self.city}住宿点（按景点中心估算）", lng=lng, lat=lat)

    def _departure_ok(self, end_min: int, place: LocationPoint, is_last_day: bool) -> bool:
        """返程日：活动结束后还要来得及赶到车站并留出缓冲。"""
        if not (is_last_day and self.departure_point and self.departure_min is not None):
            return True
        t_min = self.travel.leg(place, self.departure_point).minutes
        return end_min + LEAVE_PREP + t_min + STATION_BUFFER <= self.departure_min

    def _evening_ok(self) -> List[bool]:
        """每天晚上能否安排夜游：返程日要赶车，车次早于「晚餐 + 夜游 + 去车站」所需时间时不可用。"""
        ok = [True] * self.total_days
        if self.departure_point and self.departure_min is not None:
            need = (DINNER_DEFAULT + DINNER_DURATION + EVENING_SPOT_TYPICAL + LEAVE_PREP
                    + 30 + STATION_BUFFER)  # 30 分钟为到站通勤估计，与 _day_budgets 一致
            ok[-1] = self.departure_min >= need
        return ok

    def _day_budgets(self, station_leg_known: bool) -> List[int]:
        """逐日游玩+通勤预算。返程日：到发车前的可用时间扣掉进站缓冲、动身准备和午餐。

        station_leg_known=True 时分天算法会按「最后一个景点 → 车站」的实际估算距离计入去车站的路程；
        否则（景点多、走启发式分天）在这里按 30 分钟粗估扣掉。
        """
        budgets = [self.daily_budget_min] * self.total_days
        if self.departure_point and self.departure_min is not None:
            usable_end = self.departure_min - STATION_BUFFER - LEAVE_PREP - (0 if station_leg_known else 30)
            lunch = LUNCH_DURATION if usable_end > LUNCH_TRIGGER else 0
            budgets[-1] = max(0, min(self.daily_budget_min, usable_end - DAY_START - lunch))
        return budgets

    # ---------- 主流程 ----------

    def schedule(
        self,
        spots: List[Spot],
        restaurants: List[Restaurant],
        backup_spots: Optional[List[Spot]] = None,
        must_eat: Optional[List[str]] = None,
    ) -> FullTripPlan:
        """运行完整排程流程，产出结构化行程。

        backup_spots：可选的备选景点，只在某天出现大段空档且顺路时才加入，用不上不算「未安排」。
        must_eat：用户点名的必吃美食；优先安排，排不进的写入 plan.unmet_must_eat 并说明原因。
        """
        self.hotel = self._infer_hotel(spots)

        days_spots, packed_out, calendar_warnings = split_spots_by_time_and_calendar(
            hotel=self.hotel,
            spots=spots,
            total_days=self.total_days,
            start_date=self.start_date,
            target_daily_budget_min=self.daily_budget_min,
            max_spots_per_day=self.max_spots_per_day,
            day_budgets_min=self._day_budgets(station_leg_known=len(spots) <= EXACT_MAX_SPOTS),
            evening_ok=self._evening_ok(),
            end_points=[None] * (self.total_days - 1) + [self.departure_point],
        )
        # 未安排：名称 -> 原因（有序）；空档补进行程时从这里移除
        unassigned = {s.name: reason for s, reason in packed_out}
        main_names = {s.name for s in spots}
        # 空档候选池：先放用户想去但没排进去的，再放备选景点
        gap_pool: List[Spot] = [s for s, _ in packed_out]
        gap_pool += [b for b in (backup_spots or []) if b.name not in main_names]

        used_restaurant_names: set = set()
        used_cuisines: set = set()
        day_plans: List[DayItinerary] = []
        evening_ok = self._evening_ok()
        meal_gaps: List[dict] = []

        for day_idx in range(self.total_days):
            curr_date = self.start_date + timedelta(days=day_idx)
            is_last_day = day_idx == self.total_days - 1
            day = _DayBuilder(self, day_idx + 1, curr_date, is_last_day, restaurants,
                              used_restaurant_names, used_cuisines, gap_pool, unassigned)

            ordered = order_by_period(days_spots[day_idx])
            day.evening_ok = evening_ok[day_idx]
            # 晚上不可用（返程日赶车）或当晚已排满夜游时，多出的宜傍晚景点按白天游排，不能直接丢掉
            evening, demoted = split_evening(ordered, day.evening_ok)
            day.demoted = {s.name for s in demoted}
            daytime = [s for s in ordered if s not in evening]
            day.reserved_spots = len(evening)  # 夜游景点稍后才排，但要占每日景点数
            # 当天最后要去的地方：夜游景点 > 返程车站 > 住宿；餐厅按「往这个方向顺路」挑
            day.end_target = (evening[0] if evening
                              else self.departure_point if is_last_day and self.departure_point
                              else self.hotel)
            if daytime and daytime[0].best_period == "morning":
                day.start_early_for(daytime[0])

            lunch_done = False
            for s in daytime:
                t_min = self.travel.leg(day.loc, s).minutes
                if not lunch_done and day.t >= 11 * 60 and day.t + t_min + s.duration_minutes > LUNCH_TRIGGER:
                    day.add_lunch(next_stop=s)
                    lunch_done = True
                day.place_main_spot(s)

            if not lunch_done:
                day.fill_gap(day.lunch_earliest())
                day.add_lunch()
            # 夜游景点比住处更近时就别回去了，傍晚直接过去
            if not (evening and haversine_distance(day.loc.lng, day.loc.lat, evening[0].lng, evening[0].lat)
                    <= haversine_distance(day.loc.lng, day.loc.lat, self.hotel.lng, self.hotel.lat)):
                day.maybe_return_to_hotel(DINNER_DEFAULT - 15)
            day.fill_gap(SNACK_EARLIEST)
            day.add_snack()
            day.fill_gap(DINNER_DEFAULT - 15)
            # 有夜游景点时，晚餐就近选在夜游景点旁边，吃完直接逛
            day.add_dinner(anchor=evening[0] if evening else None)
            for s in evening:
                day.place_main_spot(s, latest_end=EVENING_LATEST_END)
            day.finish()

            day_plans.append(day.build())
            meal_gaps += day.gaps

        summary_text = (
            f"为您规划了 {self.city} {self.total_days} 天行程，"
            f"共安排 {sum(d.total_spots for d in day_plans)} 个景点。"
        )
        extra = self.notes + calendar_warnings + self.travel.notes()
        if extra:
            summary_text += "\n" + " \n".join(extra)

        plan = FullTripPlan(
            city=self.city,
            total_days=self.total_days,
            hotel=self.hotel,
            days=day_plans,
            unassigned_spots=[f"{name}（{reason}）" for name, reason in unassigned.items()],
            unmet_must_eat=self._unmet_must_eat(must_eat or [], restaurants, day_plans, used_cuisines),
            gaps=meal_gaps,
            summary=summary_text,
            departure_min=self.departure_min if self.departure_point else None,
        )
        # 排程与校验分开实现：排程逻辑有漏洞时，由校验器拦下，绝不输出走不通的行程
        plan.gaps += self._must_eat_gaps(plan, day_plans)
        errors = [i for i in validate_plan(plan, station_buffer=STATION_BUFFER, travel=self.travel)
                  if i.level == "error"]
        if errors:
            raise InfeasiblePlanError(errors)
        return plan

    @staticmethod
    def _must_eat_gaps(plan: FullTripPlan, day_plans: List[DayItinerary]) -> List[dict]:
        """没排上的必吃：给出行程里各景点的 id，模型可直接在这些景点附近按美食名检索。"""
        spot_ids = [it.location.poi_id for d in day_plans for it in d.items
                    if it.category == "spot" and it.location and it.location.poi_id]
        return [{"type": "must_eat_unmet", "dish": u.split("（", 1)[0], "reason": u,
                 "near_ids": spot_ids} for u in plan.unmet_must_eat]

    def _unmet_must_eat(self, must_eat: List[str], restaurants: List[Restaurant],
                        day_plans: List[DayItinerary], eaten: set) -> List[str]:
        """没排进行程的必吃美食及原因（「美食（原因）」），让模型据此补检索或如实告知用户。"""
        route = [it.location for d in day_plans for it in d.items if it.location]
        meal_names = {"meal": "正餐", "snack": "小吃", "night_market": "夜市"}
        result = []
        for dish in must_eat:
            if dish in eaten:
                continue
            cands = [r for r in restaurants if r.dish_key == dish]
            if not cands:
                result.append(f"{dish}（候选餐厅里没有与「{dish}」相关的店，需先用该美食名检索）")
                continue
            dist, nearest = min(
                (min(haversine_distance(p.lng, p.lat, r.lng, r.lat) for p in route), r) for r in cands
            ) if route else (float("inf"), cands[0])
            if dist > MAX_MEAL_DIST_KM:
                result.append(f"{dish}（检索到的 {len(cands)} 家店离行程路线都超过 {MAX_MEAL_DIST_KM:g} 公里，"
                              f"最近的【{nearest.name}】约 {dist:.1f} 公里；可在行程中景点附近重新检索）")
            else:
                kinds = "、".join(sorted({meal_names.get(r.meal_type, r.meal_type) for r in cands}))
                result.append(f"{dish}（附近有【{nearest.name}】，但营业时间或餐次类型（{kinds}）"
                              f"与行程里的用餐时段对不上；小吃只排在下午 14:30–17:00）")
        return result


class _DayBuilder:
    """单日时间轴：维护当前时刻与位置，逐项追加并做可行性校验。"""

    def __init__(self, sched: TripScheduler, day_num: int, curr_date: date, is_last_day: bool,
                 restaurants: List[Restaurant], used_names: set, used_cuisines: set,
                 gap_pool: List[Spot], unassigned: dict):
        self.s = sched
        self.used_cuisines = used_cuisines
        self.gap_pool = gap_pool
        self.unassigned = unassigned
        self.reserved_spots = 0
        self.evening_ok = True  # 当天晚上能否夜游（返程日赶车时为 False）
        self.demoted: set = set()  # 宜傍晚但改为白天游的景点名
        self.gaps: List[dict] = []  # 缺候选的饭点，汇总进 plan.gaps 供模型一次性补检索
        self.end_target: LocationPoint = sched.hotel  # 当天最后要去的地方，挑餐厅时据此判断顺不顺路
        self.free_start = 0
        self.day_num = day_num
        self.date = curr_date
        self.is_last_day = is_last_day
        self.restaurants = restaurants
        self.used_names = used_names
        self.t = DAY_START
        self.loc: LocationPoint = sched.hotel
        self.items: List[ScheduleItem] = []
        self.spot_names: List[str] = []
        self.cost = 0.0
        self.has_unknown_cost = False
        self.dist = 0.0
        self.skipped_meals: List[str] = []

        description = f"{curr_date.strftime('%Y-%m-%d')} 开启第 {day_num} 天行程"
        leave_hotel = self._latest_leave_hotel()
        if leave_hotel is not None and leave_hotel < DAY_START:
            # 返程太早：当天不安排任何活动，按「到站留足缓冲」倒推出门时间
            self.t = leave_hotel
            description += "（返程较早，收拾好直接前往车站）"

        self.items.append(ScheduleItem(
            time_slot=minutes_to_hhmm(self.t),
            category="hotel",
            title=f"从【{sched.hotel.name}】出发",
            description=description,
            location=sched.hotel,
        ))

    def _latest_leave_hotel(self) -> Optional[int]:
        """返程日最晚的出门时刻；非返程日或没有车次时间时返回 None。

        出门时刻早于 00:00 说明当天根本赶不上，直接报错，不输出走不通的行程。
        """
        dep, dep_min = self.s.departure_point, self.s.departure_min
        if not (self.is_last_day and dep and dep_min is not None):
            return None
        t_min = self.s.travel.leg(self.s.hotel, dep).minutes
        leave = dep_min - STATION_BUFFER - t_min - LEAVE_PREP
        if leave < 0:
            raise ValueError(
                f"返程时间 {minutes_to_hhmm(dep_min)} 过早：从住宿点到【{dep.name}】约需 {t_min} 分钟，"
                f"加上提前 {STATION_BUFFER} 分钟到站，需要在前一天夜里出发。"
                f"请确认车次时间，或把返程日改为前一天。"
            )
        return leave

    def _add_cost(self, cost: Optional[float]) -> None:
        if cost is None:
            self.has_unknown_cost = True
        else:
            self.cost += cost

    def start_early_for(self, s: Spot) -> None:
        """第一个景点「宜早去」时提前出发：按开门时间到达，最早 07:00 出发，最晚仍是 09:00。"""
        opening = first_open_minute(s.open_time)
        arrive = opening if opening is not None else MORNING_ARRIVE_DEFAULT
        t_min = self.s.travel.leg(self.loc, s).minutes
        depart = min(DAY_START, max(EARLIEST_DEPART, arrive - t_min))
        if depart >= self.t:
            return
        self.t = depart
        start_item = self.items[0]
        start_item.time_slot = minutes_to_hhmm(depart)
        start_item.description += f"（{s.name}宜早去，提前出发）"

    def place_main_spot(self, s: Spot, latest_end: int = SPOT_LATEST_END) -> None:
        """排主景点；排不进时记入未安排，同时放进空档候选池，后面哪天有空档还能补上。"""
        reason = self.add_spot(s, latest_end=latest_end)
        if reason:
            self.unassigned[s.name] = reason
            if s.best_period != "evening" or s.name in self.demoted:
                self.gap_pool.append(s)

    def _gap_until(self, until: int) -> Tuple[int, bool]:
        """返程日把空档截止时间收紧到「动身去车站」之前；返回 (截止时刻, 是否受赶车限制)。"""
        dep = self.s.departure_point
        if self.is_last_day and dep and self.s.departure_min is not None:
            t_min = self.s.travel.leg(self.loc, dep).minutes
            leave_by = self.s.departure_min - STATION_BUFFER - LEAVE_PREP - t_min
            if leave_by < until:
                return leave_by, True
        return until, False

    def _best_gap_spot(self, until: int) -> Optional[Spot]:
        """在候选池里找一个顺路、营业、能在 until 前游完的景点；用户想去的优先，其次按距离。"""
        best, best_key = None, None
        for idx, c in enumerate(self.gap_pool):
            # 宜傍晚的留给晚上；当天晚上不可用（返程日）时才允许白天补进去
            if ((c.best_period == "evening" and self.evening_ok and c.name not in self.demoted)
                    or is_spot_closed_on_date(c, self.date)[0]):
                continue
            dist = haversine_distance(self.loc.lng, self.loc.lat, c.lng, c.lat)
            if dist > GAP_SPOT_MAX_KM:
                continue
            t_min = self.s.travel.estimate(self.loc, c).minutes  # 筛选用估算；add_spot 再按真实路段复核
            status, start, _ = find_open_slot(self.t + t_min, c.duration_minutes, c.open_time, max_wait=GAP_MAX_WAIT)
            if status is False or start + c.duration_minutes > until:
                continue
            if not self.s._departure_ok(start + c.duration_minutes, c, self.is_last_day):
                continue
            key = (0 if c.name in self.unassigned else 1, dist)
            if best_key is None or key < best_key:
                best, best_key = c, key
        return best

    def maybe_return_to_hotel(self, until: int) -> None:
        """白天行程已结束、离晚餐还有很长空档、又离住处较远时，先回住处休整。

        否则会在远郊景点干等几个小时，晚餐也只能在当地凑合，最后再长途返回。
        附近还有能补的空档景点时不回（交给 fill_gap 顺路加游）。
        """
        hotel = self.s.hotel
        until, rushing = self._gap_until(until)
        if rushing or until - self.t < GAP_RETURN_MIN or self.loc is hotel:
            return
        if haversine_distance(self.loc.lng, self.loc.lat, hotel.lng, hotel.lat) <= HOTEL_NEAR_KM:
            return
        if (self.s.pace != "relaxed" and self._best_gap_spot(until)
                and len(self.spot_names) + self.reserved_spots < self.s.max_spots_per_day):
            return
        leg = self.s.travel.leg(self.loc, hotel)
        arrive = self.t + leg.minutes
        if arrive >= until:
            return
        self.items.append(ScheduleItem(
            time_slot=minutes_to_hhmm(arrive),
            category="hotel",
            title=f"返回【{hotel.name}】休整",
            description="白天的景点已游完，离晚餐还有较长空档，先回住处休息，傍晚就近用餐",
            location=hotel,
            transit_info=leg.desc,
        ))
        self.dist += leg.road_km
        self.t = arrive
        self.loc = hotel

    def fill_gap(self, until: int, note: bool = True) -> None:
        """处理 self.t 到 until 之间的空档：时间够就顺路加游，不够就给出建议并把时间轴推到 until。"""
        until, rushing = self._gap_until(until)
        if self.s.pace != "relaxed":
            while (until - self.t >= GAP_FILL_MIN
                   and len(self.spot_names) + self.reserved_spots < self.s.max_spots_per_day):
                cand = self._best_gap_spot(until)
                if cand is None or self.add_spot(cand, latest_end=until, filler=True):
                    break
                self.gap_pool.remove(cand)
                self.unassigned.pop(cand.name, None)

        gap = until - self.t
        if not note or gap < GAP_NOTE_MIN:
            return
        start = self.t
        if self.items and self.items[-1].category == "free":
            # 上一项也是自由活动（中间没有排进任何东西），合并成一行
            start = self.free_start
            self.items.pop()
            gap = until - start
        self.free_start = start
        self.items.append(ScheduleItem(
            time_slot=f"{minutes_to_hhmm(start)} - {minutes_to_hhmm(until)}",
            category="free",
            title=f"空档约 {_fmt_duration(gap)}：自由活动",
            description=self._gap_advice(gap, rushing),
        ))
        self.t = until

    def _gap_advice(self, gap: int, rushing: bool) -> str:
        if rushing:
            return "离动身去车站还有一段时间：可在附近逛逛、买些伴手礼，或提前前往车站候车，更从容。"
        if gap < GAP_FILL_MIN:
            return "时间不长，可在附近稍作休息、喝杯茶，或随意逛逛周边街巷。"
        hotel = self.s.hotel
        hotel_dist = haversine_distance(self.loc.lng, self.loc.lat, hotel.lng, hotel.lat)
        if hotel_dist <= HOTEL_NEAR_KM:
            return "离住宿点不远，可以回酒店休息一下再出发；也可以在附近街区随意逛逛。"
        if len(self.spot_names) + self.reserved_spots >= self.s.max_spots_per_day or self.s.pace == "relaxed":
            advice = "今天的景点已按当前节奏排满，可在附近慢慢逛、找地方喝茶休息，留些体力。"
        else:
            advice = ("附近暂无合适的待选景点，可在周边随意逛逛；"
                      "如果想多安排一处，可以告诉我想去的地方，我重新生成。")
        if gap >= 180:
            t_min = self.s.travel.leg(self.loc, hotel).minutes
            advice += f"空档较长，也可以先回住处休息（单程约 {t_min} 分钟）。"
        return advice

    def add_spot(self, s: Spot, latest_end: int = SPOT_LATEST_END, filler: bool = False) -> Optional[str]:
        """追加景点；放不进可行时段时返回原因（调用方列入未安排）。"""
        leg = self.s.travel.leg(self.loc, s)
        t_min, t_desc = leg.minutes, leg.desc
        status, start, open_msg = find_open_slot(self.t + t_min, s.duration_minutes, s.open_time)
        if status is False:
            return f"到达时已无法在营业时间内游完：{open_msg}"
        end = start + s.duration_minutes
        if end > latest_end:
            if latest_end == SPOT_LATEST_END:
                return "当日时间不足（会拖到晚餐之后）"
            return "夜间时段放不下（会晚于 22:30 结束）"
        if not self.s._departure_ok(end, s, self.is_last_day):
            return "返程日时间不足，来不及赶车"
        is_closed, closed_msg = is_spot_closed_on_date(s, self.date)
        if is_closed:
            return closed_msg

        warnings = []
        if status is None:
            warnings.append(open_msg)
        if start > self.t + t_min:
            warnings.append(f"到达后需等待开门（{minutes_to_hhmm(start)} 开放）")
        if filler:
            warnings.append("利用空档顺路加游")
        if s.best_period == "morning":
            warnings.append("宜早去：上午人少、体验更好")
        elif s.best_period == "evening":
            if latest_end == EVENING_LATEST_END:  # 排在晚上
                warnings.append("宜傍晚/夜间游览")
            elif not self.evening_ok:
                warnings.append("宜傍晚游览，因当天要赶车改为白天前往")
            else:
                warnings.append("宜傍晚游览，当晚已安排其他夜游，改为白天前往")

        self.items.append(ScheduleItem(
            time_slot=f"{minutes_to_hhmm(start)} - {minutes_to_hhmm(end)}",
            category="spot",
            title=f"游览【{s.name}】",
            description=f"建议游玩 {s.duration_minutes} 分钟。{_fmt_cost(s.cost, '门票参考')}。"
            + (f" 标签: {', '.join(s.tags)}" if s.tags else ""),
            location=s,
            transit_info=t_desc,
            cost=s.cost,
            open_time=s.open_time,
            rating=s.rating,
            warnings=warnings,
        ))
        self._add_cost(s.cost)
        self.dist += leg.road_km
        self.t = end
        self.loc = s
        self.spot_names.append(s.name)
        return None

    def _add_meal(self, label: str, category: str, meal_types: Tuple[str, ...], earliest: int,
                  duration: int, placeholder: Optional[str], anchor: Optional[LocationPoint] = None,
                  next_stop: Optional[LocationPoint] = None, last_chance: bool = False) -> bool:
        rejected: set = set()  # 估算时营业、按真实路段到店时已打烊的候选
        while True:
            rest = self.s._pick_best_restaurant(
                anchor or self.loc, meal_types, self.restaurants, self.used_names,
                origin=self.loc, now=self.t, earliest=earliest, duration=duration,
                used_cuisines=self.used_cuisines, exclude=rejected,
                next_stop=next_stop or self.end_target, last_chance=last_chance,
            )
            if not rest:
                road_km, t_desc, start, place = 0.0, None, max(self.t, earliest), self.loc
                break
            leg = self.s.travel.leg(self.loc, rest)
            road_km, t_desc, place = leg.road_km, leg.desc, rest
            start = max(self.t + leg.minutes, earliest)
            if check_open_window(start, start + duration, rest.open_time)[0] is not False:
                break
            rejected.add(rest.name)
        end = start + duration

        if not self.s._departure_ok(end, place, self.is_last_day):
            if earliest < self.s.departure_min:  # 饭点本就在发车之后的，不算「省略」
                self.skipped_meals.append(label)
            return False

        if rest:
            # 确认排入后才登记：赶车省略的餐不能把必吃记成「已吃过」
            self.used_names.add(rest.name)
            if cuisine_key(rest):
                self.used_cuisines.add(cuisine_key(rest))
            warnings = []
            is_open, open_msg = check_open_window(start, end, rest.open_time)
            if is_open is None:  # 确定不营业的已在上面排除
                warnings.append(open_msg)
            is_market = rest.meal_type == "night_market"
            title_label = "夜市逛吃" if is_market and category == "meal" else label
            self.items.append(ScheduleItem(
                time_slot=f"{minutes_to_hhmm(start)} - {minutes_to_hhmm(end)}",
                category=category,
                title=f"{title_label}：【{rest.name}】",
                description=f"{rest.cuisine or '地方风味'}，{_fmt_rating(rest.rating)}，"
                f"{_fmt_cost(rest.cost, '人均')}。{rest.recommend_reason}",
                location=rest,
                transit_info=t_desc,
                cost=rest.cost,
                open_time=rest.open_time,
                rating=rest.rating,
                warnings=warnings,
            ))
            self._add_cost(rest.cost)
            self.dist += road_km
            self.loc = rest
        elif placeholder:
            self.gaps.append({
                "type": "meal_without_candidate", "day": self.day_num, "meal": label,
                "near": self.loc.name, "near_id": self.loc.poi_id,
            })
            self.items.append(ScheduleItem(
                time_slot=f"{minutes_to_hhmm(start)} - {minutes_to_hhmm(end)}",
                category=category,
                title=f"{label}：{placeholder}",
                description=f"{MAX_MEAL_DIST_KM:g} 公里内未检索到合适的候选餐厅，可在附近街区自选，避免专程绕路",
            ))
        else:
            return False
        self.t = end
        return True

    def lunch_earliest(self) -> int:
        if self.is_last_day and self.s.departure_min is not None:
            return LUNCH_EARLIEST_RUSH
        # 上午 11 点后结束就 11:30 开饭，不再干等到 12:00
        return LUNCH_EARLIEST if self.t >= 11 * 60 else LUNCH_DEFAULT

    def add_lunch(self, next_stop: Optional[LocationPoint] = None) -> None:
        earliest = self.lunch_earliest()
        self._add_meal("午餐", "meal", ("meal",), earliest, LUNCH_DURATION, "景点周边自选简餐",
                       next_stop=next_stop)

    def add_snack(self) -> None:
        # 下午游览拖过 17:00 就不再安排小吃，避免和晚餐连着吃
        if self.t > SNACK_LATEST_START:
            return
        self._add_meal("特色小吃尝鲜", "snack", ("snack",), SNACK_EARLIEST, SNACK_DURATION, None)
        if self.skipped_meals and self.skipped_meals[-1] == "特色小吃尝鲜":
            self.skipped_meals.pop()  # 小吃本就可选，赶车时省略不必提示

    def add_dinner(self, anchor: Optional[LocationPoint] = None) -> None:
        farewell = self.is_last_day and self.s.departure_point is not None
        # 全程最后一顿正餐：还没吃到的必吃就算不太顺路也要安排
        self._add_meal("返程前告别餐" if farewell else "晚餐", "meal", ("night_market", "meal"),
                       DINNER_DEFAULT, DINNER_DURATION, "周边自选晚餐", anchor=anchor,
                       last_chance=self.is_last_day)

    def finish(self) -> None:
        dep = self.s.departure_point
        if self.is_last_day and dep:
            leg = self.s.travel.leg(self.loc, dep)
            t_min, t_desc = leg.minutes, leg.desc
            leave = self.t + LEAVE_PREP
            arrive = leave + t_min
            if self.s.departure_min is not None:
                desc = (f"车次/航班 {minutes_to_hhmm(self.s.departure_min)}，预计 {minutes_to_hhmm(arrive)} 到达，"
                        f"留出约 {self.s.departure_min - arrive} 分钟进站或值机。")
            else:
                desc = "未提供车次/航班时间，默认晚餐后前往，请按实际时间调整；建议提前 60~90 分钟到达。"
            warnings = []
            if self.skipped_meals:
                warnings.append(f"为保证赶车已省略{'、'.join(self.skipped_meals)}，建议在车站附近简单用餐")
            self.items.append(ScheduleItem(
                time_slot=f"{minutes_to_hhmm(leave)} - {minutes_to_hhmm(arrive)}",
                category="transit",
                title=f"前往【{dep.name}】准备返程",
                description=desc,
                location=dep,
                transit_info=t_desc,
                warnings=warnings,
            ))
            self.dist += leg.road_km
        else:
            hotel = self.s.hotel
            leg = self.s.travel.leg(self.loc, hotel)
            t_min, t_desc = leg.minutes, leg.desc
            self.items.append(ScheduleItem(
                time_slot=minutes_to_hhmm(self.t + t_min),
                category="hotel",
                title=f"返回【{hotel.name}】",
                description="结束一天行程，回酒店休整",
                location=hotel,
                transit_info=t_desc,
            ))
            self.dist += leg.road_km

    def build(self) -> DayItinerary:
        theme = f"{' + '.join(self.spot_names)} 深度游" if self.spot_names else "城市漫步休闲日"
        return DayItinerary(
            day_number=self.day_num,
            theme=theme,
            date=self.date.strftime("%Y-%m-%d"),
            items=self.items,
            total_spots=len(self.spot_names),
            total_cost=self.cost,
            estimated_distance_km=round(self.dist, 1),
            has_unknown_cost=self.has_unknown_cost,
        )
