"""回归测试：按日期取营业时间（2026-10-24 武汉实测），返程日告别餐提前（同日大理实测）。"""

import unittest
from datetime import date, timedelta

from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from app.engine.geo import find_open_slot, first_open_minute
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

    def test_seasonal_ranges_without_year(self):
        # 2026-10-28 杭州实测（雷峰塔 / 西溪湿地高德原文节选）：旧逻辑认不出不带年份的日期段，把三季时刻并成 08:00-20:00
        leifeng = ("03-16至04-30 周一至周日 08:00-19:00；05-01至10-31 周一至周日 08:00-20:00；"
                   "11-01至03-15 周一至周日 08:00-17:30")
        self.assertEqual(hours_on_date(leifeng, date(2026, 11, 7)), "08:00-17:30")  # 实测出行日，11-01 起冬季
        self.assertEqual(hours_on_date(leifeng, date(2027, 1, 10)), "08:00-17:30")  # 跨年
        self.assertEqual(hours_on_date(leifeng, date(2026, 3, 20)), "08:00-19:00；")
        self.assertEqual(hours_on_date(leifeng, FRI), "08:00-20:00；")
        xixi = "10月8日至3月31日 周一至周日 08:00-17:30"
        self.assertEqual(hours_on_date(xixi, date(2026, 11, 7)), "08:00-17:30")
        self.assertEqual(hours_on_date(xixi, date(2027, 2, 1)), "08:00-17:30")

    def test_time_range_not_mistaken_for_date(self):
        self.assertEqual(hours_on_date("08:00-17:30至20:00", TUE), "08:00-17:30至20:00")

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

    def test_single_day_listed_before_range(self):
        # 2026-10-30 测评实测（成都宽窄巷子景区高德原文）：只有周二不开。旧版把「周一，周三」当成一组、吃掉了
        # 「周三至周日」的开头，结果只有周日营业；模型以为是排满了，删景点重排了 6 次
        text = "周一，周三至周日 00:00-24:00"
        for d in range(7):
            day = date(2026, 10, 26) + timedelta(days=d)
            expected = CLOSED_MARK if day.weekday() == 1 else "00:00-24:00"
            self.assertEqual(hours_on_date(text, day), expected, day)

    def test_real_texts_from_eval(self):
        # 只写「营业」不带时刻：开门，时刻未知
        self.assertNotEqual(hours_on_date("周一至周五 11:00-14:30；16:30-21:30,周六至周日 营业", SAT), CLOSED_MARK)
        # 节假日的特殊安排不当成平日营业时间
        holiday = "周二-周日, 09:00-17:30；周一, 全天不开放；法定节假日（含周一）, 09:00-17:30"
        self.assertEqual(hours_on_date(holiday, date(2026, 10, 26)), CLOSED_MARK)
        # 跨零点写成「次日」
        self.assertIs(find_open_slot(23 * 60, 60, hours_on_date("周一至周日 07:00-次日00:30", TUE))[0], True)

    def test_date_range_with_hyphen(self):
        """「10月1日-10月6日」用短横线连接。2026-10-30 测评：长隆 10 月 7 日后 9:30 开门，旧版按 8:30 排进行程。"""
        text = ("10月1日-10月6日:8:30-19:00；10月7日-10月31日:9:30-18:00注:营业时间与设施运营计划如有调整，"
                "请以官网/现场公示为准")
        self.assertEqual(first_open_minute(hours_on_date(text, date(2026, 10, 3))), 8 * 60 + 30)
        self.assertEqual(first_open_minute(hours_on_date(text, date(2026, 10, 30))), 9 * 60 + 30)

    def test_date_range_after_hours_in_brackets(self):
        """时刻在前、日期段在后的括号里（钟山风景区）：12 月 7:00 开门，旧版整年按 06:30 算。"""
        text = "06:30-18:30（2月1日-11月30日），7:00-17:30（12月1日-1月31日）(数据来源:江苏省文旅厅公共服务平台苏心游)"
        self.assertEqual(first_open_minute(hours_on_date(text, date(2026, 11, 25))), 6 * 60 + 30)
        self.assertEqual(first_open_minute(hours_on_date(text, date(2026, 12, 10))), 7 * 60)
        self.assertEqual(first_open_minute(hours_on_date(text, date(2027, 1, 20))), 7 * 60)

    def test_next_year_and_joined_ranges(self):
        """高德原文：「次年」跨年、多个日期段用「与」共用一段时刻。"""
        text = "每年3月15日—11月15日08:00-20:30,11月16日—次年3月14日08:00-17:30"
        self.assertEqual(hours_on_date(text, date(2026, 1, 15)), "08:00-17:30")
        self.assertEqual(hours_on_date(text, date(2026, 6, 1)), "08:00-20:30,")
        text = ("4月1日-7月26日与7月28日-10月31日 周二至周日 08:30-17:00开放 最晚进入16:10；"
                "1月1日-3月31日与11月1日-12月31日 周二至周日 08:30-16:30开放 最晚进入15:40；周一 全天不开放")
        self.assertEqual(first_open_minute(hours_on_date(text, date(2026, 1, 15))), 8 * 60 + 30)  # 周四
        self.assertIn("16:30", hours_on_date(text, date(2026, 1, 15)))
        self.assertIn("17:00", hours_on_date(text, date(2026, 5, 7)))
        self.assertEqual(hours_on_date(text, date(2026, 5, 4)), CLOSED_MARK)  # 周一

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


class TestSnackNotFullRestaurant(unittest.TestCase):
    def _snacks(self, shops):
        spots = [Spot(name="景点", lng=114.301, lat=30.581, duration_minutes=60),
                 Spot(name="景点2", lng=114.304, lat=30.583, duration_minutes=60)]
        plan = TripScheduler("某市", 1, HOTEL, start_date=TUE.isoformat()).schedule(spots, shops)
        return [it.title for it in plan.days[0].items if it.category == "snack"]

    def test_pricey_snack_candidate_skipped(self):
        shops = [Restaurant(name="正式中餐厅", lng=114.302, lat=30.582, meal_type="snack", rating=4.9, cost=81),
                 Restaurant(name="街边小吃", lng=114.303, lat=30.582, meal_type="snack", rating=4.2, cost=20)]
        snacks = self._snacks(shops)
        self.assertTrue(snacks and "正式中餐厅" not in snacks[0], snacks)

    def test_named_must_eat_snack_kept(self):
        shops = [Restaurant(name="老字号点心", lng=114.302, lat=30.582, meal_type="snack", rating=4.5, cost=81,
                            dish_key="点心")]
        self.assertTrue(any("老字号点心" in t for t in self._snacks(shops)))


if __name__ == "__main__":
    unittest.main()
