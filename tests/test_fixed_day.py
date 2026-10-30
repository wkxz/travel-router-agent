"""用户指定景点放第几天（spots 里的 "day"）。

2026-10-30 第 4 轮测评：用户说「第二天把陈家祠换成长隆野生动物世界」，排程工具没有指定日期的参数，
引擎按路线把长隆排在了第 1 天，模型还回复「已替换」。指定日期按「其他日子都去不了」处理，
分天穷举、启发式分天、跨天对调、空档补点、校验器都沿用闭馆日的同一套判定。

运行：./.venv/Scripts/python.exe -m unittest tests.test_fixed_day -v
"""

import unittest

from tests.fake_amap import FakeAmap
from app._data.poi_repo import PoiRepo, record_from_poi
from app._data.trip_input import build_trip_inputs
from app.engine import LocationPoint, Spot, TripScheduler

HOTEL = LocationPoint(name="住处", lng=113.27, lat=23.13)


def _day_of(plan):
    return {it.location.name: d.day_number for d in plan.days for it in d.items if it.category == "spot"}


def _big(name, lng, day=0, **kw):
    return Spot(name=name, lng=lng, lat=23.13, duration_minutes=300, open_time="09:00-18:00", day=day, **kw)


class TestFixedDay(unittest.TestCase):
    def test_pinned_day_honoured_both_ways(self):
        """两个各要 5 小时的景点，一天只放得下一个：指定哪天就放哪天（两种指定里至少一种与默认分法相反）。"""
        for pin in (1, 2):
            spots = [_big("长隆", 113.33, day=pin, must_visit=True), _big("陈家祠", 113.25)]
            plan = TripScheduler("广州", 2, HOTEL, start_date="2026-10-30").schedule(spots, [])
            day = _day_of(plan)
            self.assertEqual(day["长隆"], pin, day)
            self.assertEqual(day["陈家祠"], 3 - pin, day)
            self.assertFalse(any("闭馆" in n for n in plan.summary.split("\n")), plan.summary)  # 不当成闭馆提示

    def test_heuristic_split_honours_pin(self):
        """超过 12 个点走启发式分天，也要遵守指定日期（不指定时「远处」分在第 3 天）。"""
        spots = [Spot(name=f"点{i}", lng=113.25 + 0.004 * i, lat=23.13, duration_minutes=60, open_time="09:00-18:00")
                 for i in range(12)]
        spots.append(Spot(name="远处", lng=113.45, lat=23.05, duration_minutes=90, open_time="09:00-18:00",
                          must_visit=True, day=1))
        plan = TripScheduler("广州", 3, HOTEL, start_date="2026-10-30", pace="compact").schedule(spots, [])
        self.assertEqual(_day_of(plan).get("远处"), 1, (_day_of(plan), plan.unassigned_spots))

    def test_pinned_but_no_room_says_why(self):
        spots = [_big("长隆", 113.33, day=2, must_visit=True), _big("陈家祠", 113.25, day=2, must_visit=True)]
        plan = TripScheduler("广州", 2, HOTEL, start_date="2026-10-30").schedule(spots, [])
        self.assertEqual(len(plan.unassigned_spots), 1, plan.unassigned_spots)
        self.assertIn("指定在第 2 天", plan.unassigned_spots[0])
        self.assertEqual(set(_day_of(plan).values()), {2})

    def test_pinned_day_closed(self):
        # 2026-11-02 是周一：指定的那天闭馆，如实说明
        museum = Spot(name="博物馆", lng=113.28, lat=23.13, duration_minutes=120,
                      open_time="周一 闭馆；周二至周日 09:00-17:00", day=1)
        plan = TripScheduler("广州", 2, HOTEL, start_date="2026-11-02").schedule([museum], [])
        self.assertIn("指定的第 1 天", plan.unassigned_spots[0])
        self.assertIn("闭馆", plan.unassigned_spots[0])

    def test_day_beyond_trip_rejected(self):
        with self.assertRaises(ValueError) as cm:
            TripScheduler("广州", 2, HOTEL, start_date="2026-10-30").schedule([_big("长隆", 113.33, day=3)], [])
        self.assertIn("第 3 天", str(cm.exception))

    def test_parse_day_from_refs(self):
        repo = PoiRepo(fetch=FakeAmap())
        repo.put(record_from_poi({"id": "B0ZOO", "name": "长隆野生动物世界", "location": "113.33,23.0",
                                  "type": "风景名胜;风景名胜;动物园", "biz_ext": {"open_time": "", "opentime2": ""}}))
        repo.put(record_from_poi({"id": "B0CJC", "name": "陈家祠", "location": "113.25,23.13",
                                  "type": "风景名胜;风景名胜;风景名胜", "biz_ext": {"open_time": "", "opentime2": ""}}))
        out = build_trip_inputs(repo, '[{"id": "B0ZOO", "day": "2"}, {"id": "B0CJC", "day": "abc"}]', "[]")
        self.assertEqual([s.day for s in out.spots], [2, 0])


if __name__ == "__main__":
    unittest.main()
