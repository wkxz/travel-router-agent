"""两顿之间至少隔 MEAL_GAP_MIN（90 分钟）：小吃、晚餐、返程前告别餐都按上一顿实际吃完的时间往后排。

2026-10-30 第 4 轮测评 12 例里有 17 处两顿挨得太近（eval/plan_checks.py 的 meal_too_close）：
午饭 14:25 吃完、14:35 又排小吃；小吃 17:35 吃完、18:00 吃晚饭；广州游长隆到 15:30，17:10 吃完午饭、18:13 又吃晚饭。

运行：./.venv/Scripts/python.exe -m unittest tests.test_meal_gap -v
"""

import unittest

from app.engine import LocationPoint, Restaurant, Spot, TripScheduler
from app.engine.renderer import render_dict
from eval.plan_checks import check_plan

HOTEL = LocationPoint(name="住处", lng=118.78, lat=32.05)
STATION = LocationPoint(name="南京南站", lng=118.80, lat=32.04)


def _r(name, dx, meal_type="meal"):
    return Restaurant(name=name, lng=118.78 + dx, lat=32.051, meal_type=meal_type, rating=4.5, cost=50)


RESTAURANTS = [_r("午饭馆", 0.004), _r("晚饭馆", 0.006), _r("另一家", 0.002),
               _r("锅贴店", 0.005, "snack"), _r("鸭血粉丝", 0.007, "snack")]


def _spot(name, minutes, open_time="08:30-17:00", **kw):
    return Spot(name=name, lng=118.785, lat=32.05, duration_minutes=minutes, open_time=open_time, **kw)


def _plan(spots, **kw):
    return TripScheduler("南京", 1, HOTEL, start_date="2026-11-03", **kw).schedule(spots, RESTAURANTS)


def _close_meals(plan):
    return [i["detail"] for i in check_plan(render_dict(plan)) if i["type"] == "meal_too_close"]


def _titles(plan):
    return [it.title for d in plan.days for it in d.items]


class TestMealGap(unittest.TestCase):
    def test_snack_waits_after_late_lunch(self):
        # 上午大景点游到 13:00，午饭 14:20 吃完：小吃不能 15:30 就排
        plan = _plan([_spot("大景区", 270, best_period="morning"),
                      Spot(name="小园", lng=118.786, lat=32.051, duration_minutes=60, open_time="08:30-17:00")])
        self.assertEqual(_close_meals(plan), [], _titles(plan))

    def test_snack_kept_when_there_is_room(self):
        # 午饭 12:45 吃完，下午有空：小吃照排，不因间隔规则全部去掉
        plan = _plan([_spot("上午点", 120), _spot("下午点", 150)])
        self.assertEqual(_close_meals(plan), [])
        self.assertTrue(any("小吃" in t for t in _titles(plan)), _titles(plan))

    def test_departure_day_snack_not_right_after_lunch(self):
        plan = _plan([_spot("大景区", 300)], departure_point=STATION, departure_time="18:30")
        self.assertEqual(_close_meals(plan), [], _titles(plan))

    def test_farewell_dinner_not_right_after_lunch(self):
        # 15:29 吃完午饭、19:00 的车：告别餐最晚 16:50 开饭，离午饭不到 90 分钟，宁可省略、提示车站附近吃
        plan = _plan([_spot("大景区", 300)], departure_point=STATION, departure_time="19:00")
        self.assertEqual(_close_meals(plan), [], _titles(plan))

    def test_dinner_after_very_late_lunch(self):
        # 游大景点到 15:30 才吃午饭：晚饭往后推，不在 18:00 紧接着吃
        plan = _plan([_spot("动物园", 360, open_time="09:30-18:00")])
        self.assertEqual(_close_meals(plan), [], _titles(plan))
        self.assertTrue(any(t.startswith("晚餐") for t in _titles(plan)), _titles(plan))


if __name__ == "__main__":
    unittest.main()
