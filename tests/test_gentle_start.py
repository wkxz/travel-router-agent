"""轻松节奏或同行有老人时，「宜早去」的景点也不 07:00 出门，最早 08:30 出发。

round8 杭州（02_hangzhou_relaxed，带爸妈、轻松节奏）：灵隐寺标了 morning，第 2 天 07:00 从住处出发。
start_early_for 只把出发时刻限制在 07:00–09:00，不看节奏和同行人。分天时的时间模拟（timeline.simulate_day）
用同一个最早出发时刻，两边一致。

运行：./.venv/Scripts/python.exe -m unittest tests.test_gentle_start -v
"""

import unittest

from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.geo import parse_time_to_minutes
from app.engine.timeline import EARLIEST_DEPART, earliest_depart

HOTEL = LocationPoint(name="住处", lng=120.15, lat=30.25)
TEMPLE = Spot(name="灵隐寺", lng=120.10, lat=30.24, duration_minutes=150, open_time="07:00-17:15",
              best_period="morning")


def _depart(**kw):
    plan = TripScheduler("杭州", 1, HOTEL, start_date="2026-11-03", **kw).schedule([TEMPLE], [])
    return parse_time_to_minutes(plan.days[0].items[0].time_slot)


class TestGentleStart(unittest.TestCase):
    def test_relaxed_starts_no_earlier_than_0830(self):
        self.assertEqual(_depart(pace="relaxed"), 8 * 60 + 30)

    def test_limited_mobility_starts_no_earlier_than_0830(self):
        self.assertEqual(_depart(pace="balanced", mobility="limited"), 8 * 60 + 30)

    def test_balanced_still_early(self):
        self.assertLess(_depart(pace="balanced"), 8 * 60)

    def test_context_restored(self):
        _depart(pace="relaxed")
        self.assertEqual(earliest_depart(), EARLIEST_DEPART)


if __name__ == "__main__":
    unittest.main()
