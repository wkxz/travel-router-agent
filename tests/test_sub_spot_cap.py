"""子景点（如凤凰古城里的虹桥、沱江泛舟）不占每日景点名额：它们是在所属景区里顺带游的。

2026-10-30 第 4 轮测评（凤凰 2 天，balanced 每天白天 3 个）：凤凰古城虹桥高德营业时间 08:00-17:00，
夜游排不了、改白天；第 1 天白天已有沱江泛舟、凤凰古城、熊希龄故居 3 个，虹桥以「名额已满」进了未安排，
而当天下午空着 2 小时。坐标、营业时间取自当次高德返回（虹桥没排上、没有坐标，按古城内估）。

运行：./.venv/Scripts/python.exe -m unittest tests.test_sub_spot_cap -v
"""

import unittest

from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.calendar_pack import cap_spots

HOTEL = LocationPoint(name="凤凰古城景区", lng=109.606267, lat=27.948931)
GUCHENG = "周一至周日 00:00-24:00凤凰古城八景营业时间:08:00-17:30、沱江日游08:00-17:50、夜游18:00-23:30"


def _fenghuang():
    return [
        Spot(name="凤凰古城", lng=109.606267, lat=27.948931, duration_minutes=180, open_time=GUCHENG),
        Spot(name="沈从文故居", lng=109.602778, lat=27.947114, duration_minutes=60, is_indoor=True),
        Spot(name="湘西州凤凰古城景区-沱江泛舟", lng=109.602808, lat=27.9495, duration_minutes=60,
             best_period="morning", open_time="周一至周日 08:00-23:00"),
        Spot(name="凤凰古城虹桥", lng=109.6047, lat=27.9493, duration_minutes=60, best_period="evening",
             open_time="周一至周日 08:00-17:00"),
        Spot(name="熊希龄故居", lng=109.60186, lat=27.949458, duration_minutes=60, is_indoor=True),
        Spot(name="南华山国家森林公园", lng=109.594566, lat=27.915799, duration_minutes=150,
             open_time="周一至周日 08:30-22:40 最晚进入22:10"),
        Spot(name="凤凰奇梁洞景区", lng=109.558843, lat=28.000387, duration_minutes=120,
             open_time="周一至周日 08:30-17:30 最晚进入17:00"),
    ]


def _placed(plan):
    return [it.location.name for d in plan.days for it in d.items if it.category == "spot"]


class TestSubSpotCap(unittest.TestCase):
    def test_cap_spots_skips_sub_spots(self):
        spots = _fenghuang()
        self.assertEqual([s.name for s in cap_spots(spots[:5])], ["凤凰古城", "沈从文故居", "熊希龄故居"])
        # 互相包含（「中山陵」与「中山陵景区」）时都算，不会两个都免名额
        a = Spot(name="中山陵", lng=118.85, lat=32.05)
        b = Spot(name="中山陵景区", lng=118.851, lat=32.05)
        self.assertEqual(len(cap_spots([a, b])), 2)

    def test_fenghuang_rerun_places_hongqiao(self):
        plan = TripScheduler("凤凰县", 2, HOTEL, start_date="2026-10-30").schedule(_fenghuang(), [])
        self.assertEqual(plan.unassigned_spots, [])
        self.assertIn("凤凰古城虹桥", _placed(plan))

    def test_heuristic_split_too(self):
        # 超过 12 个点走启发式分天：再加 6 个远处的点，虹桥仍和凤凰古城排在同一天
        far = [Spot(name=f"远郊{i}号", lng=109.50 + 0.01 * i, lat=27.80, duration_minutes=60,
                    open_time="08:30-17:00") for i in range(6)]
        plan = TripScheduler("凤凰县", 4, HOTEL, start_date="2026-10-30").schedule(_fenghuang() + far, [])
        day_of = {it.location.name: d.day_number for d in plan.days for it in d.items if it.category == "spot"}
        self.assertIn("凤凰古城虹桥", day_of, plan.unassigned_spots)
        self.assertEqual(day_of["凤凰古城虹桥"], day_of["凤凰古城"])


class TestHeuristicMustSwap(unittest.TestCase):
    # 随机压力测试种子 1 第 38 例（南京坐标，4 天 relaxed、20:00 的车，13 个点走启发式分天）：
    # 必去的 S3号景区没排上，没点名的点却占着名额
    CASE = [
        ("S0号景区", 118.8176, 32.0008, 240, "", "", False),
        ("S1号景区", 118.7633, 32.0637, 120, "00:00-24:00", "evening", False),
        ("S2号博物馆", 118.7810, 32.0402, 45, "周二至周日 09:00-17:00", "morning", True),
        ("S3号景区", 118.7770, 32.0038, 120, "", "", True),
        ("S4号景区", 118.7728, 32.0812, 90, "周二至周日 09:00-17:00", "", False),
        ("S5号景区", 118.8173, 32.0375, 60, "18:00-23:00", "evening", True),
        ("S6号景区", 118.7779, 32.0098, 300, "09:00-16:30", "morning", False),
        ("S7号博物馆", 118.7701, 32.0867, 150, "09:00-17:00；周一闭馆", "", False),
        ("S8号景区", 118.8241, 32.0358, 240, "18:00-23:00", "morning", False),
        ("S9号景区", 118.7537, 32.0385, 150, "", "", True),
        ("S10号景区", 118.7512, 32.0345, 90, "08:30-17:00", "", False),
        ("S11号景区", 118.7534, 32.0881, 45, "13:00-21:00", "", True),
        ("S11号景区音乐台", 118.7564, 32.0901, 45, "06:30-18:30", "", True),
    ]

    def test_must_spot_replaces_optional(self):
        """启发式分天：点名的景点放不回任何一天时，换掉某天一个没点名的，而不是把点名的放进未安排。"""
        spots = [Spot(name=n, lng=x, lat=y, duration_minutes=m, open_time=o, best_period=p, must_visit=must,
                      is_indoor="博物馆" in n) for n, x, y, m, o, p, must in self.CASE]
        plan = TripScheduler("某市", 4, LocationPoint(name="H", lng=118.78, lat=32.05), start_date="2026-11-12",
                             pace="relaxed", departure_point=LocationPoint(name="Station", lng=118.83, lat=32.0),
                             departure_time="20:00").schedule(spots, [])
        dropped = {u.split("（")[0] for u in plan.unassigned_spots}
        self.assertEqual([s.name for s in spots if s.must_visit and s.name in dropped], [], plan.unassigned_spots)

if __name__ == "__main__":
    unittest.main()
