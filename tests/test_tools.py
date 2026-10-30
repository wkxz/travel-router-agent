"""工具层端到端测试：经 OpenAI Agents SDK 的公开接口（ToolContext + on_invoke_tool）调用，
覆盖参数 schema 解析与 run context 注入；高德请求由 FakeAmap 替身应答，不发网络请求。

运行：./.venv/Scripts/python.exe -m unittest tests.test_tools -v
"""

import asyncio
import json
import unittest
from unittest import mock

from tests.fake_amap import FakeAmap, MONDAY
from agents.tool_context import ToolContext  # OpenAI Agents SDK
import app._tools as tools
import app._data.poi_repo as poi_repo
from app._data.poi_repo import TripContext, PoiRepo


def call(tool, context: TripContext, **kwargs) -> dict:
    """调用工具并解析输出。排程工具给模型的只有精简版（itinerary）和编号 plan_no，
    这里按编号从上下文取出推给前端的完整行程放进 res["plan"]，方便断言；精简版本身见
    test_model_sees_compact_view_frontend_gets_full_plan。"""
    args = json.dumps(kwargs, ensure_ascii=False)
    tc = ToolContext(context=context, tool_name=tool.name, tool_call_id="t1", tool_arguments=args)
    res = json.loads(asyncio.run(tool.on_invoke_tool(tc, args)))
    if isinstance(res, dict) and "plan_no" in res:
        res["plan"] = tools.extract_plan(res, context)
    return res


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAmap()
        for target in (mock.patch.object(tools, "amap_get", self.fake),
                       mock.patch.object(poi_repo, "amap_get", self.fake)):
            target.start()
            self.addCleanup(target.stop)
        self.ctx = TripContext()


class TestSearchTools(ToolTestCase):
    def test_named_place_outside_spot_types_found(self):
        """2026-10-29 南京实测：先锋书店属于书店类目，按景点类目检索只返回名字不相干的「骏惠书屋」，模型拿它顶替。"""
        res = call(tools.search_scenic_spots, self.ctx, city="南京", keywords="先锋书店")
        self.assertIn("先锋书店", res["spots"][0]["name"], res)
        self.assertTrue(self.ctx.repo.get(res["spots"][0]["id"])[0])

    def test_category_search_not_widened(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="博物院")
        self.assertEqual(len([p for p, _ in self.fake.calls if p == "/place/text"]), 1)

    def test_spot_search_returns_ids_without_coordinates(self):
        res = call(tools.search_scenic_spots, self.ctx, city="南京", keywords="南京博物院")
        raw = json.dumps(res, ensure_ascii=False)
        self.assertEqual(res["spots"][0]["id"], "B0MUSEUM")
        for leak in ('"lng"', '"lat"', '"location"', "118.8"):
            self.assertNotIn(leak, raw)

    def test_spot_search_includes_museum_category(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="南京博物院")
        self.assertIn("140000", self.fake.calls[0][1]["types"])

    def test_food_search_near_spot_uses_repo_coordinates(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="南京博物院")
        call(tools.search_food_and_snacks, self.ctx, city="南京", keywords="鸭血粉丝", near_id="B0MUSEUM")
        path, params = self.fake.calls[-1]
        self.assertEqual((path, params["location"]), ("/place/around", "118.823200,32.041000"))

    def test_unknown_near_id_is_an_error(self):
        res = call(tools.search_food_and_snacks, self.ctx, city="南京", keywords="面馆", near_id="B0FAKE")
        self.assertIn("无法识别", res["error"])


