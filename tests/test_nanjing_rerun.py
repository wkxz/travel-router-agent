"""2026-10-30 南京 3 天实测（用户会话回放）暴露的问题，坐标、门区与营业时间取自当次高德返回。

1. 音乐台（高德全名「钟山风景名胜区中山陵景区音乐台」）是中山陵景区里的一个点，模型没给时长时按 120 分钟算，
   和中山陵一起放不下，被拆到第 2 天 07:00 单独打车去。
2. 第 3 天按估算分钟数选路线，「老门东 → 总统府 → 夫子庙」只比「总统府 → 老门东 → 夫子庙」少 1 分钟，
   却多走 5km、来回坐车。
3. 空档补进明孝陵 16:38-17:38：11 月底南京 17:05 左右日落，天黑才进户外景区。
4. 午餐百味斋素菜馆、晚餐鸡鸣寺素面馆：后者高德分类是笼统的「中餐厅」，菜系去重没认出来。

运行：./.venv/Scripts/python.exe -m unittest tests.test_nanjing_rerun -v
"""

import unittest
from datetime import date

from tests.fake_amap import FakeAmap
from app._data.poi_repo import PoiRepo, record_from_poi
from app._data.trip_input import build_trip_inputs, SUB_SPOT_DEFAULT_MIN
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from app.engine.calendar_pack import is_sub_spot
from app.engine.geo import sunset_minutes, parse_time_to_minutes
from app.engine.scheduler import cuisine_key
from app.engine.travel import Leg, TravelTimes

HOTEL = LocationPoint(name="南京住宿点（按景点中心估算）", lng=118.80999055555556, lat=32.04534822222222)
ZS_OPEN = "06:30-18:30（2月1日-11月30日），7:00-17:30（12月1日-1月31日）(数据来源:江苏省文旅厅公共服务平台苏心游)"
ZSL_OPEN = ("08:30-17:00(周一闭馆)；法定节假日及孙中山先生诞辰、逝世纪念日正常开放,"
            "墓室仅周二至周五开放(数据来源:江苏省文旅厅公共服务平台苏心游)")
MUSIC = "钟山风景名胜区中山陵景区音乐台"


def _g(*pts):
    return [LocationPoint(name=n, lng=x, lat=y) for n, x, y in pts]


def _spots(music_minutes=SUB_SPOT_DEFAULT_MIN):
    """用户会话里模型传的 9 个景点（时长、宜早/宜晚、点名与当次一致）。"""
    return [
        Spot(name=MUSIC, lng=118.855382, lat=32.055096, duration_minutes=music_minutes, best_period="morning",
             must_visit=True, open_time=ZS_OPEN, gates=_g(("", 118.84812, 32.05576))),
        Spot(name="中山陵景区", lng=118.854097, lat=32.054508, duration_minutes=120, open_time=ZSL_OPEN,
             gates=_g(("", 118.849683, 32.047667))),
        Spot(name="南京博物院", lng=118.825064, lat=32.040802, duration_minutes=180, must_visit=True, is_indoor=True,
             open_time="周一 闭馆(法定节假日除外)；周二至周日 09:00-17:00开放(16:00停止入园)",
             gates=_g(("", 118.824015, 32.039131))),
        Spot(name="古鸡鸣寺", lng=118.795246, lat=32.061061, duration_minutes=120, must_visit=True,
             open_time="07:00-17:00；月初周一、15开放时间为:05:30-17:00,16:45停止入园,2026年春节期间07:00-18:00",
             gates=_g(("", 118.795823, 32.060155))),
        Spot(name="玄武湖景区", lng=118.812688, lat=32.069455, duration_minutes=120, must_visit=True,
             open_time="周一至周日 00:00-24:00",
             gates=_g(("玄武门", 118.787506, 32.070519), ("解放门", 118.796638, 32.062228), ("", 118.813101, 32.069758))),
        Spot(name="先锋书店(老门东店)", lng=118.785259, lat=32.011452, duration_minutes=120, must_visit=True,
             is_indoor=True, open_time="周一至周四 10:00-21:00；周五至周日 10:00-22:00"),
        Spot(name="总统府", lng=118.797398, lat=32.044228, duration_minutes=120,
             open_time="08:30-18:00（17:00停止入场，周一闭馆）(数据来源:江苏省文旅厅公共服务平台苏心游)",
             gates=_g(("", 118.797561, 32.042579))),
        Spot(name="老门东", lng=118.787645, lat=32.011604, duration_minutes=120, best_period="evening",
             must_visit=True, open_time="周一至周日 00:00-24:00", gates=_g(("", 118.78738, 32.013039))),
        Spot(name="南京夫子庙", lng=118.788899, lat=32.02066, duration_minutes=120, best_period="evening",
             open_time="周一至周日 09:00-22:00", gates=_g(("", 118.787947, 32.019211))),
    ]


