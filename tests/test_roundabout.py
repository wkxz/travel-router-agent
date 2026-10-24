"""回归测试：直线近、路网要绕远（隔海/隔江）的路段。

2026-10-24 厦门实测：鼓浪屿与中山路直线 1.5km，驾车 9.3km / 85 分钟。挑餐厅按直线估算，
第一天在岛上吃午饭 → 回市区吃小吃 → 再回岛上吃晚饭 → 回住处，一天过海 4 趟。
"""

import unittest

from tests.fake_amap import FakeAmap  # noqa: F401  挂载 app 别名
from app._data.routing import AmapTravel
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler, TravelTimes
from app.engine.travel import Leg, is_roundabout

ISLAND_MAX_LNG = 118.072  # 经度小于它的算「岛上」
HOTEL = LocationPoint(name="市区酒店", lng=118.080, lat=24.455)


def on_island(p) -> bool:
    return p.lng < ISLAND_MAX_LNG


class StraitTravel(TravelTimes):
    """同侧按估算；过海一律 80 分钟、9km（相当于高德驾车给出的绕行路线）。"""

    def leg(self, a, b, mode="auto"):
        if on_island(a) != on_island(b):
            return Leg(80, "打车/驾车约 80 分钟 (9.0 km)", 9.0, False, roundabout=True)
        est = self.estimate(a, b, mode)
        return Leg(est.minutes, est.desc, est.road_km, False)


class TestIsRoundabout(unittest.TestCase):
    def test_threshold(self):
        self.assertTrue(is_roundabout(1.5, 9.3))   # 厦门实测：过海
        self.assertFalse(is_roundabout(1.5, 2.2))  # 城区正常绕行
        self.assertFalse(is_roundabout(0.3, 1.2))  # 倍数大但绝对值小（步行绕个街区）

    def test_amap_leg_marked_and_described(self):
        far = LocationPoint("岛上", 118.067, 24.447)

        def fetch(path, params):
            return {"results": [{"origin_id": "1", "distance": "9300", "duration": "4800"}]}

        leg = AmapTravel([HOTEL, far], fetch=fetch).leg(HOTEL, far)
        self.assertTrue(leg.roundabout)
        self.assertIn("以地图导航为准", leg.desc)


class TestNoBackAndForthAcrossWater(unittest.TestCase):
    def setUp(self):
        self.island_spot = Spot(name="岛上景区", lng=118.067, lat=24.447, duration_minutes=240)
        self.restaurants = [
            Restaurant(name="岛上午餐店", lng=118.066, lat=24.448, meal_type="meal", rating=4.5),
            Restaurant(name="岛上晚餐店", lng=118.068, lat=24.446, meal_type="meal", rating=4.3),
            Restaurant(name="市区海蛎煎", lng=118.082, lat=24.457, meal_type="snack", rating=4.6, dish_key="海蛎煎"),
            Restaurant(name="市区高分餐厅", lng=118.081, lat=24.456, meal_type="meal", rating=4.9),
        ]

    def _plan(self):
        sched = TripScheduler("某市", 1, HOTEL, start_date="2026-11-03", travel=StraitTravel())
        return sched.schedule([self.island_spot], self.restaurants, must_eat=["海蛎煎"])

    def test_crosses_water_at_most_twice(self):
        plan = self._plan()
        items = plan.days[0].items
        located = [it.location for it in items if it.location is not None]
        crossings = sum(on_island(a) != on_island(b) for a, b in zip(located, located[1:]))
        self.assertLessEqual(crossings, 2, [(it.time_slot, it.title) for it in items])

    def test_meals_stay_on_current_side(self):
        titles = [it.title for it in self._plan().days[0].items]
        self.assertTrue(any("岛上午餐店" in t for t in titles), titles)
        self.assertFalse(any("市区海蛎煎" in t for t in titles), titles)  # 从岛上专程过海吃小吃
        self.assertFalse(any("市区高分餐厅" in t for t in titles), titles)


if __name__ == "__main__":
    unittest.main()
