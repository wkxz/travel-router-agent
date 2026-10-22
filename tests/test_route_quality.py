"""路线质量回归测试：分天折返、餐厅绕路、远郊干等。

坐标取自 2026-10-22 用真实高德数据复现的成都行程（春熙路住宿，7 个景点 3 天）。
修复前的问题：熊猫基地（城东北）与宽窄巷子（城西）排同一天，打车 42 分钟横穿市区；
锦里与武侯祠相隔 0.3 km 却被拆到两天；必吃火锅为了「优先」从城西专程折返春熙路。

运行：./.venv/Scripts/python.exe -m unittest tests.test_route_quality -v
"""

import itertools
import unittest

from tests.fake_amap import FakeAmap  # noqa: F401  挂载 app 别名
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from app.engine.geo import haversine_distance

CHUNXI = LocationPoint(name="春熙路", lng=104.078643, lat=30.657952)
P = {
    "熊猫基地": (104.138176, 30.740573), "大慈寺": (104.083791, 30.654243),
    "武侯祠": (104.047992, 30.646168), "锦里": (104.049828, 30.645994),
    "宽窄巷子": (104.053307, 30.663869), "杜甫草堂": (104.028514, 30.660153),
    "人民公园": (104.057641, 30.656990),
}
MONDAY = "2026-10-26"


def _spots():
    meta = {"熊猫基地": (240, "morning"), "锦里": (120, "evening"), "大慈寺": (90, ""), "人民公园": (90, "")}
    return [Spot(name=n, lng=lng, lat=lat, duration_minutes=meta.get(n, (120, ""))[0],
                 best_period=meta.get(n, (120, ""))[1], open_time="08:00-22:00")
            for n, (lng, lat) in P.items()]


def _day_spots(plan):
    return [[it.location.name for it in d.items if it.category == "spot"] for d in plan.days]


def _day_of(plan, name):
    return next(i for i, names in enumerate(_day_spots(plan)) if name in names)


class TestDaySplit(unittest.TestCase):
    def setUp(self):
        self.plan = TripScheduler("成都", 3, CHUNXI, start_date=MONDAY).schedule(_spots(), [])

    def test_all_spots_scheduled(self):
        self.assertEqual(sorted(sum(_day_spots(self.plan), [])), sorted(P))
        self.assertEqual(self.plan.unassigned_spots, [])

    def test_panda_not_paired_with_west_side(self):
        """熊猫基地在城东北 ~11km；与城西景点同一天就是横穿市区的折返。"""
        west = {"宽窄巷子", "杜甫草堂", "人民公园", "武侯祠", "锦里"}
        self.assertFalse(west & set(_day_spots(self.plan)[_day_of(self.plan, "熊猫基地")]), _day_spots(self.plan))

    def test_adjacent_spots_same_day(self):
        self.assertEqual(_day_of(self.plan, "锦里"), _day_of(self.plan, "武侯祠"), _day_spots(self.plan))

    def test_daily_order_is_shortest(self):
        """每天的游览顺序在满足「宜早去在前、夜游在后」的前提下是最短的。"""
        rank = {"morning": 0, "evening": 2}
        for d in self.plan.days:
            spots = [it.location for it in d.items if it.category == "spot"]

            def tour(seq):
                pts = [CHUNXI, *seq, CHUNXI]
                return sum(haversine_distance(a.lng, a.lat, b.lng, b.lat) for a, b in zip(pts, pts[1:]))
            legal = [p for p in itertools.permutations(spots)
                     if [rank.get(s.best_period, 1) for s in p] == sorted(rank.get(s.best_period, 1) for s in p)]
            self.assertAlmostEqual(tour(spots), min(map(tour, legal)), places=3, msg=_day_spots(self.plan))

    def test_closed_day_respected(self):
        """博物馆周一闭馆：穷举分天时直接排除闭馆日。"""
        spots = _spots()
        for s in spots:
            if s.name == "杜甫草堂":
                s.closed_days = [0]
        plan = TripScheduler("成都", 3, CHUNXI, start_date=MONDAY).schedule(spots, [])
        self.assertNotEqual(_day_of(plan, "杜甫草堂"), 0)
        self.assertTrue(any("杜甫草堂" in n and "闭馆" in n for n in plan.summary.split("\n")[1:]), plan.summary)


