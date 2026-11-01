"""轻松节奏也补空档，但不超过当天名额和游玩时长预算，且空档要 ≥ RELAXED_GAP_FILL_MIN。

round8 杭州（02_hangzhou_relaxed，轻松）：模型传了 3 个备选景点（苏堤、岳王庙、湖滨），第 2 天白天只排了灵隐寺，
出现 10:03-12:00、15:43-17:45 两段 2 小时空档，备选一个没用上。旧版轻松节奏完全不补空档，
与工具说明「备选景点在 1 小时以上空档且顺路时加入」不一致。

运行：./.venv/Scripts/python.exe -m unittest tests.test_relaxed_gap_fill -v
"""

import unittest

from app.engine import LocationPoint, Spot, TripScheduler

HOTEL = LocationPoint(name="住处", lng=120.15, lat=30.25)
PARK = Spot(name="小园", lng=120.135, lat=30.245, duration_minutes=60, open_time="06:00-22:00")


def _spots(main_minutes, pace="relaxed"):
    main = Spot(name="灵隐寺", lng=120.13, lat=30.24, duration_minutes=main_minutes, open_time="07:00-17:15")
    plan = TripScheduler("杭州", 1, HOTEL, start_date="2026-11-03", pace=pace).schedule([main], [], backup_spots=[PARK])
    return [it.location.name for it in plan.days[0].items if it.category == "spot"]


class TestRelaxedGapFill(unittest.TestCase):
    def test_backup_used_when_day_is_light(self):
        self.assertEqual(_spots(150), ["灵隐寺", "小园"])

    def test_budget_respected(self):
        # 轻松档每天游玩 330 分钟：290 + 60 超出，不补
        self.assertEqual(_spots(290), ["灵隐寺"])

    def test_balanced_unchanged(self):
        self.assertEqual(_spots(150, "balanced"), ["灵隐寺", "小园"])


if __name__ == "__main__":
    unittest.main()
