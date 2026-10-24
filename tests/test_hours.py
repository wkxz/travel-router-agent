"""回归测试：按日期取营业时间（2026-10-24 武汉实测），返程日告别餐提前（同日大理实测）。"""

import unittest
from datetime import date

from tests.fake_amap import FakeAmap  # noqa: F401  挂载 app 别名
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from app.engine.geo import find_open_slot
from app.engine.hours import CLOSED_MARK, hours_on_date
from app.engine.models import DayItinerary, FullTripPlan, ScheduleItem
from app.engine.validator import validate_plan

TUE, FRI, SAT = date(2026, 10, 27), date(2026, 10, 30), date(2026, 10, 31)
WUHAN_SHOP = "周一至周四 06:00-15:00；周五至周日 06:00-20:00 2026-10-03至2026-10-07 05:30-20:00"  # 高德原文


class TestHoursOnDate(unittest.TestCase):
    def test_weekday_clauses(self):
        self.assertEqual(hours_on_date(WUHAN_SHOP, TUE), "06:00-15:00；")
        self.assertEqual(hours_on_date(WUHAN_SHOP, FRI), "06:00-20:00")

    def test_special_date_range_overrides(self):
        self.assertEqual(hours_on_date(WUHAN_SHOP, date(2026, 10, 5)), "05:30-20:00")  # 国庆特殊时段
        closed = "周一至周日 11:00-21:00 2026-02-16至2026-02-18 周一至周日 全天关闭"
        self.assertEqual(hours_on_date(closed, date(2026, 2, 17)), CLOSED_MARK)
        self.assertEqual(hours_on_date(closed, TUE), "11:00-21:00")

    def test_day_not_listed_is_closed(self):
        self.assertEqual(hours_on_date("周二至周日 09:00-17:00", date(2026, 10, 26)), CLOSED_MARK)
        self.assertEqual(hours_on_date("09:00-17:00 周一闭馆", date(2026, 10, 26)), CLOSED_MARK)
        self.assertEqual(hours_on_date("09:00-17:00 周一闭馆", TUE), "09:00-17:00")

    def test_weekend_and_workday_words(self):
        text = "周六、周日 10:00-22:00；工作日 17:00-22:00"
        self.assertEqual(hours_on_date(text, TUE), "17:00-22:00")
        self.assertIn("10:00-22:00", hours_on_date(text, SAT))

    def test_plain_text_unchanged(self):
        for text in ("08:30-17:00", "24小时营业", "周一闭馆", ""):
            self.assertEqual(hours_on_date(text, TUE), text)
        self.assertEqual(hours_on_date(WUHAN_SHOP, None), WUHAN_SHOP)

    def test_closed_mark_is_not_open(self):
        self.assertIs(find_open_slot(10 * 60, 60, CLOSED_MARK)[0], False)


HOTEL = LocationPoint(name="住处", lng=114.30, lat=30.58)


class TestSchedulerUsesDayHours(unittest.TestCase):
    def test_dinner_not_at_shop_closed_that_weekday(self):
        spot = Spot(name="景点", lng=114.301, lat=30.581, duration_minutes=120)
        shops = [
            Restaurant(name="早点铺", lng=114.302, lat=30.582, meal_type="meal", rating=4.9, open_time=WUHAN_SHOP),
            Restaurant(name="午餐店", lng=114.303, lat=30.581, meal_type="meal", rating=5.0, open_time="10:00-14:00"),
            Restaurant(name="晚餐店", lng=114.302, lat=30.580, meal_type="meal", rating=4.0, open_time="17:00-21:00"),
        ]
        plan = TripScheduler("某市", 1, HOTEL, start_date=TUE.isoformat()).schedule([spot], shops)
        dinner = [it for it in plan.days[0].items if "晚餐" in it.title][0]
        lunch = [it for it in plan.days[0].items if "午餐" in it.title][0]
        self.assertIn("午餐店", lunch.title)  # 早点铺留给晚餐竞争，测试才有意义
        self.assertNotIn("早点铺", dinner.title)  # 周二 15:00 关门

    def test_spot_open_only_on_weekend(self):
        spot = Spot(name="周末市集", lng=114.301, lat=30.581, duration_minutes=60,
                    open_time="周六、周日 09:00-17:00")
        plan = TripScheduler("某市", 1, HOTEL, start_date=TUE.isoformat()).schedule([spot], [])
        self.assertFalse([it for it in plan.days[0].items if it.category == "spot"])

    def test_validator_checks_day_hours(self):
        shop = Restaurant(name="早点铺", lng=114.302, lat=30.582, meal_type="meal", open_time=WUHAN_SHOP)
        day = DayItinerary(day_number=1, theme="", date=TUE.isoformat(), items=[
            ScheduleItem(time_slot="18:00 - 19:20", category="meal", title="晚餐：【早点铺】",
                         description="", location=shop, open_time=WUHAN_SHOP),
        ])
        plan = FullTripPlan(city="某市", total_days=1, hotel=HOTEL, days=[day])
        errors = [i for i in validate_plan(plan) if i.level == "error"]
        self.assertTrue(any("营业时间" in i.message for i in errors), errors)


