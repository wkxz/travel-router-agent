"""2026-10-22 修复的 5 个问题的回归测试：
1. Agent 运行轮数上限（SDK 默认 10 轮不够）
2. 提示词注入当天日期；排程默认日期按北京时间
3. 必吃优先安排，排不进时如实给出原因
4. 通勤时间接入高德路网（/v3/distance），取不到时回退估算并标注
5. 关键词搜索限定城市（citylimit）

运行：./.venv/Scripts/python.exe -m unittest tests.test_top5_fixes -v
"""

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from tests.fake_amap import FakeAmap

import app.agent as chat  # noqa: E402
import app._tools as tools  # noqa: E402
import app._data.poi_repo as poi_repo  # noqa: E402
from app._data.routing import AmapTravel, MAX_ORIGINS, MAX_REQUESTS  # noqa: E402
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler, TravelTimes, render_dict  # noqa: E402
from app.engine.validator import validate_plan  # noqa: E402
from agents import MaxTurnsExceeded, RunContextWrapper  # noqa: E402
from tests.test_tools import call  # noqa: E402

UTC8 = timezone(timedelta(hours=8))
HOTEL = LocationPoint(name="新街口", lng=118.7844, lat=32.0416)
NEAR_SPOT = dict(name="总统府", lng=118.7967, lat=32.0450)  # 距 HOTEL 约 1.2 km


def _offset(p: LocationPoint, km_east: float) -> tuple:
    """在 p 正东 km_east 公里处的经纬度。"""
    import math
    return p.lng + km_east / (111.32 * math.cos(math.radians(p.lat))), p.lat


# ---------- 1. 轮数上限 ----------

class _RaisingStream:
    async def stream_events(self):
        raise MaxTurnsExceeded("Max turns (30) exceeded")
        yield  # pragma: no cover


class TestMaxTurns(unittest.TestCase):
    def test_runner_gets_raised_max_turns(self):
        fake = SimpleNamespace(stream_events=lambda: _empty())
        with mock.patch.object(chat.Runner, "run_streamed", return_value=fake) as run:
            asyncio.run(_collect(chat._event_stream("去南京")))
        self.assertEqual(run.call_args.kwargs["max_turns"], chat.MAX_TURNS)
        self.assertGreaterEqual(chat.MAX_TURNS, 30)

    def test_max_turns_exceeded_is_explained(self):
        with mock.patch.object(chat.Runner, "run_streamed", return_value=_RaisingStream()):
            frames = asyncio.run(_collect(chat.chat_events("去南京")))
        err = next(f for f in frames if f.startswith("event: error"))
        self.assertIn("轮上限", err)
        self.assertTrue(frames[-1].startswith("event: done"))


async def _empty():
    if False:
        yield


async def _collect(agen):
    return [x async for x in agen]


# ---------- 2. 当前日期 ----------

class TestTodayInPrompt(unittest.TestCase):
    def test_today_line_has_date_and_weekday(self):
        line = chat.today_line(datetime(2026, 10, 26, 9, 0, tzinfo=UTC8))
        self.assertIn("2026-10-26", line)
        self.assertIn("周一", line)

    def test_system_prompt_is_built_per_request_with_today(self):
        self.assertTrue(callable(chat.agent.instructions))
        prompt = asyncio.run(chat.agent.get_system_prompt(RunContextWrapper(context=None)))
        self.assertIn(datetime.now(UTC8).strftime("%Y-%m-%d"), prompt)
        self.assertIn("【分工原则】", prompt)  # 原有提示词仍在

    def test_scheduler_default_date_is_beijing_today(self):
        self.assertEqual(TripScheduler("南京", 1, HOTEL).start_date, datetime.now(UTC8).date())


# ---------- 3. 必吃 ----------

def _rest(name, km_east, dish="", rating=4.5, meal_type="meal"):
    lng, lat = _offset(HOTEL, km_east)
    return Restaurant(name=name, lng=lng, lat=lat, meal_type=meal_type, rating=rating,
                      cost=50.0, cuisine="中餐厅", dish_key=dish, open_time="10:00-22:00")


