"""校验器 + 审核中实测发现的 4 个 bug 的回归测试。

运行：./.venv/Scripts/python.exe -m unittest tests.test_engine_validator -v
"""

import unittest
from datetime import date

from tests.test_engine_p0 import HOTEL, STATION, _items, _minutes  # 同时完成 route_engine 别名挂载
from route_engine.models import (
    LocationPoint, Spot, Restaurant, ScheduleItem, DayItinerary, FullTripPlan,
)
from route_engine.scheduler import TripScheduler, STATION_BUFFER
from route_engine.calendar_pack import parse_closed_days, is_spot_closed_on_date
from route_engine.geo import haversine_distance, estimate_transit
from route_engine.validator import validate_plan

XUANWU = dict(name="玄武湖", lng=118.7966, lat=32.0700)
TUESDAY = "2026-10-27"
MONDAY = "2026-10-26"


class TestEarlyDeparture(unittest.TestCase):
    """bug 1：返程早于 09:00 时曾输出「留出约 -75 分钟进站」。"""

    def test_early_train_leaves_hotel_in_time(self):
        plan = TripScheduler("南京", 1, HOTEL, departure_point=STATION, departure_time="08:30",
                             start_date=TUESDAY).schedule([Spot(**XUANWU)], [])
        transit = _items(plan, "transit")[-1]
        self.assertGreaterEqual(8 * 60 + 30 - _minutes(transit.time_slot, end=True), STATION_BUFFER, transit.time_slot)
        self.assertLess(_minutes(plan.days[0].items[0].time_slot), 9 * 60)
        self.assertEqual(_items(plan, "spot"), [])

    def test_early_train_explains_why_spot_dropped(self):
        plan = TripScheduler("南京", 1, HOTEL, departure_point=STATION, departure_time="08:30",
                             start_date=TUESDAY).schedule([Spot(**XUANWU)], [])
        self.assertIn("时间不足", plan.unassigned_spots[0])
        self.assertNotIn("已排满", plan.unassigned_spots[0])

    def test_early_train_does_not_claim_lunch_skipped(self):
        # 饭点本就在发车之后，不应提示「为赶车省略了午餐」
        plan = TripScheduler("南京", 1, HOTEL, departure_point=STATION, departure_time="08:30",
                             start_date=TUESDAY).schedule([Spot(**XUANWU)], [])
        warnings = _items(plan, "transit")[-1].warnings
        self.assertFalse(any("午餐" in w for w in warnings), warnings)

    def test_impossible_departure_raises(self):
        with self.assertRaises(ValueError):
            TripScheduler("南京", 1, HOTEL, departure_point=STATION, departure_time="00:30",
                          start_date=TUESDAY).schedule([Spot(**XUANWU)], [])


class TestClosedRestaurant(unittest.TestCase):
    """bug 2：附近唯一的餐厅不营业时曾被硬选为午餐。"""

    def test_closed_restaurant_not_picked(self):
        rs = [Restaurant(name="只开早上的店", lng=118.7966, lat=32.0705, meal_type="meal",
                         open_time="06:00-09:30", rating=4.8)]
        plan = TripScheduler("南京", 1, HOTEL, start_date=TUESDAY).schedule([Spot(**XUANWU)], rs)
        titles = [it.title for it in _items(plan)]
        self.assertFalse(any("只开早上的店" in t for t in titles), titles)
        self.assertTrue(any("午餐" in t and "自选" in t for t in titles), titles)

    def test_open_restaurant_still_picked(self):
        rs = [Restaurant(name="全天营业的店", lng=118.7966, lat=32.0705, meal_type="meal", open_time="10:00-21:00")]
        plan = TripScheduler("南京", 1, HOTEL, start_date=TUESDAY).schedule([Spot(**XUANWU)], rs)
        self.assertTrue(any("全天营业的店" in it.title for it in _items(plan, "meal")))


class TestReturnToHotel(unittest.TestCase):
    """bug 3：回酒店时间曾写死为「上一项结束 + 20 分钟」。"""

    def test_return_time_uses_transit_estimate(self):
        far = Spot(name="远处景点", lng=119.03, lat=32.10, duration_minutes=120)
        plan = TripScheduler("南京", 1, HOTEL, start_date=TUESDAY).schedule([far], [])
        items = plan.days[0].items
        # 远处景点游完、离晚餐还早：先回住处休整（这一段就是从远处景点回酒店）
        idx = next(i for i, it in enumerate(items) if "休整" in it.title)
        back, before = items[idx], items[idx - 1]
        t_min, _ = estimate_transit(haversine_distance(far.lng, far.lat, HOTEL.lng, HOTEL.lat))
        self.assertGreater(t_min, 20)
        self.assertEqual(_minutes(back.time_slot) - _minutes(before.time_slot, end=True), t_min)


