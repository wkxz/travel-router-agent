"""2026-10-22 真实测评（eval/）暴露的 3 个排程 bug 的回归测试，坐标与营业时间取自当次高德返回。

1. 北京 3 天、周五 18:00 返程：宜傍晚的南锣鼓巷被分到返程日，晚上要赶车排不进，直接列为未安排；
   模型为此反复改参数重排了 3 次。
2. 中山陵营业时间含「墓室仅周二至周五开放」，被当成全景区只在周二至周五开放，周六日误判闭馆。
3. 广州：陈家祠 → 沙面途中有绕路 0km 的餐厅，必吃强制优先（阈值 2.5km）却选了绕路 2.1km 的必吃店。

运行：./.venv/Scripts/python.exe -m unittest tests.test_eval_regressions -v
"""

import unittest
from datetime import date

from tests.fake_amap import FakeAmap  # noqa: F401  挂载 app 别名
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from app.engine.calendar_pack import is_spot_closed_on_date, parse_closed_days

WANGFUJING = LocationPoint(name="王府井", lng=116.41122, lat=39.915458)
BEIJING_SOUTH = LocationPoint(name="北京南站", lng=116.378059, lat=39.867679)
GUGONG_OPEN = ("旺季4月1日至10月31日 周二至周日 08:30-17:00 16:00停止检票，淡季11月1日至3月31日 周二至周日 "
               "08:30-16:30 15:30停止检票，周一全天关闭，节假日营业时间以官方通知为准")
ZHONGSHANLING_OPEN = ("08:30-17:00(周一闭馆)；法定节假日及孙中山先生诞辰、逝世纪念日正常开放,"
                      "墓室仅周二至周五开放(数据来源:江苏省文旅厅公共服务平台苏心游)")


def _beijing_spots():
    return [
        Spot(name="故宫博物院", lng=116.397029, lat=39.917839, duration_minutes=240, best_period="morning",
             open_time=GUGONG_OPEN),
        Spot(name="颐和园", lng=116.275179, lat=39.999617, duration_minutes=240, best_period="morning",
             open_time="4月至10月 周一至周日 06:00-20:00 最晚进入19:00"),
        Spot(name="天坛公园", lng=116.410829, lat=39.881913, duration_minutes=180, best_period="morning",
             open_time="4月至10月 周一至周日 06:00-22:00 最晚进入21:00"),
        Spot(name="南锣鼓巷", lng=116.402394, lat=39.937182, duration_minutes=120, best_period="evening",
             open_time="周一至周日 00:00-24:00"),
    ]


def _spot_items(plan):
    return [(d.day_number, it) for d in plan.days for it in d.items if it.category == "spot"]


class TestEveningSpotOnDepartureDay(unittest.TestCase):
    def test_beijing_case_schedules_evening_spot(self):
        plan = TripScheduler("北京", 3, WANGFUJING, departure_point=BEIJING_SOUTH, start_date="2026-10-28",
                             departure_time="18:00").schedule(_beijing_spots(), [])
        names = [it.location.name for _, it in _spot_items(plan)]
        self.assertIn("南锣鼓巷", names, plan.unassigned_spots)
        self.assertEqual(plan.unassigned_spots, [])
        day, item = next((d, it) for d, it in _spot_items(plan) if it.location.name == "南锣鼓巷")
        if day == 3:  # 放在返程日就必须是白天，且说明原因
            self.assertTrue(any("改为白天" in w for w in item.warnings), item.warnings)
        else:
            self.assertTrue(any("宜傍晚/夜间" in w for w in item.warnings), item.warnings)

    def test_prefers_a_day_with_usable_evening(self):
        """有晚上可用的日子时，宜傍晚的景点不放返程日。"""
        plan = TripScheduler("北京", 3, WANGFUJING, departure_point=BEIJING_SOUTH, start_date="2026-10-28",
                             departure_time="18:00").schedule(_beijing_spots(), [])
        day = next(d for d, it in _spot_items(plan) if it.location.name == "南锣鼓巷")
        self.assertNotEqual(day, 3, [(d, it.title, it.time_slot) for d, it in _spot_items(plan)])

    def test_single_departure_day_falls_back_to_daytime(self):
        """只有一天且傍晚要赶车：宜傍晚的景点白天去，而不是丢掉。"""
        nanluo = _beijing_spots()[3]
        plan = TripScheduler("北京", 1, WANGFUJING, departure_point=BEIJING_SOUTH, start_date="2026-10-28",
                             departure_time="17:00").schedule([nanluo], [])
        items = _spot_items(plan)
        self.assertEqual(len(items), 1, plan.unassigned_spots)
        self.assertTrue(any("改为白天" in w for w in items[0][1].warnings), items[0][1].warnings)

    def test_late_train_keeps_evening(self):
        """返程车次够晚（23:30）时，返程日晚上仍可夜游。"""
        sched = TripScheduler("北京", 2, WANGFUJING, departure_point=BEIJING_SOUTH, departure_time="23:30")
        self.assertEqual(sched._evening_ok(), [True, True])
        sched = TripScheduler("北京", 2, WANGFUJING, departure_point=BEIJING_SOUTH, departure_time="18:00")
        self.assertEqual(sched._evening_ok(), [True, False])


