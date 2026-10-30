"""P1：忌口 / 预算交给引擎、无预算时高价降权、排程返回结构化缺口清单。

背景（2026-10-22/23 真实测评）：
- 「成都 2 天，完全不能吃辣，人均 80 以内」：引擎不认识忌口和预算，模型只能反复删减 foods 重排，5 次、255~273 秒；
- 没给预算时选中了人均 ¥331、¥406 的店；
- 排程只告诉模型「周边自选」，模型不知道该在哪补检索，只能盲目改参数重排。

运行：./.venv/Scripts/python.exe -m unittest tests.test_p1_constraints -v
"""

import asyncio
import json
import unittest
from unittest import mock

from tests.fake_amap import FakeAmap
import app._tools as tools
import app._data.poi_repo as poi_repo
from app._data.poi_repo import PoiRepo, PoiRecord, TripContext
from app._data.trip_input import build_trip_inputs
from app.engine import LocationPoint, Spot, Restaurant, TripScheduler
from tests.test_tools import call

HOTEL = LocationPoint(name="春熙路", lng=104.078643, lat=30.657952)


def _repo_with(*recs):
    repo = PoiRepo(fetch=lambda path, params: {"status": "1", "pois": []})
    for r in recs:
        repo.put(r)
    return repo


def _rec(i, name, category, cost, kw=""):
    return PoiRecord(id=i, name=name, lng=104.05, lat=30.65, category=category, cost=cost,
                     meal_type="meal", search_keyword=kw)


class TestDietAndBudgetFilter(unittest.TestCase):
    def setUp(self):
        self.repo = _repo_with(
            _rec("A", "蜀大侠火锅(春熙路店)", "餐饮服务;中餐厅;火锅店", 120, "美食"),
            _rec("B", "钟水饺(总店)", "餐饮服务;中餐厅;四川菜(川菜)", 30, "美食"),
            _rec("C", "冒椒火辣", "餐饮服务;中餐厅;中餐厅", 60, "美食"),
            _rec("D", "高级粤菜馆", "餐饮服务;中餐厅;广东菜(粤菜)", 300, "美食"),
            _rec("E", "无价格小馆", "餐饮服务;中餐厅;中餐厅", None, "美食"),
            _rec("F", "老妈蹄花", "餐饮服务;中餐厅;中餐厅", 40, "蹄花"),
        )
        self.foods = json.dumps(list("ABCDEF"))

    def test_avoid_keywords_filter_restaurants(self):
        out = build_trip_inputs(self.repo, "[]", self.foods, avoid="辣,火锅")
        names = [r.name for r in out.restaurants]
        self.assertNotIn("蜀大侠火锅(春熙路店)", names)
        self.assertNotIn("冒椒火辣", names)
        self.assertIn("钟水饺(总店)", names)
        self.assertTrue(any("忌口" in n and "排除了 2 家" in n for n in out.notes), out.notes)

    def test_no_spicy_expands_to_common_dishes(self):
        """2026-10-30 第 4 轮测评（成都 不吃辣）：模型传了「辣,麻辣,火锅,串串,冒菜,...」，
        仍漏过张老二凉粉、冒烤鸭。忌口含「辣」时按常见辣味菜名补全。"""
        repo = _repo_with(_rec("G", "张老二凉粉", "餐饮服务;快餐厅;快餐厅", 15, "美食"),
                          _rec("H", "贺记冒烤鸭(总店)", "餐饮服务;中餐厅;中餐厅", 40, "美食"),
                          _rec("I", "龙抄手(春熙路店)", "餐饮服务;中餐厅;中餐厅", 30, "美食"))
        out = build_trip_inputs(repo, "[]", '["G", "H", "I"]', avoid="辣,麻辣,火锅,串串,冒菜")
        self.assertEqual([r.name for r in out.restaurants], ["龙抄手(春熙路店)"])
        self.assertTrue(any("凉粉" in n and "冒烤鸭" in n for n in out.notes), out.notes)
        # 没说不吃辣时不扩展
        self.assertEqual(len(build_trip_inputs(repo, "[]", '["G", "H", "I"]', avoid="香菜").restaurants), 3)

    def test_budget_filters_and_reports_unknown(self):
        out = build_trip_inputs(self.repo, "[]", self.foods, max_cost_per_meal=80)
        names = [r.name for r in out.restaurants]
        self.assertNotIn("高级粤菜馆", names)
        self.assertNotIn("蜀大侠火锅(春熙路店)", names)
        self.assertIn("无价格小馆", names)  # 没有人均数据：保留但说明
        self.assertTrue(any("人均 ¥80" in n for n in out.notes), out.notes)
        self.assertTrue(any("没有人均数据" in n for n in out.notes), out.notes)

    def test_must_eat_exempt_from_avoid(self):
        """用户点名的必吃不受忌口过滤（如忌口「火锅」但点名要吃某家火锅）。"""
        out = build_trip_inputs(self.repo, "[]", self.foods, avoid="火锅", must_eat="蜀大侠")
        self.assertIn("蜀大侠火锅(春熙路店)", [r.name for r in out.restaurants])

    def test_no_constraints_no_notes(self):
        out = build_trip_inputs(self.repo, "[]", self.foods)
        self.assertEqual(len(out.restaurants), 6)
        self.assertEqual(out.notes, [])