class TestItineraryTool(ToolTestCase):
    def _prepare(self, ctx):
        hotel = call(tools.geocode_location, ctx, address="新街口", city="南京")["id"]
        station = call(tools.geocode_location, ctx, address="南京南站", city="南京")["id"]
        call(tools.search_scenic_spots, ctx, city="南京", keywords="南京博物院")
        call(tools.search_food_and_snacks, ctx, city="南京", keywords="鸭血粉丝", near_id="B0MUSEUM")
        return hotel, station

    def test_full_flow(self):
        hotel, station = self._prepare(self.ctx)
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=2,
                   spots='[{"id": "B0MUSEUM", "duration_minutes": 180}, "B0LAKE"]',
                   foods='["B0DUCK", "B0SNACK"]', hotel_id=hotel, departure_id=station,
                   start_date=MONDAY, departure_time="17:30", must_eat="鸭血粉丝")
        self.assertTrue(res["success"], res)
        days = res["plan"]["days"]
        titles = {d["date"]: [i["title"] for i in d["items"]] for d in days}
        # 周一闭馆（信息来自 opentime2）→ 博物院只能排在周二
        self.assertFalse(any("南京博物院" in t for t in titles["2026-10-26"]), titles)
        self.assertTrue(any("南京博物院" in t for t in titles["2026-10-27"]), titles)
        self.assertEqual(sum("鸭血粉丝" in t for ts in titles.values() for t in ts), 1, titles)
        self.assertTrue(any("南京南站" in t for t in titles["2026-10-27"]))

    def test_new_request_resolves_ids_from_previous_turn(self):
        hotel, _ = self._prepare(self.ctx)
        fresh = TripContext(repo=PoiRepo())  # 多轮对话：新请求的仓库是空的
        n_before = len(self.fake.calls)
        res = call(tools.generate_trip_itinerary, fresh, city="南京", total_days=1,
                   spots='["B0LAKE"]', foods='["B0SNACK"]', hotel_id=hotel, start_date="2026-10-27")
        self.assertTrue(res["success"], res)
        # 只看 id 回查；/distance 是排程时的路网请求，/v5/place/detail 是景区门区查询，另有测试覆盖
        self.assertEqual(sorted(p for p, _ in self.fake.calls[n_before:] if p not in ("/distance", "/v5/place/detail")),
                         ["/geocode/geo", "/place/detail", "/place/detail"])

    def test_fabricated_spot_reported_as_unassigned(self):
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0LAKE", "B0FAKE"]', foods="[]", start_date="2026-10-27")
        self.assertTrue(res["success"], res)
        self.assertTrue(any(u.startswith("B0FAKE") for u in res["plan"]["unassigned_spots"]), res["plan"])

    def test_infeasible_departure_is_explained(self):
        hotel, station = self._prepare(self.ctx)
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1, spots='["B0LAKE"]',
                   foods="[]", hotel_id=hotel, departure_id=station, departure_time="00:30")
        self.assertFalse(res["success"])
        self.assertIn("过早", res["error"])

    def test_bad_json(self):
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1, spots="[{坏", foods="[]")
        self.assertIn("JSON", res["error"])

    def test_model_sees_compact_view_frontend_gets_full_plan(self):
        """给模型的只有时间、标题、提醒与未安排等结论；完整行程（坐标、营业时间、交通…）存在本轮上下文里推给前端。
        第 4 轮测评：排程工具每次返回平均 7.8KB、最大 11KB，12 例共 17 次，回复里却明确要求「不要复述行程表」。"""
        out = asyncio.run(tools.generate_trip_itinerary.on_invoke_tool(
            ToolContext(context=self.ctx, tool_name="generate_trip_itinerary", tool_call_id="t1", tool_arguments="{}"),
            json.dumps(dict(city="南京", total_days=1, spots='["B0LAKE", "B0FAKE"]', foods="[]",
                            start_date="2026-10-27"), ensure_ascii=False)))
        res = json.loads(out)
        for key in ('"lng"', '"lat"', '"poi_id"', '"open_time"', '"transit_info"'):
            self.assertNotIn(key, out)
        day = res["itinerary"]["days"][0]
        self.assertTrue(any("游览【" in x for x in day["items"]), day)
        self.assertTrue(any(u.startswith("B0FAKE") for u in res["itinerary"]["unassigned_spots"]))
        plan = tools.extract_plan(out, self.ctx)
        self.assertEqual(plan["city"], "南京")
        self.assertIn("lng", plan["days"][0]["items"][1]["location"])
        self.assertIsNone(tools.extract_plan(out))  # 没有上下文取不到

    def test_output_is_plan_not_markdown(self):
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0LAKE"]', foods="[]", start_date="2026-10-27")
        self.assertNotIn("markdown", res)
        self.assertIn("不要复述", res["display"])
        self.assertEqual(tools.extract_plan(json.dumps(res, ensure_ascii=False))["city"], "南京")


