"""空档补点时每日名额按时长算：游玩 ≥90 分钟算 1 个，短的按比例（60 分钟约 0.67 个）。

2026-10-30 第 4 轮测评（重庆 1 天，均衡档每天白天 3 个）：李子坝、解放碑、十八梯各 60 分钟，
3 个就算排满，15:15-17:45 空着 2.5 小时，备选的鹅岭公园没补进来。第 4、5 轮 21 个空档 ≥2 小时的天里，
有 7 天是「名额已满、还有备选没用上」。分天穷举仍按个数算（一天塞进 7、8 个短点时全排列会爆），
只放宽空档补点这一步，所以只会比原来宽、不会更严。坐标、营业时间取自当次高德返回（鹅岭公园没排上，按实际位置估）。

运行：./.venv/Scripts/python.exe -m unittest tests.test_cap_by_duration -v
"""

import unittest

from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.scheduler import cap_load

HOTEL = LocationPoint(name="解放碑", lng=106.575545, lat=29.55772)


def _spots():
    return [
        Spot(name="李子坝单轨穿楼观景平台", lng=106.537916, lat=29.553629, duration_minutes=60, best_period="morning",
             must_visit=True, open_time="周一至周日 06:30-23:00"),
        Spot(name="解放碑步行街", lng=106.575329, lat=29.557253, duration_minutes=60, must_visit=True,
             open_time="周一至周日 00:00-24:00"),
        Spot(name="洪崖洞民俗风貌区", lng=106.579027, lat=29.562204, duration_minutes=120, best_period="evening",
             must_visit=True, open_time="周一至周日 00:00-24:00"),
    ]


def _backups():
    return [
        Spot(name="重庆十八梯传统风貌区", lng=106.573042, lat=29.551015, duration_minutes=60,
             open_time="周一至周日 00:00-24:00"),
        Spot(name="鹅岭公园", lng=106.5446, lat=29.5540, duration_minutes=60, open_time="周一至周日 06:00-22:00"),
    ]


class TestCapByDuration(unittest.TestCase):
    def test_cap_load(self):
        mk = lambda m: Spot(name=f"点{m}号", lng=0, lat=0, duration_minutes=m)
        self.assertEqual(cap_load([mk(240), mk(90)]), 2)
        self.assertAlmostEqual(cap_load([mk(45), mk(60)]), 0.5 + 2 / 3)

    def test_chongqing_idle_afternoon_filled(self):
        plan = TripScheduler("重庆", 1, HOTEL, start_date="2026-10-30").schedule(_spots(), [], backup_spots=_backups())
        placed = [it.location.name for d in plan.days for it in d.items if it.category == "spot"]
        self.assertIn("鹅岭公园", placed, [(it.time_slot, it.title) for it in plan.days[0].items])

    def test_long_spots_still_capped(self):
        # 3 个 2 小时的景点已满额：不因空档再补
        spots = [Spot(name=f"大景点{i}号", lng=106.57 + 0.002 * i, lat=29.557, duration_minutes=120,
                      open_time="08:00-17:00") for i in range(3)]
        plan = TripScheduler("重庆", 1, HOTEL, start_date="2026-10-30").schedule(spots, [], backup_spots=_backups())
        placed = [it.location.name for d in plan.days for it in d.items if it.category == "spot"]
        self.assertEqual(len(placed), 3, placed)


if __name__ == "__main__":
    unittest.main()
