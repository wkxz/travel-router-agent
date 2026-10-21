"""行程日程编排器：融合日历闭馆检测、时间预算装箱、用餐生理抑制与返程点接驳。"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Tuple, Dict, Any

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
    estimate_transit,
    minutes_to_hhmm,
    is_time_in_range,
)
from .optimizer import optimize_spots_route
from .calendar_pack import split_spots_by_time_and_calendar, is_spot_closed_on_date


class TripScheduler:
    """工业级旅游行程日程编排器。"""

    def __init__(
        self,
        city: str,
        total_days: int,
        hotel: Optional[LocationPoint] = None,
        departure_point: Optional[LocationPoint] = None,  # 返程终点（如高铁站/机场）
        start_date: Optional[str] = None,  # 出行起始日期 "YYYY-MM-DD"
        pace: str = "balanced",  # 当前固定为 balanced (均衡)
    ):
        self.city = city
        self.total_days = max(1, total_days)
        self.hotel = hotel or LocationPoint(name=f"{city}市中心住宿点", lng=116.4074, lat=39.9042)
        self.departure_point = departure_point
        self.pace = pace

        # 起始日期解析（默认当天）
        if start_date:
            try:
                self.start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            except Exception:
                self.start_date = date.today()
        else:
            self.start_date = date.today()

        # 节奏档位配置
        if pace == "relaxed":
            self.max_spots_per_day = 2
            self.min_spots_per_day = 1
        elif pace == "compact":
            self.max_spots_per_day = 4
            self.min_spots_per_day = 2
        else:  # balanced
            self.max_spots_per_day = 3
            self.min_spots_per_day = 1

    def _pick_best_restaurant(
        self,
        anchor: LocationPoint,
        target_meal_type: str,
        restaurants: List[Restaurant],
        used_names: set,
        target_time: str,
    ) -> Optional[Restaurant]:
        """就近挑选最合适且营业的餐饮/小吃。

        权重因子：距离近 > 评分高 > 营业状态。强距离惩罚杜绝跨城折返。
        """
        candidates = [
            r for r in restaurants
            if r.name not in used_names
            and (target_meal_type == "all" or r.meal_type == target_meal_type)
        ]

        if not candidates and target_meal_type != "all":
            candidates = [r for r in restaurants if r.name not in used_names]

        if not candidates:
            return None

        def score(r: Restaurant) -> float:
            dist = haversine_distance(anchor.lng, anchor.lat, r.lng, r.lat)
            is_open, _ = is_time_in_range(target_time, r.open_time)
            open_bonus = 2.0 if is_open else -5.0
            distance_penalty = dist * 2.0 if dist <= 3.0 else (dist * 3.5 + 5.0)
            return (r.rating or 4.0) * 1.5 - distance_penalty + open_bonus

        candidates.sort(key=score, reverse=True)
        chosen = candidates[0]
        used_names.add(chosen.name)
        return chosen

    def schedule(
        self,
        spots: List[Spot],
        restaurants: List[Restaurant],
    ) -> FullTripPlan:
        """运行完整排程流程，产出结构化行程。"""
        # 1. 连续分块切分 + 动态闭馆检测与避让调换
        days_spots, unassigned, calendar_warnings = split_spots_by_time_and_calendar(
            hotel=self.hotel,
            spots=spots,
            total_days=self.total_days,
            start_date=self.start_date,
            max_spots_per_day=self.max_spots_per_day,
        )

        used_restaurant_names = set()
        day_plans: List[DayItinerary] = []

        for day_idx in range(self.total_days):
            day_num = day_idx + 1
            curr_date = self.start_date + timedelta(days=day_idx)
            is_last_day = (day_num == self.total_days)

            curr_spots = days_spots[day_idx]
            current_time_min = 9 * 60  # 每天 09:00 出发
            current_loc = self.hotel
            day_items: List[ScheduleItem] = []
            day_cost = 0.0
            day_dist = 0.0

            # 出发
            day_items.append(
                ScheduleItem(
                    time_slot="09:00",
                    category="hotel",
                    title=f"从【{self.hotel.name}】出发",
                    description=f"{curr_date.strftime('%Y-%m-%d')} 晨起出行，开启第 {day_num} 天行程",
                    location=self.hotel,
                )
            )

            # 上午景点与下午景点划分
            morning_spots = curr_spots[:1] if len(curr_spots) <= 2 else curr_spots[:2]
            afternoon_spots = curr_spots[len(morning_spots):]

            # --- 上午行程 ---
            for s in morning_spots:
                dist = haversine_distance(current_loc.lng, current_loc.lat, s.lng, s.lat)
                t_min, t_desc = estimate_transit(dist)
                current_time_min += t_min
                day_dist += dist

                start_s = minutes_to_hhmm(current_time_min)
                end_min = current_time_min + s.duration_minutes
                end_s = minutes_to_hhmm(end_min)
                current_time_min = end_min

                warnings = []
                is_open, open_msg = is_time_in_range(start_s, s.open_time)
                if not is_open:
                    warnings.append(f"景点营业时间预警：{open_msg}")

                # 闭馆校验
                is_closed, closed_msg = is_spot_closed_on_date(s, curr_date)
                if is_closed:
                    warnings.append(closed_msg)

                day_items.append(
                    ScheduleItem(
                        time_slot=f"{start_s} - {end_s}",
                        category="spot",
                        title=f"游览【{s.name}】",
                        description=f"建议游玩 {s.duration_minutes} 分钟。门票参考: ¥{s.cost:.0f}/人。"
                        + (f" 标签: {', '.join(s.tags)}" if s.tags else ""),
                        location=s,
                        transit_info=t_desc,
                        cost=s.cost,
                        open_time=s.open_time,
                        rating=s.rating,
                        warnings=warnings,
                    )
                )
                day_cost += s.cost
                current_loc = s

            # --- 午餐安排 (12:00 ~ 13:30) ---
            lunch_start_min = max(current_time_min, 12 * 60)
            lunch_start_s = minutes_to_hhmm(lunch_start_min)
            lunch_end_s = minutes_to_hhmm(lunch_start_min + 75)
            current_time_min = lunch_start_min + 75

            lunch_rest = self._pick_best_restaurant(
                current_loc, "meal", restaurants, used_restaurant_names, lunch_start_s
            )
            if lunch_rest:
                dist = haversine_distance(current_loc.lng, current_loc.lat, lunch_rest.lng, lunch_rest.lat)
                _, t_desc = estimate_transit(dist, mode="walking" if dist < 1.0 else "auto")
                day_dist += dist
                warnings = []
                is_open, open_msg = is_time_in_range(lunch_start_s, lunch_rest.open_time)
                if not is_open:
                    warnings.append(open_msg)

                day_items.append(
                    ScheduleItem(
                        time_slot=f"{lunch_start_s} - {lunch_end_s}",
                        category="meal",
                        title=f"午餐：【{lunch_rest.name}】",
                        description=f"{lunch_rest.cuisine or '地道美食'}，高德评分 {lunch_rest.rating} 分，"
                        f"人均约 ¥{lunch_rest.cost:.0f}。{lunch_rest.recommend_reason}",
                        location=lunch_rest,
                        transit_info=t_desc,
                        cost=lunch_rest.cost,
                        open_time=lunch_rest.open_time,
                        rating=lunch_rest.rating,
                        warnings=warnings,
                    )
                )
                day_cost += lunch_rest.cost
                current_loc = lunch_rest
            else:
                day_items.append(
                    ScheduleItem(
                        time_slot=f"{lunch_start_s} - {lunch_end_s}",
                        category="meal",
                        title="午餐：周边自选特色简餐 / 美食街",
                        description="在附近街区或商圈享用顺路午餐，休息蓄力",
                        cost=35.0,
                    )
                )
                day_cost += 35.0

            # --- 下午行程 ---
            for s in afternoon_spots:
                dist = haversine_distance(current_loc.lng, current_loc.lat, s.lng, s.lat)
                t_min, t_desc = estimate_transit(dist)
                current_time_min += t_min
                day_dist += dist

                start_s = minutes_to_hhmm(current_time_min)
                end_min = current_time_min + s.duration_minutes
                end_s = minutes_to_hhmm(end_min)
                current_time_min = end_min

                warnings = []
                is_open, open_msg = is_time_in_range(start_s, s.open_time)
                if not is_open:
                    warnings.append(f"景点营业时间预警：{open_msg}")

                is_closed, closed_msg = is_spot_closed_on_date(s, curr_date)
                if is_closed:
                    warnings.append(closed_msg)

                day_items.append(
                    ScheduleItem(
                        time_slot=f"{start_s} - {end_s}",
                        category="spot",
                        title=f"游览【{s.name}】",
                        description=f"建议游玩 {s.duration_minutes} 分钟。门票参考: ¥{s.cost:.0f}/人。"
                        + (f" 标签: {', '.join(s.tags)}" if s.tags else ""),
                        location=s,
                        transit_info=t_desc,
                        cost=s.cost,
                        open_time=s.open_time,
                        rating=s.rating,
                        warnings=warnings,
                    )
                )
                day_cost += s.cost
                current_loc = s

            # --- 优化点 3：下午茶小吃档生理间隔动态抑制机制 ---
            # 只有在下午游览在 17:00 前完成时，才安排下午茶小吃档；
            # 若游玩已超过 17:00，则自动跳过下午茶，避免在 20 分钟内连吃两顿饭！
            if current_time_min <= 17 * 60:
                snack_rest = self._pick_best_restaurant(
                    current_loc, "snack", restaurants, used_restaurant_names, "16:00"
                )
                if snack_rest:
                    snack_start_min = max(current_time_min, 15 * 60 + 30)
                    snack_start_s = minutes_to_hhmm(snack_start_min)
                    snack_end_s = minutes_to_hhmm(snack_start_min + 40)
                    current_time_min = snack_start_min + 40

                    dist = haversine_distance(current_loc.lng, current_loc.lat, snack_rest.lng, snack_rest.lat)
                    _, t_desc = estimate_transit(dist, mode="walking")
                    day_dist += dist

                    day_items.append(
                        ScheduleItem(
                            time_slot=f"{snack_start_s} - {snack_end_s}",
                            category="snack",
                            title=f"特色小吃尝鲜：【{snack_rest.name}】",
                            description=f"品尝地道风味：{snack_rest.cuisine or '地方小吃'}，评分 {snack_rest.rating}，人均约 ¥{snack_rest.cost:.0f}。{snack_rest.recommend_reason}",
                            location=snack_rest,
                            transit_info=t_desc,
                            cost=snack_rest.cost,
                            open_time=snack_rest.open_time,
                            rating=snack_rest.rating,
                        )
                    )
                    day_cost += snack_rest.cost
                    current_loc = snack_rest

            # --- 晚餐 / 告别餐 (18:00 ~ 20:00) ---
            dinner_start_min = max(current_time_min, 18 * 60)
            dinner_start_s = minutes_to_hhmm(dinner_start_min)
            dinner_end_s = minutes_to_hhmm(dinner_start_min + 80)
            current_time_min = dinner_start_min + 80

            dinner_rest = self._pick_best_restaurant(
                current_loc, "night_market", restaurants, used_restaurant_names, dinner_start_s
            ) or self._pick_best_restaurant(
                current_loc, "meal", restaurants, used_restaurant_names, dinner_start_s
            )

            if dinner_rest:
                dist = haversine_distance(current_loc.lng, current_loc.lat, dinner_rest.lng, dinner_rest.lat)
                _, t_desc = estimate_transit(dist)
                day_dist += dist
                warnings = []
                is_open, open_msg = is_time_in_range(dinner_start_s, dinner_rest.open_time)
                if not is_open:
                    warnings.append(open_msg)

                is_market = dinner_rest.meal_type == "night_market"
                day_items.append(
                    ScheduleItem(
                        time_slot=f"{dinner_start_s} - {dinner_end_s}",
                        category="meal",
                        title=f"{'夜市逛吃' if is_market else ('返程前告别餐' if is_last_day else '晚餐')}：【{dinner_rest.name}】",
                        description=f"{dinner_rest.cuisine or '地道风味'}，高德评分 {dinner_rest.rating} 分，"
                        f"人均约 ¥{dinner_rest.cost:.0f}。{dinner_rest.recommend_reason}",
                        location=dinner_rest,
                        transit_info=t_desc,
                        cost=dinner_rest.cost,
                        open_time=dinner_rest.open_time,
                        rating=dinner_rest.rating,
                        warnings=warnings,
                    )
                )
                day_cost += dinner_rest.cost
                current_loc = dinner_rest

            # --- 优化点 4：最后一天返程接驳 vs 平日返回酒店 ---
            if is_last_day and self.departure_point:
                # 终点直接规划至车站/机场，留足缓冲时间
                dep_dist = haversine_distance(current_loc.lng, current_loc.lat, self.departure_point.lng, self.departure_point.lat)
                _, dep_desc = estimate_transit(dep_dist, mode="auto")
                day_dist += dep_dist
                dep_time_s = minutes_to_hhmm(current_time_min + 15)

                day_items.append(
                    ScheduleItem(
                        time_slot=dep_time_s,
                        category="transit",
                        title=f"前往【{self.departure_point.name}】准备返程",
                        description=f"行程结束，建议提前 60~90 分钟抵达车站/机场办理进站或值机手续，结束愉快的旅程。",
                        location=self.departure_point,
                        transit_info=dep_desc,
                    )
                )
            else:
                back_dist = haversine_distance(current_loc.lng, current_loc.lat, self.hotel.lng, self.hotel.lat)
                _, back_desc = estimate_transit(back_dist)
                day_dist += back_dist

                day_items.append(
                    ScheduleItem(
                        time_slot=minutes_to_hhmm(current_time_min + 20),
                        category="hotel",
                        title=f"返回【{self.hotel.name}】",
                        description="结束愉快充实的一天，回酒店休整充电",
                        location=self.hotel,
                        transit_info=back_desc,
                    )
                )

            # 主题提炼
            theme_keywords = [s.name for s in curr_spots]
            theme_text = f"{' + '.join(theme_keywords)} 深度游" if theme_keywords else "城市漫步休闲日"

            day_plans.append(
                DayItinerary(
                    day_number=day_num,
                    theme=theme_text,
                    items=day_items,
                    total_spots=len(curr_spots),
                    total_cost=day_cost,
                    estimated_distance_km=round(day_dist, 1),
                )
            )

        unassigned_names = [s.name for s in unassigned]
        summary_text = (
            f"为您精心规划了 {self.city} {self.total_days} 天行程，"
            f"共涵盖 {len(spots) - len(unassigned)} 个核心景点与精选地道美食。"
        )
        if calendar_warnings:
            summary_text += "\n" + " \n".join(calendar_warnings)

        return FullTripPlan(
            city=self.city,
            total_days=self.total_days,
            hotel=self.hotel,
            days=day_plans,
            unassigned_spots=unassigned_names,
            summary=summary_text,
        )
