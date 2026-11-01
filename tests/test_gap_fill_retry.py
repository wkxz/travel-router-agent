"""空档补点：最近的候选按真实车程放不进这段空档时，换下一个候选，不直接放弃。

round8 南京（06_typo）第 1 天 13:57 起的空档：候选明孝陵直线 2.7km、估算 14 分钟，高德真实车程 43 分钟，
游完超过空档截止时刻；旧版 fill_gap 遇到任何失败都 break，后面的候选不再试，原因还写成「夜间时段放不下」。
在这段空档里放不下的候选，同一天更长的空档里仍可能放得下，所以只在本次补点中跳过，不记入当天的 gap_rejected。

运行：./.venv/Scripts/python.exe -m unittest tests.test_gap_fill_retry -v
"""

import unittest

from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.travel import Leg, TravelTimes

HOTEL = LocationPoint(name="住处", lng=118.80, lat=32.05)
MAIN = Spot(name="大景区", lng=118.80, lat=32.051, duration_minutes=240, open_time="08:00-18:00")
NEAR = Spot(name="近处园", lng=118.805, lat=32.05, duration_minutes=60, open_time="06:00-22:00", is_indoor=True)
FAR = Spot(name="稍远馆", lng=118.815, lat=32.05, duration_minutes=60, open_time="06:00-22:00", is_indoor=True)


class _JammedToNear(TravelTimes):
    """去「近处园」的路堵死（直线近，真实车程远超估算），其余按估算。"""
    def leg(self, a, b, mode="auto"):
        e = self.estimate(a, b, mode)
        return Leg(e.minutes + 300, "堵车", e.road_km, False) if b.name == NEAR.name else e


def _spots(plan):
    return [it.location.name for it in plan.days[0].items if it.category == "spot"]


class TestGapFillRetry(unittest.TestCase):
    def test_next_candidate_tried(self):
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-06-23", travel=_JammedToNear()).schedule(
            [MAIN], [], backup_spots=[NEAR, FAR])
        self.assertIn("稍远馆", _spots(plan))
        self.assertNotIn("近处园", _spots(plan))

    def test_unchanged_when_nearest_fits(self):
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-06-23").schedule([MAIN], [], backup_spots=[NEAR, FAR])
        self.assertEqual(_spots(plan), ["大景区", "近处园", "稍远馆"])


if __name__ == "__main__":
    unittest.main()