class TestMustEat(unittest.TestCase):
    def _plan(self, restaurants, must_eat):
        spot = Spot(**NEAR_SPOT, duration_minutes=120, open_time="08:00-18:00")
        return TripScheduler("南京", 1, HOTEL, start_date="2026-10-27").schedule([spot], restaurants, must_eat=must_eat)

    def _titles(self, plan):
        return [it.title for d in plan.days for it in d.items]

    def test_pending_must_eat_beats_nearer_higher_rated(self):
        # 旧逻辑只给必吃 +3 分：近 0.2km、评分 4.9 的普通店会把 2km 外的必吃挤掉
        rs = [_rest("普通好店A", 1.2, rating=4.9), _rest("普通好店B", 1.3, rating=4.9),
              # 2.2km：晚餐绕路约 2km，在最后一顿 3km 的上限内（3km 外绕路 3.6km，按新规则交给缺口清单）
              _rest("鸭血粉丝老店", 2.2, dish="鸭血粉丝", rating=4.0)]
        plan = self._plan(rs, ["鸭血粉丝"])
        self.assertTrue(any("鸭血粉丝老店" in t for t in self._titles(plan)), self._titles(plan))
        self.assertEqual(plan.unmet_must_eat, [])

    def test_unmet_when_never_searched(self):
        plan = self._plan([_rest("普通好店", 1.2)], ["螺蛳粉"])
        self.assertEqual(len(plan.unmet_must_eat), 1)
        self.assertIn("螺蛳粉", plan.unmet_must_eat[0])
        self.assertIn("需先用该美食名检索", plan.unmet_must_eat[0])

    def test_unmet_when_too_far_names_nearest_shop(self):
        plan = self._plan([_rest("普通好店", 1.2), _rest("远方火锅", 12.0, dish="火锅")], ["火锅"])
        self.assertEqual(len(plan.unmet_must_eat), 1)
        self.assertIn("远方火锅", plan.unmet_must_eat[0])
        self.assertIn("超过 2.5 公里", plan.unmet_must_eat[0])

    def test_unmet_is_rendered(self):
        plan = self._plan([_rest("普通好店", 1.2)], ["螺蛳粉"])
        self.assertEqual(render_dict(plan)["unmet_must_eat"], plan.unmet_must_eat)


# ---------- 4. 高德路网 ----------