class TestMealDetour(unittest.TestCase):
    def _rest(self, name, lng, lat, **kw):
        return Restaurant(name=name, lng=lng, lat=lat, meal_type="meal", rating=4.5, cost=60.0,
                          cuisine="川菜", open_time="10:00-22:00", **kw)

    def test_lunch_on_the_way_beats_nearer_opposite(self):
        """宽窄巷子 → 杜甫草堂（往西）：西边顺路的店优于离宽窄更近、但在东边要折返的店。"""
        kz, dfct = Spot(name="宽窄巷子", lng=P["宽窄巷子"][0], lat=P["宽窄巷子"][1], duration_minutes=150,
                        open_time="08:00-22:00"), \
            Spot(name="杜甫草堂", lng=P["杜甫草堂"][0], lat=P["杜甫草堂"][1], duration_minutes=120,
                 open_time="08:00-22:00")
        east = self._rest("东边近店", 104.0650, 30.6640)       # 离宽窄 ~1.1 km，方向与草堂相反
        west = self._rest("顺路西边店", 104.0380, 30.6620)    # 离宽窄 ~1.5 km，就在去草堂的路上
        plan = TripScheduler("成都", 1, CHUNXI, start_date=MONDAY).schedule([kz, dfct], [east, west])
        lunch = next(it for it in plan.days[0].items if "午餐" in it.title)
        self.assertIn("顺路西边店", lunch.title)

    def test_must_eat_not_forced_into_detour_but_kept_for_last_meal(self):
        """必吃火锅在春熙路：不在城西的午餐专程折返，留到最后一顿晚餐顺路吃。"""
        kz = Spot(name="宽窄巷子", lng=P["宽窄巷子"][0], lat=P["宽窄巷子"][1], duration_minutes=150,
                  open_time="08:00-22:00")
        dfct = Spot(name="杜甫草堂", lng=P["杜甫草堂"][0], lat=P["杜甫草堂"][1], duration_minutes=120,
                    open_time="08:00-22:00")
        hotpot = self._rest("春熙路老火锅", 104.0788, 30.6583, dish_key="老火锅")
        west = self._rest("草堂川菜", 104.0380, 30.6620)
        dinner_near = self._rest("草堂晚餐", 104.0300, 30.6595)
        plan = TripScheduler("成都", 1, CHUNXI, start_date=MONDAY).schedule(
            [kz, dfct], [hotpot, west, dinner_near], must_eat=["老火锅"])
        titles = [it.title for it in plan.days[0].items]
        lunch = next(t for t in titles if "午餐" in t)
        dinner = next(t for t in titles if "晚餐" in t)
        self.assertNotIn("老火锅", lunch, titles)
        self.assertIn("老火锅", dinner, titles)
        self.assertEqual(plan.unmet_must_eat, [])


class TestLongGapFarFromHotel(unittest.TestCase):
    PANDA = dict(name="熊猫基地", lng=P["熊猫基地"][0], lat=P["熊猫基地"][1], duration_minutes=240,
                 best_period="morning", open_time="07:30-18:00")

    def _idle_location(self, plan):
        """最长那段自由活动开始时人在哪（前一个有坐标的项目）。"""
        items = plan.days[0].items
        free = max((i for i, it in enumerate(items) if it.category == "free"),
                   key=lambda i: int(items[i].time_slot[8:10]) * 60 + int(items[i].time_slot[11:13])
                   - int(items[i].time_slot[:2]) * 60 - int(items[i].time_slot[3:5]))
        return next(it.location for it in reversed(items[:free]) if it.location)

    def test_lunch_on_way_back_then_idle_in_city(self):
        """回城路上有饭吃：远郊游完直接回城吃午饭，下午空档在住处附近。"""
        rs = [Restaurant(name=n, lng=lng, lat=lat, meal_type="meal", rating=4.5, open_time="10:00-22:00")
              for n, lng, lat in [("春熙路午餐", 104.0790, 30.6585), ("春熙路晚餐", 104.0795, 30.6590)]]
        plan = TripScheduler("成都", 1, CHUNXI, start_date=MONDAY).schedule([Spot(**self.PANDA)], rs)
        loc = self._idle_location(plan)
        self.assertLessEqual(haversine_distance(loc.lng, loc.lat, CHUNXI.lng, CHUNXI.lat), 2.0,
                             [it.title for it in plan.days[0].items])

    def test_return_to_hotel_when_nothing_on_the_way(self):
        """远郊附近与回城路上都没有候选：午餐周边自选后先回住处休整，不在远郊干等。"""
        plan = TripScheduler("成都", 1, CHUNXI, start_date=MONDAY).schedule([Spot(**self.PANDA)], [])
        titles = [it.title for it in plan.days[0].items]
        i_back = next(i for i, t in enumerate(titles) if "休整" in t)
        i_dinner = next(i for i, t in enumerate(titles) if "晚餐" in t)
        self.assertLess(i_back, i_dinner, titles)
        loc = self._idle_location(plan)
        self.assertEqual(loc.name, CHUNXI.name, titles)

    def test_no_return_when_hotel_is_close(self):
        near = Spot(name="大慈寺", lng=P["大慈寺"][0], lat=P["大慈寺"][1], duration_minutes=90,
                    open_time="08:00-18:00")
        plan = TripScheduler("成都", 1, CHUNXI, start_date=MONDAY).schedule([near], [])
        self.assertFalse(any("休整" in it.title for it in plan.days[0].items))


if __name__ == "__main__":
    unittest.main()
