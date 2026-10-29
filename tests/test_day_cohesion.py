"""2026-10-29 南京 3 天实测（eval 用例 11_nanjing_3days）暴露的分天与时间轴问题，坐标、门区与营业时间取自当次高德返回。

1. 同片区被拆开：老门东（宜傍晚）分到第 2 天、相距 1km 的夫子庙（宜傍晚）分到第 3 天；
   中山陵与相距 0.9km 的音乐台、明孝陵也分在两天。原因是均衡项和「宜傍晚改白天」代价压过了片区。
2. 夜游景点占每日景点数：第 3 天白天只排了 2 个点，下午空档 2.5 小时。
3. 上午 10:50 结束一个景点、下一个要游 3 小时时，午饭被拖到 14 点以后。
4. 下午小吃专程绕路去吃。

运行：./.venv/Scripts/python.exe -m unittest tests.test_day_cohesion -v
"""

import unittest

from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from app.engine.geo import parse_time_to_minutes

HOTEL = LocationPoint(name="南京住宿点（按景点中心估算）", lng=118.81610422222222, lat=32.04185433333333)
ZSL_OPEN = ("08:30-17:00(周一闭馆)；法定节假日及孙中山先生诞辰、逝世纪念日正常开放,"
            "墓室仅周二至周五开放(数据来源:江苏省文旅厅公共服务平台苏心游)")
ZS_OPEN = "06:30-18:30(2月1日-11月30日)，7:00-17:30(12月1日-1月31日)(数据来源:江苏省文旅厅公共服务平台苏心游)"


def _g(*pts):
    return [LocationPoint(name=n, lng=x, lat=y) for n, x, y in pts]


def _nanjing_spots():
    return [
        Spot(name="古鸡鸣寺", lng=118.795246, lat=32.061061, duration_minutes=120, best_period="morning",
             must_visit=True, open_time="07:00-17:00", gates=_g(("", 118.795823, 32.060155))),
        Spot(name="玄武湖景区", lng=118.812688, lat=32.069455, duration_minutes=120, must_visit=True,
             open_time="周一至周日 00:00-24:00",
             gates=_g(("玄武门", 118.787506, 32.070519), ("解放门", 118.796638, 32.062228), ("", 118.813101, 32.069758))),
        Spot(name="南京博物院", lng=118.825064, lat=32.040802, duration_minutes=180, must_visit=True,
             open_time="周一 闭馆(法定节假日除外)；周二至周日 09:00-17:00开放(16:00停止入园)",
             gates=_g(("", 118.824015, 32.039131))),
        # 当次检索没搜到先锋书店，模型用了同楼的「骏惠书屋」；这里换成修复检索后能拿到的五台山总店
        Spot(name="先锋书店(五台山总店)", lng=118.773496, lat=32.050720, duration_minutes=120, must_visit=True,
             open_time="周一至周日 10:00-21:00"),
        Spot(name="老门东", lng=118.787645, lat=32.011604, duration_minutes=120, best_period="evening",
             must_visit=True, open_time="周一至周日 00:00-24:00", gates=_g(("", 118.78738, 32.013039))),
        Spot(name="音乐台", lng=118.855382, lat=32.055096, duration_minutes=120, best_period="morning",
             must_visit=True, open_time=ZS_OPEN, gates=_g(("", 118.84812, 32.05576))),
        Spot(name="中山陵景区", lng=118.854097, lat=32.054508, duration_minutes=120, open_time=ZSL_OPEN,
             gates=_g(("", 118.849683, 32.047667))),
        Spot(name="明孝陵景区", lng=118.840463, lat=32.051926, duration_minutes=150, open_time=ZS_OPEN,
             gates=_g(("1号门", 118.836139, 32.049361), ("2号门", 118.83323, 32.045926), ("3号门", 118.836087, 32.04278),
                      ("4号门", 118.839234, 32.045999), ("5号门", 118.842815, 32.047349), ("6号门", 118.843842, 32.051907))),
        Spot(name="夫子庙秦淮风光带", lng=118.788972, lat=32.020699, duration_minutes=120, best_period="evening",
             open_time="周一至周日 09:00-22:00", gates=_g(("", 118.792108, 32.021829))),
    ]


def _day_of(plan):
    return {it.location.name: d.day_number for d in plan.days for it in d.items if it.category == "spot"}


def _spot_rows(plan):
    return [(d.day_number, it.time_slot, it.location.name) for d in plan.days for it in d.items if it.category == "spot"]


class TestSameAreaSameDay(unittest.TestCase):
    def _plan(self, drop=(), pace="balanced"):
        spots = [s for s in _nanjing_spots() if s.name not in drop]
        return TripScheduler("南京", 3, HOTEL, start_date="2026-11-25", pace=pace).schedule(spots, [])

    def test_walkable_pairs_same_day(self):
        plan = self._plan()
        day, rows = _day_of(plan), _spot_rows(plan)
        self.assertEqual(plan.unassigned_spots, [], rows)
        self.assertEqual(day["古鸡鸣寺"], day["玄武湖景区"], rows)
        self.assertEqual(day["音乐台"], day["中山陵景区"], rows)

    def test_adjacent_evening_spots_same_day(self):
        """老门东与夫子庙相距约 1km：时间放得下时一个下午逛、一个晚上逛，不拆到两天。

        完整 9 个点按均衡档（白天 420 分钟）放不下这种排法——即使拆片区代价调到 5000 也只能拆开，
        所以这里去掉明孝陵、或改用特种兵档验证。"""
        for kw in ({"drop": ("明孝陵景区",)}, {"pace": "compact"}):
            plan = self._plan(**kw)
            day, rows = _day_of(plan), _spot_rows(plan)
            self.assertEqual(plan.unassigned_spots, [], (kw, rows))
            self.assertEqual(day["老门东"], day["夫子庙秦淮风光带"], (kw, rows))