def _rows(plan):
    return [(d.day_number, it.time_slot, it.location.name) for d in plan.days for it in d.items if it.category == "spot"]


def _day_of(plan):
    return {it.location.name: d.day_number for d in plan.days for it in d.items if it.category == "spot"}


class TestSubSpot(unittest.TestCase):
    def test_name_contains_parent(self):
        zsl = Spot(name="中山陵景区", lng=118.854097, lat=32.054508)
        self.assertTrue(is_sub_spot(Spot(name=MUSIC, lng=118.855382, lat=32.055096), zsl))
        self.assertFalse(is_sub_spot(zsl, zsl))
        # 括号里的分店名不算：先锋书店(玄武湖分店) 不是玄武湖景区的一部分
        lake = Spot(name="玄武湖景区", lng=118.812688, lat=32.069455)
        self.assertFalse(is_sub_spot(Spot(name="先锋书店(玄武湖分店)", lng=118.793003, lat=32.074346), lake))
        # 名字对得上但离得远（另一座城市的同名景点）不算
        self.assertFalse(is_sub_spot(Spot(name="中山陵景区音乐台", lng=113.3, lat=23.1), zsl))

    def test_sub_spot_default_duration(self):
        """模型没给时长的子景点按 SUB_SPOT_DEFAULT_MIN 算；给了就用模型的。"""
        repo = PoiRepo(fetch=FakeAmap())
        for pid, name, loc in (("B0ZSL", "中山陵景区", "118.854097,32.054508"),
                               ("B0MUSIC", MUSIC, "118.855382,32.055096")):
            repo.put(record_from_poi({"id": pid, "name": name, "location": loc, "type": "风景名胜;风景名胜;风景名胜",
                                      "biz_ext": {"open_time": "", "opentime2": ZS_OPEN}}))
        out = build_trip_inputs(repo, '["B0MUSIC", "B0ZSL"]', "[]")
        self.assertEqual([s.duration_minutes for s in out.spots], [SUB_SPOT_DEFAULT_MIN, 120])
        out = build_trip_inputs(repo, '[{"id": "B0MUSIC", "duration_minutes": 90}, "B0ZSL"]', "[]")
        self.assertEqual(out.spots[0].duration_minutes, 90)

    def test_sub_spot_same_day_as_parent(self):
        plan = TripScheduler("南京", 3, HOTEL, start_date="2026-11-25").schedule(_spots(), [])
        day, rows = _day_of(plan), _rows(plan)
        self.assertEqual(plan.unassigned_spots, [], rows)
        self.assertEqual(day[MUSIC], day["中山陵景区"], rows)

    def test_sub_spot_next_to_parent_in_route(self):
        """同一天时紧挨着游：住处按景点中心估算、离南博更近，只看通勤分钟会排成 音乐台 → 南博 → 中山陵，再进一次景区。"""
        spots = [s for s in _spots() if s.name in (MUSIC, "中山陵景区", "南京博物院")]
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-11-25").schedule(spots, [])
        order = [n for _, _, n in _rows(plan)]
        self.assertEqual(order, [MUSIC, "中山陵景区", "南京博物院"], order)


class TestRouteTieByDistance(unittest.TestCase):
    NAMES = ("先锋书店(老门东店)", "总统府", "老门东", "南京夫子庙")

    def test_fewer_km_when_minutes_tie(self):
        """估算分钟数只差 1~2 分钟时按公里数选：先去总统府，再到老门东一带，傍晚就近逛夫子庙。
        用夏天的日期：11 月底日落 17:01，按日照会先逛户外的老门东、再进书店（见 test_daylight_order）。"""
        spots = [s for s in _spots() if s.name in self.NAMES]
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-06-26").schedule(spots, [])
        order = [n for _, _, n in _rows(plan)]
        self.assertEqual(plan.unassigned_spots, [], order)
        self.assertEqual(order[0], "总统府", order)
        self.assertEqual(order[-2:], ["老门东", "南京夫子庙"], order)

    def test_late_autumn_outdoor_before_bookstore(self):
        # 原始用例日期（11 月 27 日）：旧版书店 13:02、老门东 15:07-17:07（日落 17:01）；按日照先逛老门东
        spots = [s for s in _spots() if s.name in self.NAMES]
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-11-27").schedule(spots, [])
        order = [n for _, _, n in _rows(plan)]
        self.assertEqual(order, ["总统府", "老门东", "先锋书店(老门东店)", "南京夫子庙"], order)


