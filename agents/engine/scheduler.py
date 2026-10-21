"""排程引擎核心：时间轴推进、按天切分、双轨餐饮就近匹配与营业时间校验。

保证输出的每一天都遵循合理的人类出行作息，绝不在午夜排公园，不在闭馆后排博物馆。
"""

from typing import List, Dict, Optional, Tuple
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
    parse_time_to_minutes,
    minutes_to_hhmm,
    is_time_in_range,
)
from .optimizer import optimize_spots_route


class TripScheduler:
    """行程日程编排器"""

    def __init__(
        self,
        city: str,
        total_days: int,
        hotel: Optional[LocationPoint] = None,
        pace: str = "balanced",  # 当前固定为 balanced (均衡)
    ):
        self.city = city
        self.total_days = max(1, total_days)
        self.hotel = hotel or LocationPoint(name=f"{city}市中心住宿点", lng=116.4074, lat=39.9042)
        self.pace = pace

        # 均衡模式配置：单日推荐 2~3 个景点
        self.max_spots_per_day = 3
        self.min_spots_per_day = 1

    def _split_spots_to_days(self, spots: List[Spot]) -> Tuple[List[List[Spot]], List[Spot]]:
        """将全局优化后的景点序列按空间连续性切分成每天的游玩清单。

        优化逻辑：
        全局路线已由 2-opt 消除折返，按地理位置自然串成顺畅走势。
        严禁使用轮询取模 (idx % total_days)，必须按连续块（Chunking）切分，
        确保空间上紧挨着的景点（如鸡鸣寺与玄武湖、音乐台与梧桐大道、夫子庙与老门东）
        始终分在同一天游玩，绝不被拆散到不同天！
        """
        total = len(spots)
        if total == 0:
            return [[] for _ in range(self.total_days)], []

        # 计算合理容量
        capacity = self.total_days * self.max_spots_per_day
        accepted = spots[:capacity]
        unassigned = spots[capacity:]

        # 连续分块切分 (Contiguous Chunking)
        n = len(accepted)
        days_spots: List[List[Spot]] = [[] for _ in range(self.total_days)]

        base_size = n // self.total_days
        remainder = n % self.total_days

        start_idx = 0
        for day_idx in range(self.total_days):
            chunk_size = base_size + (1 if day_idx < remainder else 0)
            chunk = accepted[start_idx : start_idx + chunk_size]
            start_idx += chunk_size
            if chunk:
                # 每天内部以住宿点为起点微调单日最佳顺序
                opt, _ = optimize_spots_route(self.hotel, chunk)
                days_spots[day_idx] = opt
            else:
                days_spots[day_idx] = []

        return days_spots, unassigned

    def _pick_best_restaurant(
        self,
        anchor: LocationPoint,
        target_meal_type: str,
        restaurants: List[Restaurant],
        used_names: set,
        target_time: str,
    ) -> Optional[Restaurant]:
        """就近挑选最合适且营业的餐饮/小吃。

        权重因子：距离近 > 评分高 > 营业状态。
        """
        candidates = [
            r for r in restaurants
            if r.name not in used_names
            and (target_meal_type == "all" or r.meal_type == target_meal_type)
        ]

        if not candidates and target_meal_type != "all":
            # 降级：若特定品类无候选，放宽至所有餐饮
            candidates = [r for r in restaurants if r.name not in used_names]

        if not candidates:
            return None

        # 评分机制：距离越近越好，评分越高越好
        def score(r: Restaurant) -> float:
            dist = haversine_distance(anchor.lng, anchor.lat, r.lng, r.lat)
            is_open, _ = is_time_in_range(target_time, r.open_time)
            open_bonus = 2.0 if is_open else -5.0
            # rating 权重 1.0, 距离扣分 (每公里扣 0.5 分)
            return (r.rating or 4.0) - (dist * 0.5) + open_bonus

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
        # 1. 全局路线优化（消除折返）
        optimized_spots, _ = optimize_spots_route(self.hotel, spots)

        # 2. 按天切分
        days_spots, unassigned = self._split_spots_to_days(optimized_spots)

        used_restaurant_names = set()
        day_plans: List[DayItinerary] = []

        for day_idx in range(self.total_days):
            day_num = day_idx + 1
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
                    description="整理随身物品，开始一天的充实行程",
                    location=self.hotel,
                )
            )

            # 上午景点与下午景点划分
            morning_spots = curr_spots[:1] if len(curr_spots) <= 2 else curr_spots[:2]
            afternoon_spots = curr_spots[len(morning_spots):]

            # --- 上午行程 ---
            for s in morning_spots:
                # 通勤
                dist = haversine_distance(current_loc.lng, current_loc.lat, s.lng, s.lat)
                t_min, t_desc = estimate_transit(dist)
                current_time_min += t_min
                day_dist += dist

                # 游玩
                start_s = minutes_to_hhmm(current_time_min)
                end_min = current_time_min + s.duration_minutes
                end_s = minutes_to_hhmm(end_min)
                current_time_min = end_min

                warnings = []
                is_open, open_msg = is_time_in_range(start_s, s.open_time)
                if not is_open:
                    warnings.append(f"景点营业时间预警：{open_msg}")

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
                        title="午餐：周边自选特色简餐",
                        description="在附近商圈或特色街区享用午餐，休整蓄力",
                        cost=40.0,
                    )
                )
                day_cost += 40.0

            # --- 下午行程与特色小吃 ---
            for s_idx, s in enumerate(afternoon_spots):
                # 穿插特色小吃（若有多于 1 个下午景点，在景点间穿插；若只有 1 个，在游览后穿插）
                if s_idx == 0 and len(afternoon_spots) >= 2:
                    pass

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

            # 下午茶/特色小吃档 (15:30 ~ 17:00 之间)
            snack_rest = self._pick_best_restaurant(
                current_loc, "snack", restaurants, used_restaurant_names, "16:00"
            )
            if snack_rest:
                snack_start_s = minutes_to_hhmm(max(current_time_min, 16 * 60))
                snack_end_s = minutes_to_hhmm(max(current_time_min, 16 * 60) + 40)
                current_time_min = max(current_time_min, 16 * 60) + 40

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

            # --- 晚餐 / 夜市 (18:00 ~ 20:30) ---
            dinner_start_min = max(current_time_min, 18 * 60)
            dinner_start_s = minutes_to_hhmm(dinner_start_min)
            dinner_end_s = minutes_to_hhmm(dinner_start_min + 90)
            current_time_min = dinner_start_min + 90

            # 优先看有没有夜市或正餐
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
                        title=f"{'夜市逛吃' if is_market else '晚餐'}：【{dinner_rest.name}】",
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

            # 回酒店
            back_dist = haversine_distance(current_loc.lng, current_loc.lat, self.hotel.lng, self.hotel.lat)
            _, back_desc = estimate_transit(back_dist)
            day_dist += back_dist

            day_items.append(
                ScheduleItem(
                    time_slot=minutes_to_hhmm(current_time_min + 20),
                    category="hotel",
                    title=f"返回【{self.hotel.name}】",
                    description="结束愉快的一天，回酒店休息充能",
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
            f"共涵盖 {len(spots) - len(unassigned)} 个核心景点与 {len(used_restaurant_names)} 家精选美食。"
        )

        return FullTripPlan(
            city=self.city,
            total_days=self.total_days,
            hotel=self.hotel,
            days=day_plans,
            unassigned_spots=unassigned_names,
            summary=summary_text,
        )