class TestFarewellDinnerMovesEarlier(unittest.TestCase):
    def test_dinner_before_evening_train(self):
        """20:00 的火车、车站约 50 分钟车程（同大理实测）：18:00 吃完来不及，下午有空，告别餐应提前而不是省略。"""
        station = LocationPoint(name="火车站", lng=114.48, lat=30.58)
        spot = Spot(name="上午景点", lng=114.301, lat=30.581, duration_minutes=180)
        shops = [Restaurant(name=f"餐厅{i}", lng=114.302 + i * 0.001, lat=30.582, meal_type="meal",
                            open_time="10:00-21:00") for i in range(3)]
        plan = TripScheduler("某市", 1, HOTEL, departure_point=station, departure_time="20:00",
                             start_date=TUE.isoformat()).schedule([spot], shops)
        titles = [it.title for it in plan.days[0].items]
        farewell = [it for it in plan.days[0].items if "告别餐" in it.title]
        self.assertTrue(farewell and "周边自选" not in farewell[0].title, titles)
        self.assertGreaterEqual(int(farewell[0].time_slot[:2]), 16, farewell[0].time_slot)



    def test_dinner_recomputed_after_returning_to_hotel(self):
        """大理实测：午饭在去车站方向，按午饭地点算出 17:00 开饭；随后回住处，离车站变远，17:00 吃完又来不及。"""
        station = LocationPoint(name="火车站", lng=114.48, lat=30.58)
        spot = Spot(name="远处景点", lng=114.33, lat=30.62, duration_minutes=120)  # 离住处约 5km，下午会回住处休整
        shops = [
            Restaurant(name="去车站路上的店", lng=114.36, lat=30.61, meal_type="meal", rating=5.0, open_time="10:00-21:00"),
            Restaurant(name="景点旁餐厅", lng=114.331, lat=30.621, meal_type="meal", rating=4.0, open_time="10:00-21:00"),
            Restaurant(name="住处旁餐厅", lng=114.301, lat=30.581, meal_type="meal", rating=4.0, open_time="10:00-21:00"),
            Restaurant(name="住处旁餐厅2", lng=114.302, lat=30.580, meal_type="meal", rating=4.0, open_time="10:00-21:00"),
        ]
        plan = TripScheduler("某市", 1, HOTEL, departure_point=station, departure_time="20:00",
                             start_date=TUE.isoformat()).schedule([spot], shops)
        items = plan.days[0].items
        titles = [it.title for it in items]
        self.assertTrue(any("告别餐" in t and "周边自选" not in t for t in titles), titles)
        lunch = [t for t in titles if "午餐" in t][0]
        self.assertNotIn("去车站路上的店", lunch)  # 下午要回住处，午饭不往车站方向挑


class TestMealVenueType(unittest.TestCase):
    def _dinner(self, shops):
        spot = Spot(name="景点", lng=114.301, lat=30.581, duration_minutes=120)
        plan = TripScheduler("某市", 1, HOTEL, start_date=TUE.isoformat()).schedule([spot], shops)
        return [it.title for it in plan.days[0].items if it.category == "meal"]

    def test_bakery_and_staff_canteen_not_used_for_meals(self):
        shops = [
            Restaurant(name="鲜花饼屋", lng=114.302, lat=30.582, meal_type="meal", rating=4.9,
                       category="餐饮服务;糕饼店;糕饼店"),
            Restaurant(name="某疗养院职工餐厅", lng=114.302, lat=30.581, meal_type="meal", rating=4.9,
                       category="餐饮服务;中餐厅;中餐厅"),
            Restaurant(name="普通饭馆", lng=114.303, lat=30.582, meal_type="meal", rating=4.0,
                       category="餐饮服务;中餐厅;中餐厅"),
        ]
        meals = self._dinner(shops)
        self.assertFalse(any("鲜花饼屋" in t or "职工餐厅" in t for t in meals), meals)

    def test_named_must_eat_still_allowed(self):
        shops = [Restaurant(name="鲜花饼屋", lng=114.302, lat=30.582, meal_type="meal", rating=4.5,
                            category="餐饮服务;糕饼店;糕饼店", dish_key="鲜花饼")]
        self.assertTrue(any("鲜花饼屋" in t for t in self._dinner(shops)))


if __name__ == "__main__":
    unittest.main()