class TestNoOutdoorFillerAfterSunset(unittest.TestCase):
    MXL = dict(name="明孝陵景区", lng=118.840463, lat=32.051926, duration_minutes=60, open_time=ZS_OPEN,
               gates=_g(("2号门", 118.83323, 32.045926), ("5号门", 118.842815, 32.047349)))

    def _plan(self, backup):
        spots = [s for s in _spots() if s.name in ("中山陵景区", "南京博物院")]
        return TripScheduler("南京", 1, HOTEL, start_date="2026-11-25").schedule(spots, [], backup_spots=[backup])

    def test_sunset_estimate(self):
        # 南京（32.05N, 118.80E）：11 月 25 日约 17:03，夏至约 19:14（UTC+8）
        self.assertAlmostEqual(sunset_minutes(32.05, 118.80, date(2026, 11, 25)), 17 * 60 + 3, delta=6)
        self.assertAlmostEqual(sunset_minutes(32.05, 118.80, date(2026, 6, 21)), 19 * 60 + 14, delta=6)

    def test_outdoor_filler_not_after_sunset(self):
        plan = self._plan(Spot(**self.MXL))
        rows = _rows(plan)
        for _, slot, name in rows:
            if name == "明孝陵景区":
                end = parse_time_to_minutes(slot.split("-")[1].strip())
                self.assertLessEqual(end, sunset_minutes(HOTEL.lat, HOTEL.lng, date(2026, 11, 25)), rows)

    def test_real_leg_rechecked_against_sunset(self):
        """筛选空档景点用估算车程，真实车程更长时也不能游到天黑：add_spot 按真实到达时刻再核对日落。
        第 4~7 轮测评实测打车中位耗时是估算的 1.57 倍；这里用 3 倍放大，旧版排成 16:36-17:36（日落 17:02）。"""
        class Slow(TravelTimes):
            def leg(self, a, b, mode="auto"):
                e = self.estimate(a, b, mode)
                return Leg(e.minutes * 3, f"约 {e.minutes * 3} 分钟", e.road_km, False)

        h = LocationPoint(name="住处", lng=118.80, lat=32.05)
        main = Spot(name="大景区", lng=118.80, lat=32.051, duration_minutes=225, open_time="08:00-18:00")
        park = Spot(name="某公园", lng=118.81, lat=32.05, duration_minutes=60, open_time="06:00-22:00")
        plan = TripScheduler("南京", 1, h, start_date="2026-11-25", travel=Slow()).schedule([main], [], backup_spots=[park])
        sunset = sunset_minutes(h.lat, h.lng, date(2026, 11, 25))
        for _, slot, name in _rows(plan):
            if name == "某公园":
                self.assertLessEqual(parse_time_to_minutes(slot.split("-")[1].strip()), sunset, _rows(plan))

    def test_indoor_filler_still_allowed(self):
        hall = Spot(name="某纪念馆", lng=118.828, lat=32.041, duration_minutes=60, open_time="09:00-20:00",
                    is_indoor=True)
        self.assertIn("某纪念馆", _day_of(self._plan(hall)), _rows(self._plan(hall)))


class TestVegetarianDedupe(unittest.TestCase):
    def test_cuisine_key_from_name(self):
        veg1 = Restaurant(name="百味斋素菜馆", lng=0, lat=0, cuisine="中式素菜馆", category="餐饮服务;中餐厅;中式素菜馆")
        veg2 = Restaurant(name="鸡鸣寺素面馆", lng=0, lat=0, cuisine="中餐厅", category="餐饮服务;中餐厅;中餐厅")
        self.assertEqual(cuisine_key(veg1), cuisine_key(veg2))
        self.assertIsNotNone(cuisine_key(veg2))

    def test_no_two_vegetarian_meals_in_a_day(self):
        hotel = LocationPoint(name="住处", lng=118.790, lat=32.060)
        spots = [Spot(name="景点A", lng=118.795, lat=32.061, duration_minutes=240, open_time="07:00-17:00")]
        rs = [Restaurant(name="百味斋素菜馆", lng=118.7955, lat=32.0612, meal_type="meal", rating=4.6,
                         cuisine="中式素菜馆", category="餐饮服务;中餐厅;中式素菜馆", open_time="10:30-20:00"),
              Restaurant(name="鸡鸣寺素面馆", lng=118.7952, lat=32.0611, meal_type="meal", rating=4.6,
                         cuisine="中餐厅", category="餐饮服务;中餐厅;中餐厅", open_time="10:30-20:00"),
              Restaurant(name="淮扬菜馆", lng=118.7960, lat=32.0615, meal_type="meal", rating=4.3,
                         cuisine="淮扬菜", category="餐饮服务;中餐厅;淮扬菜", open_time="10:30-21:00")]
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-26").schedule(spots, rs)
        meals = [it.title for it in plan.days[0].items if it.category == "meal"]
        self.assertEqual(sum("素" in t for t in meals), 1, meals)


if __name__ == "__main__":
    unittest.main()