class TestAmapTravel(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAmap()
        self.near = LocationPoint("近处", *_offset(HOTEL, 0.8))
        self.far = LocationPoint("远处", *_offset(HOTEL, 6.0))

    def test_short_leg_uses_walking_network(self):
        travel = AmapTravel([HOTEL, self.near], fetch=self.fake)
        leg = travel.leg(HOTEL, self.near)
        self.assertFalse(leg.estimated)
        self.assertIn("步行", leg.desc)
        self.assertNotIn("估算", leg.desc)
        params = self.fake.calls[0][1]
        self.assertEqual((self.fake.calls[0][0], params["type"]), ("/distance", "3"))
        # 仿真接口：0.8km×1.4 路网 / 1.25 m/s ≈ 15 分钟；直线估算是 0.8×1.3×13.3 ≈ 14 分钟
        self.assertEqual(leg.minutes, 15)

    def test_long_leg_skips_walking_request(self):
        travel = AmapTravel([HOTEL, self.far], fetch=self.fake)
        leg = travel.leg(HOTEL, self.far)
        self.assertIn("打车", leg.desc)
        self.assertEqual([p["type"] for _, p in self.fake.calls], ["1"])  # 直线已超步行上限，不再查步行

    def test_column_is_reused_for_same_destination(self):
        travel = AmapTravel([HOTEL, self.near, self.far], fetch=self.fake)
        travel.leg(self.far, HOTEL)
        travel.leg(self.near, HOTEL, mode="driving")
        travel.leg(self.far, HOTEL)  # 同一段第二次查询命中缓存
        self.assertEqual(len(self.fake.calls), 1)

    def test_origins_batched_at_100(self):
        pts = [LocationPoint(f"p{i}", *_offset(HOTEL, 3 + i * 0.01)) for i in range(150)]
        travel = AmapTravel([HOTEL] + pts, fetch=self.fake)
        travel.leg(pts[120], HOTEL)
        origins = self.fake.calls[0][1]["origins"].split("|")
        self.assertEqual(len(origins), MAX_ORIGINS)
        self.assertEqual(origins[0], pts[120].location_str)  # 被查询的起点一定在本批里

    def test_error_falls_back_to_marked_estimate(self):
        travel = AmapTravel([HOTEL, self.far], fetch=lambda path, params: {"error": "高德接口限流"})
        leg = travel.leg(HOTEL, self.far)
        self.assertTrue(leg.estimated)
        self.assertIn("估算", leg.desc)
        self.assertEqual(leg.minutes, TravelTimes().estimate(HOTEL, self.far).minutes)
        self.assertTrue(any("高德接口限流" in n for n in travel.notes()))

    def test_request_cap(self):
        pts = [LocationPoint(f"p{i}", *_offset(HOTEL, 3 + i)) for i in range(MAX_REQUESTS + 5)]
        travel = AmapTravel([HOTEL] + pts, fetch=self.fake)
        for p in pts:
            travel.leg(HOTEL, p)
        self.assertEqual(len(self.fake.calls), MAX_REQUESTS)
        self.assertTrue(travel.notes())

    def test_scheduler_and_validator_use_network_times(self):
        travel = AmapTravel([HOTEL], fetch=self.fake)
        spot = Spot(**NEAR_SPOT, duration_minutes=90, open_time="08:00-18:00")
        plan = TripScheduler("南京", 1, HOTEL, start_date="2026-10-27", travel=travel).schedule([spot], [])
        first = next(it for it in plan.days[0].items if it.category == "spot")
        self.assertEqual(first.transit_info, travel.leg(HOTEL, spot).desc)
        self.assertEqual([i for i in validate_plan(plan, travel=travel) if i.level == "error"], [])


# ---------- 4+5. 工具层 ----------

class TestToolLayer(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAmap()
        for target in (mock.patch.object(tools, "amap_get", self.fake),
                       mock.patch.object(poi_repo, "amap_get", self.fake)):
            target.start()
            self.addCleanup(target.stop)
        self.ctx = poi_repo.TripContext()

    def test_text_search_is_limited_to_city(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="西湖")
        self.assertEqual(self.fake.calls[-1][0], "/place/text")
        self.assertEqual(self.fake.calls[-1][1]["citylimit"], "true")

    def test_around_search_has_no_citylimit(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="南京博物院")
        call(tools.search_food_and_snacks, self.ctx, city="南京", keywords="面馆", near_id="B0MUSEUM")
        self.assertEqual(self.fake.calls[-1][0], "/place/around")
        self.assertNotIn("citylimit", self.fake.calls[-1][1])

    def test_itinerary_uses_road_network(self):
        hotel = call(tools.geocode_location, self.ctx, address="新街口", city="南京")["id"]
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="玄武湖")
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0LAKE"]', foods="[]", hotel_id=hotel, start_date="2026-10-27")
        self.assertTrue(res["success"], res)
        self.assertIn("/distance", self.fake.paths())
        infos = [it["transit_info"] for d in res["plan"]["days"] for it in d["items"] if it["transit_info"]]
        self.assertTrue(infos)
        self.assertFalse(any("估算" in t for t in infos), infos)

    def test_must_eat_unmet_reaches_tool_output(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="玄武湖")
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0LAKE"]', foods="[]", must_eat="螺蛳粉", start_date="2026-10-27")
        self.assertTrue(res["success"], res)
        self.assertTrue(res["plan"]["unmet_must_eat"][0].startswith("螺蛳粉"), res["plan"])


if __name__ == "__main__":
    unittest.main()