class TestPartialOpeningClause(unittest.TestCase):
    def test_zhongshanling_weekend_open(self):
        self.assertEqual(parse_closed_days(ZHONGSHANLING_OPEN), [0])
        spot = Spot(name="中山陵景区", lng=118.848, lat=32.064, open_time=ZHONGSHANLING_OPEN)
        self.assertFalse(is_spot_closed_on_date(spot, date(2026, 10, 24))[0])  # 周六
        self.assertTrue(is_spot_closed_on_date(spot, date(2026, 10, 26))[0])   # 周一

    def test_whole_site_only_clause_still_counts(self):
        self.assertEqual(parse_closed_days("景区仅周六至周日开放 09:00-17:00"), [0, 1, 2, 3, 4])

    def test_sub_facility_clause_ignored(self):
        self.assertEqual(parse_closed_days("地宫仅周末开放；周一至周日 08:00-18:00"), [])


class TestMustEatDetourThreshold(unittest.TestCase):
    def test_on_the_way_lunch_beats_must_eat_detour(self):
        hotel = LocationPoint(name="北京路步行街", lng=113.268359, lat=23.122109)
        chenjiaci = Spot(name="陈家祠堂", lng=113.245158, lat=23.126692, duration_minutes=150,
                         open_time="10:00-17:30")
        shamian = Spot(name="沙面岛", lng=113.244707, lat=23.106802, duration_minutes=150,
                       open_time="周一至周日 00:00-24:00")
        on_way = Restaurant(name="清平饭店", lng=113.2452, lat=23.1170, meal_type="meal", rating=4.5,
                            cost=80.0, cuisine="粤菜", open_time="10:00-22:00")
        must = Restaurant(name="银灯食府(丽丰国际中心店)", lng=113.262815, lat=23.112855, meal_type="meal",
                          rating=4.6, cost=94.0, cuisine="粤菜", open_time="08:00-22:00", dish_key="早茶")
        plan = TripScheduler("广州", 1, hotel, start_date="2026-10-28").schedule(
            [chenjiaci, shamian], [on_way, must], must_eat=["早茶"])
        titles = [it.title for it in plan.days[0].items]
        lunch = next(t for t in titles if "午餐" in t)
        self.assertIn("清平饭店", lunch, titles)
        self.assertTrue(any("银灯食府" in t for t in titles), titles)  # 必吃留到更顺路的晚餐
        self.assertEqual(plan.unmet_must_eat, [])


if __name__ == "__main__":
    unittest.main()


# ---------- 第二轮测评（修复后重跑）暴露的问题 ----------

GZ_HOTEL = LocationPoint(name="北京路步行街", lng=113.268359, lat=23.122109)


def _gz(name, lng, lat, minutes, period=""):
    return Spot(name=name, lng=lng, lat=lat, duration_minutes=minutes, best_period=period,
                open_time="周一至周日 07:30-22:30")


