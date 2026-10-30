"""每天的游览顺序考虑日照：分天时模拟一天的时间轴，户外景点游到日落后 10 分钟（DARK_GRACE_MIN）以后的，
每处按多 40 分钟通勤（DARK_SPOT_PENALTY）计代价。

2026-10-30 模拟实验（时间轴按接近真实的车程排，300 例 11 月出行）：38 处主景点天黑后还在户外，
15 处是当天先游室内、户外排在后面，23 处是两个户外里长的排在后面（先 2 小时、再 4~5 小时）。
旧版只按通勤挑顺序，往返路程一样时按输入顺序，不看日落。
南京 11 月 25 日日落约 17:02（geo.sunset_minutes）。

运行：./.venv/Scripts/python.exe -m unittest tests.test_daylight_order -v
"""

import unittest
from datetime import date

from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.geo import parse_time_to_minutes, sunset_minutes
from app.engine.renderer import render_dict
from eval.plan_checks import check_plan

HOTEL = LocationPoint(name="住处", lng=118.80, lat=32.05)
DAY = "2026-11-25"
SUNSET = sunset_minutes(HOTEL.lat, HOTEL.lng, date(2026, 11, 25))


def _plan(spots):
    return TripScheduler("南京", 1, HOTEL, start_date=DAY, pace="compact").schedule(spots, [])


def _slots(plan):
    return {it.location.name: tuple(parse_time_to_minutes(x.strip()) for x in it.time_slot.split("-"))
            for it in plan.days[0].items if it.category == "spot"}


def _dark(plan):
    return [i["detail"] for i in check_plan(render_dict(plan)) if i["type"] == "outdoor_after_sunset"]


class TestDaylightOrder(unittest.TestCase):
    def test_long_outdoor_goes_first(self):
        # 小园 75 分钟：旧版先小园，游完 10:32 已近午饭，大园 12:56 才开始、游到 17:56（日落约 17:02）；
        # 倒过来大园 09:17-14:17、小园 16:58 游完。60 分钟以下小园游完不用先吃午饭，原顺序也不会天黑
        spots = [Spot(name="小园", lng=118.81, lat=32.05, duration_minutes=75, open_time="06:00-20:00"),
                 Spot(name="大园", lng=118.79, lat=32.05, duration_minutes=300, open_time="06:00-20:00")]
        plan = _plan(spots)
        self.assertEqual(_dark(plan), [], _slots(plan))
        self.assertLess(_slots(plan)["大园"][0], _slots(plan)["小园"][0])

    def test_indoor_goes_after_outdoor(self):
        spots = [Spot(name="某博物馆", lng=118.81, lat=32.05, duration_minutes=90, open_time="09:00-18:00",
                      is_indoor=True),
                 Spot(name="大园", lng=118.79, lat=32.05, duration_minutes=300, open_time="06:00-20:00")]
        plan = _plan(spots)
        self.assertEqual(_dark(plan), [], _slots(plan))

    def test_no_reorder_when_daylight_is_enough(self):
        # 夏天日落晚，同样两个景点照旧按输入顺序（往返路程一样）：没有天黑问题时选出的顺序与旧版相同
        spots = [Spot(name="小园", lng=118.81, lat=32.05, duration_minutes=120, open_time="06:00-20:00"),
                 Spot(name="大园", lng=118.79, lat=32.05, duration_minutes=240, open_time="06:00-20:00")]
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-06-21", pace="compact").schedule(spots, [])
        self.assertLess(_slots(plan)["小园"][0], _slots(plan)["大园"][0])


if __name__ == "__main__":
    unittest.main()