class TestNightSpotNotInDailyCap(unittest.TestCase):
    def test_three_daytime_plus_one_night(self):
        """均衡档每天白天 ≤3 个；晚餐后的夜游另算，不挤占白天名额。"""
        spots = [Spot(name="白天A", lng=118.790, lat=32.040, duration_minutes=90),
                 Spot(name="白天B", lng=118.795, lat=32.040, duration_minutes=90),
                 Spot(name="白天C", lng=118.800, lat=32.040, duration_minutes=90),
                 Spot(name="夜游D", lng=118.797, lat=32.042, duration_minutes=90, best_period="evening")]
        hotel = LocationPoint(name="住处", lng=118.795, lat=32.038)
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-25").schedule(spots, [])
        self.assertEqual(plan.unassigned_spots, [], _spot_rows(plan))
        self.assertEqual(plan.days[0].total_spots, 4)

    def test_daytime_still_capped(self):
        spots = [Spot(name=f"白天{i}", lng=118.790 + 0.003 * i, lat=32.040, duration_minutes=60) for i in range(4)]
        hotel = LocationPoint(name="住处", lng=118.795, lat=32.038)
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-25").schedule(spots, [])
        self.assertEqual(plan.days[0].total_spots, 3, _spot_rows(plan))

    def test_backup_does_not_exceed_daytime_cap(self):
        spots = [Spot(name="白天A", lng=118.790, lat=32.040, duration_minutes=60),
                 Spot(name="白天B", lng=118.793, lat=32.040, duration_minutes=60),
                 Spot(name="白天C", lng=118.796, lat=32.040, duration_minutes=60),
                 Spot(name="夜游D", lng=118.797, lat=32.042, duration_minutes=60, best_period="evening")]
        hotel = LocationPoint(name="住处", lng=118.795, lat=32.038)
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-25").schedule(
            spots, [], backup_spots=[Spot(name="备选E", lng=118.794, lat=32.041, duration_minutes=60)])
        self.assertNotIn("备选E", _day_of(plan), _spot_rows(plan))


class TestLunchBeforeLongSpot(unittest.TestCase):
    def test_lunch_not_pushed_past_two(self):
        """10:50 左右结束一个景点、下一个要 3 小时：先吃午饭再去，而不是 14 点以后才吃。"""
        hotel = LocationPoint(name="住处", lng=118.790, lat=32.040)
        spots = [Spot(name="上午A", lng=118.791, lat=32.040, duration_minutes=105, best_period="morning",
                      open_time="09:00-17:00"),
                 Spot(name="大景点B", lng=118.811, lat=32.040, duration_minutes=180, open_time="09:00-17:00")]
        rs = [Restaurant(name="午餐店", lng=118.792, lat=32.040, meal_type="meal")]
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-25").schedule(spots, rs)
        items = plan.days[0].items
        lunch = next(it for it in items if it.title.startswith("午餐"))
        b = next(it for it in items if it.category == "spot" and it.location.name == "大景点B")
        rows = [(it.time_slot, it.title) for it in items]
        self.assertLessEqual(parse_time_to_minutes(lunch.time_slot.split("-")[0].strip()), 12 * 60 + 30, rows)
        self.assertLess(items.index(lunch), items.index(b), rows)


class TestSnackOnTheWay(unittest.TestCase):
    def test_no_detour_for_snack(self):
        """下午小吃只在顺路（绕路 ≤1km）时安排，不专程跑 2km 外吃一口。"""
        hotel = LocationPoint(name="住处", lng=118.790, lat=32.040)
        spots = [Spot(name="景点A", lng=118.800, lat=32.040, duration_minutes=240, open_time="09:00-17:00")]
        rs = [Restaurant(name="午餐店", lng=118.801, lat=32.040, meal_type="meal"),
              Restaurant(name="晚餐店", lng=118.799, lat=32.040, meal_type="meal"),
              Restaurant(name="反方向小吃", lng=118.820, lat=32.040, meal_type="snack")]
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-25").schedule(spots, rs)
        rows = [(it.time_slot, it.title) for it in plan.days[0].items]
        self.assertFalse(any("反方向小吃" in it.title for it in plan.days[0].items), rows)

    def test_snack_on_the_way_kept(self):
        hotel = LocationPoint(name="住处", lng=118.790, lat=32.040)
        spots = [Spot(name="景点A", lng=118.800, lat=32.040, duration_minutes=240, open_time="09:00-17:00")]
        rs = [Restaurant(name="午餐店", lng=118.801, lat=32.040, meal_type="meal"),
              Restaurant(name="晚餐店", lng=118.799, lat=32.040, meal_type="meal"),
              Restaurant(name="顺路小吃", lng=118.7995, lat=32.0402, meal_type="snack")]
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-25").schedule(spots, rs)
        rows = [(it.time_slot, it.title) for it in plan.days[0].items]
        self.assertTrue(any("顺路小吃" in it.title for it in plan.days[0].items), rows)


if __name__ == "__main__":
    unittest.main()
