"""空档处理测试：时间够就顺路加游，不够就给出建议；午餐/小吃开始时间更有弹性。

运行：./.venv/Scripts/python.exe -m unittest tests.test_engine_gaps -v
"""

import unittest

from tests.test_engine_p0 import _items, _minutes  # 同时完成 route_engine 别名挂载
from tests.test_engine_p1 import CHUNXI, PANDA, WUHOU
from route_engine.models import Spot, Restaurant
from route_engine.scheduler import TripScheduler

NEAR_PANDA_MEAL = Restaurant(name="客家小院柴火鸡", lng=104.1440, lat=30.7260, meal_type="meal")
NEAR_PANDA_PARK = dict(name="北湖公园", lng=104.1500, lat=30.7250, duration_minutes=90, open_time="06:00-22:00")


class TestLunchFlex(unittest.TestCase):
    def test_lunch_soon_after_morning_ends(self):
        # 武侯祠 09:16-11:16（成都实测场景），午餐不应等到 12:00
        rs = [Restaurant(name="钦善斋", lng=104.0510, lat=30.6440, meal_type="meal")]
        plan = TripScheduler("成都", 1, CHUNXI).schedule([Spot(**WUHOU, duration_minutes=120)], rs)
        spot_end = _minutes(_items(plan, "spot")[0].time_slot, end=True)
        lunch = [it for it in _items(plan, "meal") if "午餐" in it.title][0]
        self.assertLessEqual(_minutes(lunch.time_slot) - spot_end, 25, lunch.time_slot)


class TestGapFilling(unittest.TestCase):
    def test_backup_spot_fills_afternoon_gap(self):
        # 熊猫基地上午结束，下午大段空档：附近有备选景点就顺路加游
        plan = TripScheduler("成都", 1, CHUNXI).schedule(
            [Spot(**PANDA, best_period="morning", duration_minutes=240)], [NEAR_PANDA_MEAL],
            backup_spots=[Spot(**NEAR_PANDA_PARK)],
        )
        spots = _items(plan, "spot")
        self.assertTrue(any("北湖公园" in it.title for it in spots), [it.title for it in spots])
        park = [it for it in spots if "北湖公园" in it.title][0]
        self.assertTrue(any("空档" in w for w in park.warnings), park.warnings)
        self.assertLessEqual(_minutes(park.time_slot, end=True), 18 * 60)

    def test_far_backup_not_used(self):
        far_park = dict(NEAR_PANDA_PARK, name="远处公园", lng=104.0000, lat=30.6000)
        plan = TripScheduler("成都", 1, CHUNXI).schedule(
            [Spot(**PANDA, best_period="morning", duration_minutes=240)], [NEAR_PANDA_MEAL],
            backup_spots=[Spot(**far_park)],
        )
        self.assertFalse(any("远处公园" in it.title for it in _items(plan, "spot")))

    def test_gap_without_backup_gives_advice(self):
        plan = TripScheduler("成都", 1, CHUNXI).schedule(
            [Spot(**PANDA, best_period="morning", duration_minutes=240)], [NEAR_PANDA_MEAL])
        free = _items(plan, "free")
        self.assertTrue(free, "大段空档应给出自由活动建议")
        self.assertIn("空档", free[0].title)
        self.assertTrue(free[0].description)

    def test_relaxed_pace_does_not_fill(self):
        plan = TripScheduler("成都", 1, CHUNXI, pace="relaxed").schedule(
            [Spot(**PANDA, best_period="morning", duration_minutes=240)], [NEAR_PANDA_MEAL],
            backup_spots=[Spot(**NEAR_PANDA_PARK)],
        )
        self.assertFalse(any("北湖公园" in it.title for it in _items(plan, "spot")))
        self.assertTrue(_items(plan, "free"))

    def test_fill_respects_daily_cap(self):
        # 每个点 90 分钟、各算 1 个名额：短景点空档补点按时长折算，见 test_cap_by_duration
        main = [Spot(name=f"景点{i}", lng=104.080 + 0.003 * i, lat=30.656, duration_minutes=90) for i in range(3)]
        plan = TripScheduler("成都", 1, CHUNXI, pace="balanced").schedule(
            main, [], backup_spots=[Spot(name="备选", lng=104.085, lat=30.657, duration_minutes=90)])
        self.assertLessEqual(plan.days[0].total_spots, 3)

    def test_unused_backups_not_reported_as_unassigned(self):
        plan = TripScheduler("成都", 1, CHUNXI).schedule(
            [Spot(**WUHOU)], [], backup_spots=[Spot(name="远处备选", lng=104.30, lat=30.90)])
        self.assertFalse(any("远处备选" in u for u in plan.unassigned_spots))

    def test_packed_out_spot_used_in_later_gap_and_removed_from_unassigned(self):
        # 第 1 天景点数超限被挤出的点，若后面某天有空档且顺路，应补进去并从「未安排」里移除
        crowded = [Spot(name=f"市区{i}", lng=104.080 + 0.002 * i, lat=30.656, duration_minutes=60) for i in range(4)]
        plan = TripScheduler("成都", 1, CHUNXI, pace="compact").schedule(crowded, [])
        names = [it.title for it in _items(plan, "spot")]
        for u in plan.unassigned_spots:
            self.assertFalse(any(u.split("（")[0] in n for n in names), (u, names))