class TestBranchesAndTips(ToolTestCase):
    """用户只说品牌（「先锋书店」）时，按当天路线换成最顺路的分店；模型的经验建议原样附在景点上。"""

    def _run(self, book_ref):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="玄武湖")
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1, start_date="2026-10-27",
                   spots=json.dumps(["B0LAKE", book_ref], ensure_ascii=False), foods="[]")
        self.assertTrue(res["success"], res)
        return [i for d in res["plan"]["days"] for i in d["items"] if i["category"] == "spot"]

    def test_any_branch_picks_branch_on_route(self):
        spots = self._run({"id": "B0BOOKS", "any_branch": True})
        book = next(i for i in spots if "先锋书店" in i["title"])
        self.assertIn("玄武湖分店", book["title"], spots)
        self.assertTrue(any("分店" in w for w in book["warnings"]), book)

    def test_named_branch_kept_without_flag(self):
        spots = self._run({"id": "B0BOOKS"})
        self.assertTrue(any("五台山总店" in i["title"] for i in spots), spots)
        self.assertNotIn("/place/text", [p for p, a in self.fake.calls if a.get("keywords") == "先锋书店"])

    def test_tips_attached_and_capped(self):
        tip = "需在小程序提前预约。\n周一闭馆" + "很长" * 200
        spots = self._run({"id": "B0BOOKS", "tips": tip})
        book = next(i for i in spots if "先锋书店" in i["title"])
        self.assertTrue(book["tips"].startswith("需在小程序提前预约。 周一闭馆"), book["tips"])
        self.assertLessEqual(len(book["tips"]), 121)
        lake = next(i for i in spots if "玄武湖" in i["title"])
        self.assertEqual(lake["tips"], "")

    def test_branch_does_not_inherit_tips(self):
        # 2026-10-30 测评：模型给五台山总店写的「地下车库改造」被原样挂到了换成的老门东店上
        spots = self._run({"id": "B0BOOKS", "any_branch": True, "tips": "由地下车库改造"})
        book = next(i for i in spots if "玄武湖分店" in i["title"])
        self.assertEqual(book["tips"], "")


class TestReplanLimit(ToolTestCase):
    """2026-10-30 测评：模型为放下一个点名景点连删带换重排了 6 次、耗时 3 分钟。提示词约束不住，由代码限制。"""

    def _gen(self, ctx, **kw):
        args = dict(city="南京", total_days=1, spots='["B0LAKE"]', foods="[]", start_date="2026-10-27")
        args.update(kw)
        return call(tools.generate_trip_itinerary, ctx, **args)

    def test_third_plan_in_one_reply_refused(self):
        self.assertTrue(self._gen(self.ctx)["success"])
        self.assertTrue(self._gen(self.ctx)["success"])
        third = self._gen(self.ctx)
        self.assertFalse(third["success"])
        self.assertIn("不再重排", third["error"])
        self.assertIsNone(tools.extract_plan(json.dumps(third, ensure_ascii=False)))  # 前端保留上一份行程

    def test_failed_calls_do_not_count(self):
        for _ in range(3):
            self.assertFalse(self._gen(self.ctx, total_days=0)["success"])  # 漏传天数这类参数错误
        self.assertTrue(self._gen(self.ctx)["success"])

    def test_new_reply_starts_fresh(self):
        self._gen(self.ctx), self._gen(self.ctx)
        self.assertTrue(self._gen(TripContext(repo=self.ctx.repo))["success"])  # 下一轮对话是新的 TripContext


class TestMobility(ToolTestCase):
    """同行有老人 / 行动不便：步行上限收紧、节奏不超过均衡；同一会话后续轮次模型漏传时沿用。"""

    def _plan(self, ctx, **kw):
        args = dict(city="南京", total_days=1, spots='["B0LAKE", "B0MUSEUM"]', foods="[]",
                    start_date="2026-10-27", pace="compact")
        args.update(kw)
        res = call(tools.generate_trip_itinerary, ctx, **args)
        self.assertTrue(res["success"], res)
        return res["plan"]

    def test_limited_mobility_applied_and_remembered(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="南京博物院")
        plan = self._plan(self.ctx, mobility="limited")
        self.assertTrue(any("行动不便" in n for n in plan["notes"]), plan["notes"])
        nxt = TripContext(repo=self.ctx.repo)  # 同一会话的下一轮，模型没再传 mobility
        plan = self._plan(nxt)
        self.assertTrue(any("行动不便" in n for n in plan["notes"]), plan["notes"])
        plan = self._plan(TripContext(repo=self.ctx.repo), mobility="normal")  # 用户说不用照顾了
        self.assertFalse(any("行动不便" in n for n in plan["notes"]), plan["notes"])


class TestExtractPlan(unittest.TestCase):
    def test_rejects_non_plans(self):
        for output in (None, "", "不是 JSON", '{"success": false, "error": "x"}',
                       '{"success": true, "plan": {"kind": "other"}}', '{"count": 1, "spots": []}'):
            self.assertIsNone(tools.extract_plan(output), output)



if __name__ == "__main__":
    unittest.main()
