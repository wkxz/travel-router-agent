"""P0 回归测试：排程引擎 app/engine。

运行：./.venv/Scripts/python.exe -m unittest tests.test_engine_p0 -v

引擎以别名 `route_engine` 挂载（早期测试沿用的名字）；engine 内部全部是相对导入，不受影响。
"""

import os
import sys
import types
import unittest

_ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "engine")
if "route_engine" not in sys.modules:
    _pkg = types.ModuleType("route_engine")
    _pkg.__path__ = [_ENGINE_DIR]
    sys.modules["route_engine"] = _pkg

from route_engine.models import LocationPoint, Spot, Restaurant  # noqa: E402
from route_engine.scheduler import TripScheduler  # noqa: E402
from route_engine.geo import check_open_window, parse_time_to_minutes  # noqa: E402
from route_engine.renderer import render_markdown  # noqa: E402

HOTEL = LocationPoint(name="南京新街口", lng=118.7844, lat=32.0416)
STATION = LocationPoint(name="南京南站", lng=118.7983, lat=31.9696)


def _spots(n: int, duration: int = 120):
    return [
        Spot(name=f"景点{i}", lng=118.78 + 0.005 * i, lat=32.04 + 0.003 * i, duration_minutes=duration)
        for i in range(n)
    ]


def _items(plan, category=None):
    return [it for d in plan.days for it in d.items if category is None or it.category == category]


def _minutes(slot: str, end: bool = False) -> int:
    part = slot.split("-")[-1 if end else 0].strip()
    return parse_time_to_minutes(part)


class TestPace(unittest.TestCase):
    """问题 1：节奏档位必须限制每日景点数，排不下的进入 unassigned。"""

    def test_pace_caps_spots_per_day(self):
        caps = {"relaxed": 2, "balanced": 3, "compact": 4}
        for pace, cap in caps.items():
            plan = TripScheduler("南京", 2, HOTEL, pace=pace).schedule(_spots(8, 90), [])
            per_day = [d.total_spots for d in plan.days]
            self.assertTrue(all(c <= cap for c in per_day), f"{pace}: {per_day} 超过上限 {cap}")
            self.assertEqual(sum(per_day) + len(plan.unassigned_spots), 8, f"{pace}: 景点数量对不上")

    def test_relaxed_has_fewer_spots_than_compact(self):
        relaxed = TripScheduler("南京", 2, HOTEL, pace="relaxed").schedule(_spots(8, 90), [])
        compact = TripScheduler("南京", 2, HOTEL, pace="compact").schedule(_spots(8, 90), [])
        self.assertLess(sum(d.total_spots for d in relaxed.days), sum(d.total_spots for d in compact.days))


class TestTimeFeasibility(unittest.TestCase):
    """问题 2：游览结束时间不能晚于闭园；白天景点不能拖到晚上。"""

    def test_open_window_checks_end_time(self):
        ok, _ = check_open_window(16 * 60 + 30, 18 * 60 + 30, "08:30-17:00")
        self.assertFalse(ok, "16:30 开始游 120 分钟，17:00 闭园，应判定为不可行")
        ok, _ = check_open_window(9 * 60, 11 * 60, "08:30-17:00")
        self.assertTrue(ok)
        ok, _ = check_open_window(23 * 60, 24 * 60 + 30, "17:00-02:00")
        self.assertTrue(ok, "跨零点营业")
        ok, _ = check_open_window(10 * 60, 11 * 60, "")
        self.assertIsNone(ok, "缺营业时间应返回 None（未知），而不是 True")

    def test_spot_never_ends_after_closing(self):
        spots = [Spot(name=f"早关门景点{i}", lng=118.78 + 0.004 * i, lat=32.04, duration_minutes=150,
                      open_time="09:00-16:00") for i in range(4)]
        plan = TripScheduler("南京", 1, HOTEL, pace="compact").schedule(spots, [])
        for it in _items(plan, "spot"):
            self.assertLessEqual(_minutes(it.time_slot, end=True), 16 * 60, it.time_slot)

    def test_daytime_spots_end_before_dinner(self):
        plan = TripScheduler("南京", 2, HOTEL, pace="compact").schedule(_spots(8, 120), [])
        for it in _items(plan, "spot"):
            self.assertLessEqual(_minutes(it.time_slot, end=True), 18 * 60 + 30, it.time_slot)

    def test_lunch_counts_transit_time(self):
        # 景点 13:00 左右结束，餐厅在约 1.9km 外（距离上限以内）：午餐开始时间必须晚于景点结束时间
        spot = Spot(name="景点", lng=118.7844, lat=32.0416, duration_minutes=230)
        far = Restaurant(name="远处餐厅", lng=118.8044, lat=32.0416, meal_type="meal")
        plan = TripScheduler("南京", 1, HOTEL).schedule([spot], [far])
        spot_end = _minutes(_items(plan, "spot")[0].time_slot, end=True)
        lunch = [it for it in _items(plan, "meal") if "午餐" in it.title][0]
        self.assertGreater(_minutes(lunch.time_slot), spot_end, "午餐开始时间应包含路程")