class TestRealRunRegressions(unittest.TestCase):
    """哈尔滨 / 厦门实测中发现的问题（只用通用规则，不涉及城市专属逻辑）。"""

    def test_evening_spot_counts_toward_cap_when_filling(self):
        # 每个点 90 分钟、各算 1 个名额：短景点空档补点按时长折算，见 test_cap_by_duration
        # 均衡档白天 ≤3、夜游另加 1 个（2026-10-29 起夜游不占白天名额，见 test_day_cohesion）：
        # 白天 3 个 + 夜游 1 个时，不应再用空档补第 5 个
        spots = [Spot(name="白天A", lng=104.080, lat=30.656, duration_minutes=90),
                 Spot(name="白天B", lng=104.083, lat=30.656, duration_minutes=90),
                 Spot(name="白天E", lng=104.081, lat=30.655, duration_minutes=90),
                 Spot(name="夜游C", lng=104.085, lat=30.657, duration_minutes=90, best_period="evening")]
        plan = TripScheduler("任意城市", 1, CHUNXI).schedule(
            spots, [], backup_spots=[Spot(name="备选D", lng=104.082, lat=30.657, duration_minutes=90)])
        self.assertLessEqual(plan.days[0].total_spots, 4, [it.title for it in _items(plan, "spot")])

    def test_two_evening_spots_take_one_daytime_slot(self):
        # 每个点 90 分钟、各算 1 个名额：短景点空档补点按时长折算，见 test_cap_by_duration
        # 夜游只有 1 个额外名额：白天 2 个 + 夜游 2 个已满 4 个，不再补白天景点
        spots = [Spot(name="白天A", lng=104.080, lat=30.656, duration_minutes=90),
                 Spot(name="白天B", lng=104.083, lat=30.656, duration_minutes=90),
                 Spot(name="夜游C", lng=104.085, lat=30.657, duration_minutes=90, best_period="evening"),
                 Spot(name="夜游F", lng=104.086, lat=30.657, duration_minutes=90, best_period="evening")]
        plan = TripScheduler("任意城市", 1, CHUNXI).schedule(
            spots, [], backup_spots=[Spot(name="备选D", lng=104.082, lat=30.657, duration_minutes=90)])
        self.assertNotIn("游览【备选D】", [it.title for it in _items(plan, "spot")])

    def test_gap_between_lunch_and_snack_is_visible(self):
        rs = [Restaurant(name="午餐店", lng=104.0816, lat=30.6559, meal_type="meal"),
              Restaurant(name="小吃店", lng=104.0817, lat=30.6559, meal_type="snack")]
        plan = TripScheduler("任意城市", 1, CHUNXI).schedule(
            [Spot(name="上午景点", lng=104.0815, lat=30.6560, duration_minutes=150)], rs)
        items = plan.days[0].items
        lunch_idx = next(i for i, it in enumerate(items) if "午餐" in it.title)
        snack_idx = next(i for i, it in enumerate(items) if it.category == "snack")
        gap = _minutes(items[snack_idx].time_slot) - _minutes(items[lunch_idx].time_slot, end=True)
        if gap >= 30:
            self.assertTrue(any(it.category == "free" for it in items[lunch_idx:snack_idx]),
                            "午餐和小吃之间超过 30 分钟的空档应给出建议")

    def test_fast_food_avoided_for_dinner(self):
        rs = [Restaurant(name="某快餐(景区店)", lng=104.0816, lat=30.6559, meal_type="meal",
                         category="餐饮服务;快餐厅;快餐", rating=4.5),
              Restaurant(name="本地菜馆", lng=104.0840, lat=30.6570, meal_type="meal",
                         category="餐饮服务;中餐厅;中餐厅", rating=4.3),
              Restaurant(name="本地菜馆2", lng=104.0845, lat=30.6575, meal_type="meal",
                         category="餐饮服务;中餐厅;中餐厅", rating=4.3)]
        plan = TripScheduler("任意城市", 1, CHUNXI).schedule(
            [Spot(name="景点", lng=104.0815, lat=30.6560, duration_minutes=60)], rs)
        self.assertFalse(any("快餐" in it.title for it in _items(plan, "meal")))