class TestClosedDaysParsing(unittest.TestCase):
    """bug 4：「周二至周日」这类写法曾被忽略，周一照排。"""

    def test_parse(self):
        cases = {
            "周二至周日 09:00-17:00": [0],
            "周二-周日 09:00-17:00(16:00停止入馆)": [0],
            "09:00-17:00，周一闭馆": [0],
            "每周一、二休息": [0, 1],
            "周一至周三闭馆": [0, 1, 2],
            "周一至周五 09:00-17:00；周六至周日 08:00-18:00": [],
            "周六 10:00-16:00": [0, 1, 2, 3, 4, 6],
            # 以下两条是高德 opentime2 实测原文（南京博物院 / 南京市博物馆，2026-10-22）
            "周一 闭馆(法定节假日除外)；周二至周日 09:00-17:00开放(16:00停止入园)": [0],
            "周二至周日 09:00-18:00开放（17:00停止入园）；周一 闭馆（国家法定节假日正常开放）；具体开放时间以官方通知为准": [0],
            "周一至周日 00:00-24:00": [],
            "09:00-17:00": None,
            "": None,
        }
        for text, expected in cases.items():
            self.assertEqual(parse_closed_days(text), expected, text)

    def test_open_time_overrides_name_keyword(self):
        # 名称含「博物馆」但营业时间写明周一开放，不应套用默认周一闭馆
        s = Spot(name="某某博物馆", lng=118.8, lat=32.0, open_time="周一至周日 09:00-17:00")
        self.assertFalse(is_spot_closed_on_date(s, date(2026, 10, 26))[0])

    def test_spot_not_scheduled_on_parsed_closed_day(self):
        s = Spot(name="某某艺术中心", lng=118.80, lat=32.05, open_time="周二至周日 09:00-17:00")
        plan = TripScheduler("南京", 1, HOTEL, start_date=MONDAY).schedule([s], [])
        self.assertEqual(_items(plan, "spot"), [])
        self.assertIn("闭馆", plan.unassigned_spots[0])


def _plan(items, departure_min=None, day_date=TUESDAY):
    return FullTripPlan(city="南京", total_days=1, hotel=HOTEL, departure_min=departure_min,
                        days=[DayItinerary(day_number=1, theme="t", date=day_date, items=items)])


def _item(slot, category, title, loc=None, open_time=""):
    return ScheduleItem(time_slot=slot, category=category, title=title, description="", location=loc,
                        open_time=open_time)


class TestValidator(unittest.TestCase):
    """校验器独立于排程器：手工构造有问题的行程，必须逐项识别出来。"""

    def setUp(self):
        self.start = _item("09:00", "hotel", "出发", HOTEL)
        self.spot = Spot(**XUANWU)

    def _errors(self, plan):
        return [str(i) for i in validate_plan(plan) if i.level == "error"]

    def test_overlap(self):
        errs = self._errors(_plan([self.start, _item("09:30 - 11:00", "spot", "A", self.spot),
                                   _item("10:30 - 11:30", "free", "B")]))
        self.assertTrue(any("重叠" in e for e in errs), errs)

    def test_not_enough_transit_time(self):
        errs = self._errors(_plan([self.start, _item("09:01 - 11:00", "spot", "A", self.spot)]))
        self.assertTrue(any("约需" in e for e in errs), errs)

    def test_outside_opening_hours(self):
        errs = self._errors(_plan([self.start, _item("19:00 - 20:00", "spot", "A", self.spot, "09:00-17:00")]))
        self.assertTrue(any("营业时间" in e for e in errs), errs)

    def test_closed_day(self):
        museum = Spot(name="南京博物院", lng=118.8232, lat=32.0410, closed_days=[0])
        errs = self._errors(_plan([self.start, _item("10:00 - 12:00", "spot", "A", museum)], day_date=MONDAY))
        self.assertTrue(any("闭馆" in e for e in errs), errs)

    def test_station_buffer(self):
        errs = self._errors(_plan([self.start, _item("09:15 - 09:45", "transit", "去车站", STATION)],
                                  departure_min=10 * 60))
        self.assertTrue(any("不足" in e for e in errs), errs)

    def test_duplicate_restaurant_and_dish(self):
        r1 = Restaurant(name="鸭血粉丝A", lng=118.785, lat=32.042, dish_key="鸭血粉丝")
        r2 = Restaurant(name="鸭血粉丝B", lng=118.786, lat=32.042, dish_key="鸭血粉丝")
        errs = self._errors(_plan([self.start, _item("11:30 - 12:30", "meal", "午餐", r1),
                                   _item("18:00 - 19:00", "meal", "晚餐", r1),
                                   _item("19:30 - 20:30", "meal", "宵夜", r2)]))
        self.assertTrue(any("餐厅" in e and "重复" in e for e in errs), errs)
        self.assertTrue(any("必吃" in e for e in errs), errs)

    def test_valid_plan_passes(self):
        t_min, _ = estimate_transit(haversine_distance(HOTEL.lng, HOTEL.lat, self.spot.lng, self.spot.lat))
        start = 9 * 60 + t_min
        slot = f"{start // 60:02d}:{start % 60:02d} - 12:00"
        self.assertEqual(self._errors(_plan([self.start, _item(slot, "spot", "A", self.spot, "06:00-22:00")])), [])

    def test_generated_plans_pass(self):
        # 排程器生成的多日行程（含返程、闭馆、夜游）都应通过校验
        spots = [Spot(name=f"景点{i}", lng=118.78 + 0.01 * i, lat=32.04 + 0.006 * i, duration_minutes=100,
                      open_time="08:30-17:30") for i in range(7)]
        spots.append(Spot(name="南京博物院", lng=118.8232, lat=32.0410, duration_minutes=180))
        spots.append(Spot(name="夫子庙夜游", lng=118.7886, lat=32.0210, best_period="evening",
                          duration_minutes=90, open_time="全天"))
        rs = [Restaurant(name=f"餐厅{i}", lng=118.78 + 0.01 * i, lat=32.041 + 0.006 * i,
                         meal_type=("meal", "snack", "night_market")[i % 3], open_time="10:00-22:00")
              for i in range(9)]
        for pace in ("relaxed", "balanced", "compact"):
            plan = TripScheduler("南京", 3, HOTEL, departure_point=STATION, departure_time="16:00",
                                 start_date=MONDAY, pace=pace).schedule(spots, rs)
            self.assertEqual(self._errors(plan), [], pace)


if __name__ == "__main__":
    unittest.main()