class TestPriceSensitivity(unittest.TestCase):
    def _lunch(self, price_sensitive):
        spot = Spot(name="人民公园", lng=104.0576, lat=30.6570, duration_minutes=120, open_time="06:00-22:00")
        rs = [Restaurant(name="天价餐厅", lng=104.0580, lat=30.6572, meal_type="meal", rating=4.8, cost=406.0,
                         cuisine="粤菜", open_time="10:00-22:00"),
              Restaurant(name="家常馆", lng=104.0585, lat=30.6575, meal_type="meal", rating=4.5, cost=60.0,
                         cuisine="川菜", open_time="10:00-22:00")]
        plan = TripScheduler("成都", 1, HOTEL, start_date="2026-10-23",
                             price_sensitive=price_sensitive).schedule([spot], rs)
        return next(it.title for it in plan.days[0].items if "午餐" in it.title)

    def test_pricey_downweighted_without_budget(self):
        self.assertIn("家常馆", self._lunch(True))

    def test_no_downweight_when_budget_given(self):
        """给了预算（已在输入层过滤）就不再额外压价，按评分距离选。"""
        self.assertIn("天价餐厅", self._lunch(False))


class TestGapsAndNextStep(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAmap()
        for target in (mock.patch.object(tools, "amap_get", self.fake),
                       mock.patch.object(poi_repo, "amap_get", self.fake)):
            target.start()
            self.addCleanup(target.stop)
        self.ctx = TripContext()

    def test_meal_gap_points_to_spot_id(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="玄武湖")
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0LAKE"]', foods="[]", start_date="2026-10-27")
        self.assertTrue(res["success"], res)
        meal_gaps = [g for g in res["gaps"] if g["type"] == "meal_without_candidate"]
        self.assertTrue(meal_gaps, res["gaps"])
        self.assertTrue(any(g["near_id"] == "B0LAKE" for g in meal_gaps), meal_gaps)
        self.assertIn("B0LAKE", res["next_step"])
        self.assertIn("只重排这一次", res["next_step"])
        self.assertNotIn("gaps", res["plan"])  # 缺口清单只给模型，不随行程展示

    def test_must_eat_gap(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="玄武湖")
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0LAKE"]', foods="[]", must_eat="螺蛳粉", start_date="2026-10-27")
        dish = [g for g in res["gaps"] if g["type"] == "must_eat_unmet"]
        self.assertEqual(dish[0]["dish"], "螺蛳粉")
        self.assertIn("keywords='螺蛳粉'", res["next_step"])

    def test_no_gaps_means_stop(self):
        self.assertIn("不要再调用", tools.next_step([]))

    def test_avoid_and_budget_reach_engine(self):
        call(tools.search_scenic_spots, self.ctx, city="南京", keywords="南京博物院")
        call(tools.search_food_and_snacks, self.ctx, city="南京", keywords="鸭血粉丝", near_id="B0MUSEUM")
        res = call(tools.generate_trip_itinerary, self.ctx, city="南京", total_days=1,
                   spots='["B0MUSEUM"]', foods='["B0DUCK", "B0SNACK"]', start_date="2026-10-27",
                   avoid="鸭血", max_cost_per_meal=20)
        self.assertTrue(res["success"], res)
        titles = [it["title"] for d in res["plan"]["days"] for it in d["items"]]
        self.assertFalse(any("鸭血粉丝" in t for t in titles), titles)
        self.assertTrue(any("忌口" in n for n in res["plan"]["notes"]), res["plan"]["notes"])


if __name__ == "__main__":
    unittest.main()


class TestConversationRepoCache(unittest.TestCase):
    def test_same_conversation_shares_repo(self):
        import app.agent as chat
        a1, a2, b = chat.context_for("conv-a"), chat.context_for("conv-a"), chat.context_for("conv-b")
        self.assertIs(a1.repo, a2.repo)
        self.assertIsNot(a1.repo, b.repo)
        self.assertIsNot(chat.context_for(None).repo, chat.context_for(None).repo)

    def test_cache_is_bounded(self):
        import app.agent as chat
        with mock.patch.object(chat, "_REPO_CACHE_MAX", 3), mock.patch.object(chat, "_repo_cache", chat.OrderedDict()):
            first = chat.context_for("c0").repo
            for i in range(1, 5):
                chat.context_for(f"c{i}")
            self.assertEqual(len(chat._repo_cache), 3)
            self.assertIsNot(chat.context_for("c0").repo, first)  # 最旧的已被淘汰