class TestRound3Regressions(unittest.TestCase):
    """厦门 / 阳朔实测发现的通用问题。"""

    def test_nearby_spots_stay_together_when_spots_le_days(self):
        # 厦门实测：3 个景点 3 天，相距约 3km 的两个点被硬拆到两天。景点少于天数时也要按距离归组
        from route_engine.geo import cluster_spots_spatially
        far = Spot(name="海岛", lng=118.0670, lat=24.4447, duration_minutes=300)
        a = Spot(name="寺庙", lng=118.0971, lat=24.4426, duration_minutes=120)
        b = Spot(name="文创村", lng=118.1251, lat=24.4252, duration_minutes=120)
        hotel = Spot(name="住处", lng=118.0819, lat=24.4541)
        clusters = cluster_spots_spatially(hotel, [far, a, b], 3, max_cluster_minutes=420)
        self.assertEqual(len(clusters), 3)
        together = [c for c in clusters if a in c]
        self.assertIn(b, together[0], [[s.name for s in c] for c in clusters])

    def test_consecutive_free_rows_are_merged(self):
        plan = TripScheduler("任意城市", 1, CHUNXI, pace="relaxed").schedule(
            [Spot(name="上午景点", lng=104.0815, lat=30.6560, duration_minutes=120)],
            [Restaurant(name="午餐店", lng=104.0816, lat=30.6559, meal_type="meal")])
        items = plan.days[0].items
        for prev, cur in zip(items, items[1:]):
            self.assertFalse(prev.category == "free" and cur.category == "free",
                             [f"{it.time_slot} {it.title}" for it in items])

    def test_long_drive_uses_faster_speed(self):
        from route_engine.geo import estimate_transit
        minutes, _ = estimate_transit(24.2)  # 直线 24km ≈ 道路 31.5km，城市快速路约 1 小时
        self.assertLessEqual(minutes, 65)
        short, _ = estimate_transit(3.0)
        self.assertGreaterEqual(short, 12)  # 市区短途仍按拥堵路况


class TestGapOnDepartureDay(unittest.TestCase):
    def test_gap_before_train_suggests_station(self):
        from route_engine.models import LocationPoint
        station = LocationPoint(name="成都东站", lng=104.1414, lat=30.6305)
        plan = TripScheduler("成都", 1, CHUNXI, departure_point=station, departure_time="17:00").schedule(
            [Spot(name="太古里", lng=104.0830, lat=30.6550, duration_minutes=60)], [])
        last = plan.days[0].items[-1]
        self.assertEqual(last.category, "transit")
        self.assertLessEqual(_minutes(last.time_slot, end=True), 16 * 60)
        free = _items(plan, "free")
        self.assertTrue(any("车站" in f.description or "提前" in f.description for f in free),
                        [f.description for f in free])


class TestOpeningHoursDriven(unittest.TestCase):
    """2026-10-30 随机压力测试发现：没标时段的 18:00 才开的景点按白天排必然放不下，被舍弃，晚上却整段空着；
    舍弃原因还写成「景点数已排满」。"""

    def test_late_opening_spot_goes_to_evening(self):
        spots = [Spot(name="白天景点", lng=104.080, lat=30.656, duration_minutes=120),
                 Spot(name="夜市街区", lng=104.083, lat=30.657, duration_minutes=90, open_time="18:00-23:00")]
        plan = TripScheduler("任意城市", 1, CHUNXI, start_date="2026-11-24").schedule(spots, [])
        self.assertEqual(plan.unassigned_spots, [])
        night = next(it for it in _items(plan, "spot") if "夜市街区" in it.title)
        self.assertGreaterEqual(_minutes(night.time_slot), 18 * 60)

    def test_hours_conflict_reason_is_specific(self):
        spots = [Spot(name="短开放", lng=104.080, lat=30.656, duration_minutes=240, open_time="10:00-12:00")]
        plan = TripScheduler("任意城市", 1, CHUNXI, start_date="2026-11-24").schedule(spots, [])
        self.assertTrue(any("营业时间" in u for u in plan.unassigned_spots), plan.unassigned_spots)


    def test_long_morning_spot_warns_about_lunch(self):
        spots = [Spot(name="大景区", lng=104.080, lat=30.656, duration_minutes=300, open_time="08:30-17:30")]
        plan = TripScheduler("任意城市", 1, CHUNXI, start_date="2026-11-24").schedule(spots, [])
        item = _items(plan, "spot")[0]
        self.assertTrue(any("午饭" in w for w in item.warnings), item.warnings)


class TestLimitedMobility(unittest.TestCase):
    def test_walk_limit_and_pace(self):
        # 两点道路距离约 1.5km：平常步行，行动不便时打车；紧凑节奏降为均衡
        a = Spot(name="甲", lng=104.080, lat=30.656, duration_minutes=60)
        b = Spot(name="乙", lng=104.0915, lat=30.656, duration_minutes=60)
        normal = TripScheduler("任意城市", 1, CHUNXI, start_date="2026-11-24", pace="compact")
        limited = TripScheduler("任意城市", 1, CHUNXI, start_date="2026-11-24", pace="compact", mobility="limited")
        self.assertIn("步行", normal.travel.leg(a, b).desc)
        self.assertIn("打车", limited.travel.leg(a, b).desc)
        self.assertEqual((normal.pace, limited.pace), ("compact", "balanced"))


if __name__ == "__main__":
    unittest.main()
