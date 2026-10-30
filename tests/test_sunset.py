"""看日落的景点（best_period="sunset"）：排在日落前后，晚饭顺延到游完之后。

2026-10-30 第 5、6 轮测评（广州 2 天）：广州塔标「宜傍晚」，按夜游排在晚饭后，19:25 才上塔，
当天日落约 17:51，看不到日落；而下午 15:16-17:45 空着 2 个半小时。
日落时刻按 geo.sunset_minutes 估算；游览时段尽量盖住 [日落 − 60 分钟, 日落 + 30 分钟]。
坐标、营业时间取自第 6 轮高德返回。

运行：./.venv/Scripts/python.exe -m unittest tests.test_sunset -v
"""

import unittest
from datetime import date

from app._data.poi_repo import PoiRepo, record_from_poi
from app._data.trip_input import build_trip_inputs
from app.engine import LocationPoint, Restaurant, Spot, TripScheduler
from app.engine.geo import sunset_minutes
from app.engine.renderer import render_dict
from eval.plan_checks import check_plan
from tests.fake_amap import FakeAmap

HOTEL = LocationPoint(name="北京路步行街", lng=113.268359, lat=23.122109)
TOWER = (113.324521, 23.106428)
SUNSET = sunset_minutes(TOWER[1], TOWER[0], date(2026, 10, 30))


def _spots(period="sunset"):
    tower = Spot(name="广州塔", lng=TOWER[0], lat=TOWER[1], duration_minutes=150, best_period="evening",
                 must_visit=True, open_time="周一至周日 09:30-22:30 最晚进入22:00", sunset=period == "sunset")
    return [
        Spot(name="沙面岛", lng=113.244707, lat=23.106802, duration_minutes=120, best_period="morning",
             must_visit=True, open_time="周一至周日 00:00-24:00"),
        Spot(name="永庆坊", lng=113.237495, lat=23.114821, duration_minutes=120, must_visit=True,
             open_time="周一至周日 00:00-24:00"),
        Spot(name="海心桥", lng=113.31951, lat=23.107118, duration_minutes=60, open_time="周一至周日 07:00-22:00"),
        tower,
    ]


RESTAURANTS = [
    Restaurant(name="银灯食府", lng=113.262815, lat=23.112855, meal_type="meal", rating=4.6, cost=90,
               open_time="周一至周日 08:00-16:30,17:00-21:30"),
    Restaurant(name="欧锦和厨房", lng=113.323861, lat=23.10394, meal_type="meal", rating=4.5, cost=120,
               open_time="周一至周日 11:00-14:00,17:00-24:00"),
    Restaurant(name="陶陶居", lng=113.2479, lat=23.1159, meal_type="meal", rating=4.6, cost=100),
    Restaurant(name="双皮奶店", lng=113.2600, lat=23.1170, meal_type="snack", rating=4.5, cost=20),
]


def _plan(spots):
    return TripScheduler("广州", 1, HOTEL, start_date="2026-10-30").schedule(spots, RESTAURANTS)


def _minutes(slot):
    a, _, b = slot.partition(" - ")
    to = lambda s: int(s[:2]) * 60 + int(s[3:5])
    return to(a), to(b) if b else to(a)


def _item(plan, name):
    return next(it for it in plan.days[0].items if it.category == "spot" and it.location.name == name)


class TestSunset(unittest.TestCase):
    def test_evening_tower_misses_sunset(self):
        # 复现：只标「宜傍晚」时按夜游排在晚饭后，上塔时天已黑
        start, _ = _minutes(_item(_plan(_spots("evening")), "广州塔").time_slot)
        self.assertGreater(start, SUNSET + 60)

    def test_tower_covers_sunset_and_dinner_after(self):
        plan = _plan(_spots())
        tower = _item(plan, "广州塔")
        start, end = _minutes(tower.time_slot)
        self.assertLessEqual(start, SUNSET - 30, tower.time_slot)
        self.assertGreaterEqual(start, SUNSET - 60 - 5, tower.time_slot)
        self.assertGreaterEqual(end, SUNSET + 30, tower.time_slot)
        self.assertTrue(any("日落" in w for w in tower.warnings), tower.warnings)
        items = plan.days[0].items
        dinner = next(i for i, it in enumerate(items) if it.title.startswith("晚餐"))
        self.assertLess(items.index(tower), dinner, [(it.time_slot, it.title) for it in items])
        issues = [i["type"] for i in check_plan(render_dict(plan))]
        self.assertNotIn("outdoor_after_sunset", issues)
        self.assertNotIn("meal_too_close", issues)

    def test_short_spot_still_sees_sunset(self):
        # 只游 60 分钟的观景点：不在日落前 60 分钟就到、日落时就走，而是盖住日落后的晚霞
        spots = _spots()
        spots[3].duration_minutes = 60
        start, end = _minutes(_item(_plan(spots), "广州塔").time_slot)
        self.assertLess(start, SUNSET)
        self.assertGreaterEqual(end, SUNSET + 20)

    def test_parse_sunset_period(self):
        repo = PoiRepo(fetch=FakeAmap())
        repo.put(record_from_poi({"id": "B00140TAG1", "name": "广州塔", "location": "113.324521,23.106428",
                                  "type": "风景名胜;风景名胜;国家级景点", "biz_ext": {"open_time": "", "opentime2": ""}}))
        tower = build_trip_inputs(repo, '[{"id": "B00140TAG1", "best_period": "sunset"}]', "[]").spots[0]
        self.assertEqual((tower.best_period, tower.sunset), ("evening", True))


if __name__ == "__main__":
    unittest.main()