class TestTwoEveningSpotsSameArea(unittest.TestCase):
    """广州：永庆坊、大佛寺都标了傍晚。晚上只放得下一个，另一个曾因排不进晚上被丢掉。"""

    def test_no_evening_spot_dropped(self):
        spots = [
            _gz("陈家祠堂", 113.245158, 23.126692, 120), _gz("沙面岛", 113.244707, 23.106802, 120),
            _gz("永庆坊", 113.2436, 23.1220, 120, "evening"), _gz("大佛寺", 113.268027, 23.122373, 90, "evening"),
            _gz("花城广场", 113.324593, 23.120498, 90), _gz("广州塔", 113.324521, 23.106428, 150, "evening"),
        ]
        plan = TripScheduler("广州", 2, GZ_HOTEL, start_date="2026-10-22").schedule(spots, [])
        self.assertEqual(plan.unassigned_spots, [])
        for d in plan.days:
            night = [it for it in d.items if it.category == "spot" and "宜傍晚/夜间" in "".join(it.warnings)]
            self.assertLessEqual(sum(it.location.duration_minutes for it in night), 175, [it.title for it in night])
        demoted = [it for _, it in _spot_items(plan) if any("改为白天" in w for w in it.warnings)]
        self.assertTrue(demoted)


class TestBalanceIgnoresUnplaceableSpot(unittest.TestCase):
    """上海：迪士尼（540 分钟）排不进，却被算进「平均负荷」，导致一天塞 4 个、另一天空 5 小时。"""

    def test_days_balanced(self):
        hotel = LocationPoint(name="人民广场", lng=121.4737, lat=31.2304)
        spots = [Spot(name="迪士尼", lng=121.6573, lat=31.1440, duration_minutes=540, best_period="morning",
                      open_time="周一至周日 08:30-21:30")]
        for i, (lng, lat) in enumerate([(121.4920, 31.2272), (121.4669, 31.2101), (121.4381, 31.2056),
                                        (121.4762, 31.2353), (121.4997, 31.2397), (121.4906, 31.2400)]):
            spots.append(Spot(name=f"市区景点{i}", lng=lng, lat=lat, duration_minutes=90 if i else 120,
                              open_time="周一至周日 00:00-24:00"))
        plan = TripScheduler("上海", 2, hotel, start_date="2026-10-23", pace="compact").schedule(spots, [])
        counts = [d.total_spots for d in plan.days]
        self.assertEqual(sorted(counts), [3, 3], counts)


class TestDepartureDayStationLeg(unittest.TestCase):
    """北京：南锣鼓巷（白天 150 分钟）与天坛排进返程日，按「到站固定 30 分钟」估算放得下，实际赶不上车。"""

    def test_all_spots_fit_with_real_station_leg(self):
        spots = _beijing_spots()
        spots[3] = Spot(name="南锣鼓巷", lng=116.402394, lat=39.937182, duration_minutes=150,
                        open_time="周一至周日 00:00-24:00")  # 模型去掉了「傍晚」标记
        plan = TripScheduler("北京", 3, WANGFUJING, departure_point=BEIJING_SOUTH, start_date="2026-10-28",
                             departure_time="18:00").schedule(spots, [])
        self.assertEqual(plan.unassigned_spots, [])


class TestAmapQpsRetry(unittest.TestCase):
    """第三轮测评：高德返回 10022（CGQPS_HAS_EXCEEDED_THE_LIMIT），旧代码只对 10021 重试，检索直接失败。"""

    def _run(self, responses):
        from unittest import mock
        import app._data.amap as amap

        class Resp:
            def __init__(self, data): self._d = data
            def raise_for_status(self): pass
            def json(self): return self._d

        seq = iter(responses)
        with mock.patch.dict("os.environ", {"AMAP_API_KEY": "test"}), \
                mock.patch.object(amap.httpx, "get", side_effect=lambda *a, **k: Resp(next(seq))), \
                mock.patch.object(amap.time, "sleep"):
            return amap.amap_get("/place/text", {"keywords": "x"})

    def test_10022_is_retried(self):
        out = self._run([{"status": "0", "info": "CGQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10022"},
                         {"status": "1", "pois": []}])
        self.assertEqual(out, {"status": "1", "pois": []})

    def test_persistent_qps_error_says_rate_limited(self):
        err = {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10021"}
        out = self._run([err, err, err])
        self.assertIn("限流", out["error"])
        self.assertIn("不代表查不到", out["error"])

    def test_other_errors_not_retried(self):
        out = self._run([{"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}])
        self.assertIn("INVALID_USER_KEY", out["error"])
