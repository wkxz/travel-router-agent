"""回归测试：分天按估算放得下、排时间轴按真实车程却掉了的景点，跨天对调后重排（2026-10-29 杭州实测）。

用户点名的雷峰塔分到返程日，真实车程比估算长约 60%，差几分钟赶不上车被舍弃；
第 1 天旁边的景点排得下，还空着 1.5 小时。
"""

import unittest

from tests.fake_amap import FakeAmap  # noqa: F401  挂载 app 别名
import app.engine.scheduler as scheduler_module
from app.engine import LocationPoint, Spot, TripScheduler, TravelTimes
from app.engine.travel import Leg

HOTEL = LocationPoint(name="住处", lng=120.16, lat=30.27)
STATION = LocationPoint(name="东站", lng=120.21, lat=30.29)


class SlowTravel(TravelTimes):
    """真实车程是估算的 1.6 倍（周末拥堵）：分天阶段的估算偏乐观。"""

    def leg(self, a, b, mode="auto"):
        est = self.estimate(a, b, mode)
        return Leg(int(est.minutes * 1.6), est.desc, est.road_km, False)


def _spots(named: str):
    spots = [Spot(name="远郊湿地", lng=120.06, lat=30.27, duration_minutes=210, best_period="morning"),
             Spot(name="湖边公园", lng=120.150, lat=30.230, duration_minutes=90),
             Spot(name="塔", lng=120.149, lat=30.232, duration_minutes=120),
             Spot(name="大湖", lng=120.14, lat=30.25, duration_minutes=180)]
    for s in spots:
        s.must_visit = s.name == named
    return spots


class TestSwapRetry(unittest.TestCase):
    def _plan(self, named: str, tries: int = scheduler_module.SWAP_MAX_TRIES):
        old = scheduler_module.SWAP_MAX_TRIES
        scheduler_module.SWAP_MAX_TRIES = tries
        try:
            return TripScheduler("某市", 2, HOTEL, departure_point=STATION, departure_time="17:30",
                                 start_date="2026-11-07", travel=SlowTravel()).schedule(_spots(named), [])
        finally:
            scheduler_module.SWAP_MAX_TRIES = old

    def test_without_swap_named_spot_dropped(self):
        # 前提：不对调时点名的「大湖」在返程日被舍弃，否则下面的用例没有意义
        plan = self._plan("大湖", tries=0)
        self.assertTrue(any("大湖" in u for u in plan.unassigned_spots), plan.unassigned_spots)

    def test_swap_keeps_named_spot(self):
        plan = self._plan("大湖")
        spots = [it.title for d in plan.days for it in d.items if it.category == "spot"]
        self.assertTrue(any("大湖" in t for t in spots), (spots, plan.unassigned_spots))
        self.assertEqual(plan.unassigned_spots, [])

    def test_swap_never_trades_named_spot_for_unnamed(self):
        for named in ("远郊湿地", "湖边公园", "塔"):
            plan = self._plan(named)
            spots = [it.title for d in plan.days for it in d.items if it.category == "spot"]
            self.assertTrue(any(named in t for t in spots), (named, spots))


if __name__ == "__main__":
    unittest.main()
