"""单向参观的景区：入口、出口分开（Spot.entry_points / exit_points），离开时从出口算、到达时从入口算。

2026-10-30 第 4 轮测评（北京 3 天）：景山公园和故宫被拆到两天，模型点评指出「故宫单向参观（午门进、神武门出），
神武门正对景山南门」。核实：高德给故宫的出入口只有一个导航入口（116.402976,39.915134），没有神武门，
故宫—景山门区距离 1.07km，同片区代价已生效仍被拆开；补上神武门后为 0.15km，两者排在同一天。
神武门只出不进，所以不能当普通门区（双向）加进去，否则可能排成「先上景山、再从神武门进故宫」。
出入口坐标来自高德 POI（「故宫博物院-神武门」B000A9PISW、「故宫博物院-午门」B000A84GDN），
哪个门进、哪个门出由模型按常识给出（spots 里的 entry_id / exit_id）。

坐标、门区、营业时间取自 2026-10-30 高德返回。
运行：./.venv/Scripts/python.exe -m unittest tests.test_entry_exit -v
"""

import unittest

from tests.fake_amap import FakeAmap
from app._data.poi_repo import PoiRepo, record_from_poi
from app._data.trip_input import build_trip_inputs
from app.engine import LocationPoint, Spot, TripScheduler
from app.engine.geo import area_distance, place_distance

HOTEL = LocationPoint(name="王府井", lng=116.41122, lat=39.915458)
STATION = LocationPoint(name="北京南站", lng=116.378517, lat=39.865246)
WUMEN = LocationPoint(name="午门", lng=116.397228, lat=39.913582)
SHENWUMEN = LocationPoint(name="神武门", lng=116.396786, lat=39.922305)

FIXTURE = [
    ("故宫博物院", 116.397029, 39.917839, 240, "morning", True,
     "旺季4月1日至10月31日 周二至周日 08:30-17:00 16:00停止检票，淡季11月1日至3月31日 周二至周日 08:30-16:30 "
     "15:30停止检票，周一全天关闭，节假日营业时间以官方通知为准", [("", 116.402976, 39.915134)]),
    ("天坛公园", 116.410829, 39.881913, 150, "", True,
     "1月至3月,11月至12月 周一至周日 06:30-22:00 最晚进入21:00；4月至10月 周一至周日 06:00-22:00 最晚进入21:00",
     [("南门", 116.41305, 39.87449), ("西门", 116.400408, 39.881086), ("东门", 116.419999, 39.884051),
      ("北门", 116.412441, 39.887913)]),
    ("颐和园", 116.275179, 39.999617, 210, "morning", True,
     "1月至3月,11月至12月 周一至周日 06:30-19:00 最晚进入18:00；4月至10月 周一至周日 06:00-20:00 最晚进入19:00",
     [("东宫门", 116.281135, 39.997868), ("北宫门", 116.274034, 40.002337), ("新建宫门", 116.280239, 39.990907)]),
    ("南锣鼓巷", 116.402394, 39.937182, 90, "evening", True, "周一至周日 00:00-24:00", [("", 116.403081, 39.933521)]),
    ("景山公园", 116.396551, 39.925875, 60, "", False,
     "1月至3月,11月至12月 周一至周日 06:30-20:00 最晚进入19:30；4月至10月 周一至周日 06:00-21:00 最晚进入20:30",
     [("东门", 116.399309, 39.925992), ("南门", 116.396846, 39.923502), ("西门", 116.393972, 39.925787)]),
    ("什刹海", 116.385121, 39.941893, 90, "evening", False, "周一至周日 00:00-24:00", [("", 116.392593, 39.933535)]),
    ("恭王府博物馆", 116.386315, 39.937222, 120, "", False,
     "周二至周日08:30-17:00(16:10停止检票),周一闭馆(法定节假日除外)", [("", 116.386522, 39.935587)]),
]


def _spots(one_way=True):
    out = []
    for name, lng, lat, minutes, period, must, hours, gates in FIXTURE:
        s = Spot(name=name, lng=lng, lat=lat, duration_minutes=minutes, best_period=period, must_visit=must,
                 open_time=hours, gates=[LocationPoint(name=n, lng=x, lat=y) for n, x, y in gates])
        if one_way and name == "故宫博物院":
            s.entry_points, s.exit_points = [WUMEN], [SHENWUMEN]
        out.append(s)
    return out


def _plan(spots):
    return TripScheduler("北京", 3, HOTEL, start_date="2026-11-04", pace="balanced",
                         departure_point=STATION, departure_time="18:00").schedule(spots, [])


def _day_of(plan):
    return {it.location.name: d.day_number for d in plan.days for it in d.items if it.category == "spot"}


class TestEntryExit(unittest.TestCase):
    def test_distance_is_directed(self):
        gugong, jingshan = _spots()[0], _spots()[4]
        self.assertLess(place_distance(gugong, jingshan), 0.2)    # 神武门出 → 景山南门
        self.assertGreater(place_distance(jingshan, gugong), 1.0)  # 景山 → 只能从午门进
        self.assertLess(area_distance(jingshan, gugong), 0.2)      # 是不是同一片区不分方向

    def test_beijing_rerun_keeps_jingshan_with_gugong(self):
        old = _day_of(_plan(_spots(one_way=False)))
        self.assertNotEqual(old["景山公园"], old["故宫博物院"])  # 复现：只有导航入口时被拆开
        plan = _plan(_spots())
        day = _day_of(plan)
        self.assertEqual(day["景山公园"], day["故宫博物院"], day)
        items = [it for d in plan.days for it in d.items if it.category == "spot"]
        names = [it.location.name for it in items]
        self.assertLess(names.index("故宫博物院"), names.index("景山公园"))  # 先故宫、后景山
        self.assertIn("神武门", items[names.index("景山公园")].transit_info)   # 路段写明从神武门出

    def test_hotel_leg_enters_at_entry(self):
        plan = _plan(_spots())
        gugong = next(it for d in plan.days for it in d.items if it.category == "spot" and it.location.name == "故宫博物院")
        self.assertIn("午门", gugong.transit_info)

    def test_parse_entry_exit_ids(self):
        repo = PoiRepo(fetch=FakeAmap())
        for pid, name, loc in (("B000A8UIN8", "故宫博物院", "116.397029,39.917839"),
                               ("B000A84GDN", "故宫博物院-午门", "116.397228,39.913582"),
                               ("B000A9PISW", "故宫博物院-神武门", "116.396786,39.922305")):
            repo.put(record_from_poi({"id": pid, "name": name, "location": loc, "type": "风景名胜;风景名胜;国家级景点",
                                      "biz_ext": {"open_time": "", "opentime2": ""}}))
        out = build_trip_inputs(repo, '[{"id": "B000A8UIN8", "entry_id": "B000A84GDN", "exit_id": "B000A9PISW"},'
                                      ' {"id": "B000A84GDN", "exit_id": "NOPE"}]', "[]")
        gugong = out.spots[0]
        self.assertEqual([(p.name, p.lng) for p in gugong.entry_points], [("午门", 116.397228)])
        self.assertEqual([(p.name, p.lng) for p in gugong.exit_points], [("神武门", 116.396786)])
        self.assertEqual(out.spots[1].exit_points, [])  # 查不到的 id 忽略，不影响排程
        self.assertTrue(any("NOPE" in n for n in out.notes), out.notes)


if __name__ == "__main__":
    unittest.main()