class TestNoFabricatedData(unittest.TestCase):
    """问题 3：缺评分/人均/营业时间时不能显示编造的数值。"""

    def test_missing_rating_and_cost_not_rendered_as_numbers(self):
        r = Restaurant(name="无数据小馆", lng=118.785, lat=32.042, meal_type="meal")
        s = Spot(name="无门票数据景点", lng=118.786, lat=32.043)
        plan = TripScheduler("南京", 1, HOTEL).schedule([s], [r])
        md = render_markdown(plan)
        self.assertNotIn("评分 4.5", md)
        self.assertNotIn("¥80", md)
        self.assertNotIn("门票参考: ¥0", md)
        self.assertIn("暂无", md)

    def test_lunch_placeholder_has_no_fake_cost(self):
        plan = TripScheduler("南京", 1, HOTEL).schedule(_spots(1), [])
        lunch = [it for it in _items(plan, "meal") if "午餐" in it.title][0]
        self.assertFalse(lunch.cost, "没有候选餐厅时不应编造人均")


class TestMealTypes(unittest.TestCase):
    """问题 4：正餐不能排成小吃店；没有候选时也要有晚餐占位。"""

    def test_dinner_is_not_a_snack(self):
        rs = [
            Restaurant(name="酥烧饼", lng=118.79, lat=32.042, meal_type="snack"),
            Restaurant(name="鸭血粉丝", lng=118.79, lat=32.043, meal_type="snack"),
            Restaurant(name="大牌档", lng=118.79, lat=32.044, meal_type="meal"),
        ]
        plan = TripScheduler("南京", 1, HOTEL).schedule(_spots(2), rs)
        for it in _items(plan, "meal"):
            self.assertNotIn("酥烧饼", it.title)
            self.assertNotIn("鸭血粉丝", it.title)

    def test_dinner_placeholder_when_no_candidates(self):
        plan = TripScheduler("南京", 1, HOTEL).schedule(_spots(2), [])
        self.assertTrue(any("晚餐" in it.title for it in _items(plan, "meal")))


class TestHotelDefault(unittest.TestCase):
    """问题 5：未提供住宿点时不能落到北京坐标。"""

    def test_missing_hotel_uses_spot_centroid(self):
        chengdu = [Spot(name="宽窄巷子", lng=104.0531, lat=30.6644), Spot(name="武侯祠", lng=104.0505, lat=30.6433)]
        plan = TripScheduler("成都", 1, None).schedule(chengdu, [])
        self.assertAlmostEqual(plan.hotel.lng, 104.05, delta=0.1)
        self.assertAlmostEqual(plan.hotel.lat, 30.65, delta=0.1)


class TestDepartureTime(unittest.TestCase):
    """问题 6：给了车次时间，最后一天所有安排都要保证提前到站。"""

    def test_activities_fit_before_train(self):
        rs = [Restaurant(name="大牌档", lng=118.79, lat=32.044, meal_type="meal"),
              Restaurant(name="鸭血粉丝", lng=118.79, lat=32.043, meal_type="meal")]
        plan = TripScheduler("南京", 1, HOTEL, departure_point=STATION, departure_time="15:00").schedule(_spots(3), rs)
        last = plan.days[-1].items[-1]
        self.assertEqual(last.category, "transit")
        arrive = _minutes(last.time_slot, end=True)
        self.assertLessEqual(arrive, 15 * 60 - 60, f"应至少提前 60 分钟到站，实际 {last.time_slot}")
        self.assertFalse(any("晚餐" in it.title or "告别餐" in it.title for it in plan.days[-1].items),
                         "15:00 的车不应安排晚餐")

    def test_early_lunch_on_departure_day(self):
        # 14:30 的车：11:30 开饭来得及，不应把午餐省掉
        rs = [Restaurant(name="大牌档", lng=118.79, lat=32.044, meal_type="meal")]
        plan = TripScheduler("南京", 1, HOTEL, departure_point=STATION, departure_time="14:30").schedule(_spots(1), rs)
        self.assertTrue(any("午餐" in it.title for it in plan.days[0].items))
        arrive = _minutes(plan.days[0].items[-1].time_slot, end=True)
        self.assertLessEqual(arrive, 14 * 60 + 30 - 60)

    def test_no_departure_no_farewell_label(self):
        plan = TripScheduler("南京", 1, HOTEL).schedule(_spots(1), [Restaurant(name="大牌档", lng=118.79, lat=32.044)])
        self.assertFalse(any("告别餐" in it.title for it in _items(plan)))


if __name__ == "__main__":
    unittest.main()
