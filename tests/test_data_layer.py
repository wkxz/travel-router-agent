"""数据层测试：POI 仓库与 id 引用解析（不依赖 OpenAI Agents SDK，不发网络请求）。

运行：./.venv/Scripts/python.exe -m unittest tests.test_data_layer -v
"""

import unittest

from tests.fake_amap import FakeAmap, SPOTS, FOODS
from app._data.poi_repo import PoiRepo, record_from_poi, geo_id
from app._data.trip_input import build_trip_inputs, parse_refs


class TestPoiRecord(unittest.TestCase):
    def test_prefers_opentime2_with_weekdays(self):
        # 实测：南京博物院 open_time 只有「09:00-17:00」，周一闭馆信息只在 opentime2 里
        rec = record_from_poi(SPOTS["B0MUSEUM"])
        self.assertIn("周一 闭馆", rec.open_time)

    def test_missing_biz_fields_are_none(self):
        rec = record_from_poi(FOODS["B0SNACK"])
        self.assertIsNone(rec.rating)
        self.assertEqual(rec.cost, 12.0)

    def test_brief_has_no_coordinates(self):
        brief = record_from_poi(SPOTS["B0LAKE"]).brief()
        self.assertFalse({"lng", "lat", "location"} & set(brief), brief)
        self.assertEqual(brief["id"], "B0LAKE")


class TestPoiRepo(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAmap()
        self.repo = PoiRepo(fetch=self.fake)

    def test_hit_does_not_fetch(self):
        self.repo.put(record_from_poi(SPOTS["B0LAKE"]))
        rec, _ = self.repo.get("B0LAKE")
        self.assertEqual(rec.name, "玄武湖景区")
        self.assertEqual(self.fake.calls, [])

    def test_miss_falls_back_to_detail_once(self):
        rec, err = self.repo.get("B0MUSEUM")
        self.assertEqual((rec.name, err), ("南京博物院", ""))
        self.repo.get("B0MUSEUM")
        self.assertEqual(self.fake.paths(), ["/place/detail"])

    def test_fabricated_id_yields_error_not_data(self):
        rec, err = self.repo.get("B0FAKE123")
        self.assertIsNone(rec)
        self.assertIn("不存在", err)

    def test_geo_id_regeocodes(self):
        rec, _ = self.repo.get(geo_id("南京南站", "南京"))
        self.assertEqual((round(rec.lng, 4), round(rec.lat, 4)), (118.7983, 31.9696))
        self.assertEqual(self.fake.paths(), ["/geocode/geo"])

    def test_put_keeps_meal_type_from_earlier_search(self):
        self.repo.put(record_from_poi(FOODS["B0DUCK"], meal_type="snack", search_keyword="鸭血粉丝"))
        self.repo.put(record_from_poi(FOODS["B0DUCK"]))  # 之后被别的检索再次命中
        rec, _ = self.repo.get("B0DUCK")
        self.assertEqual((rec.meal_type, rec.search_keyword), ("snack", "鸭血粉丝"))


class TestParseRefs(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_refs('["A", "B"]'), [{"id": "A"}, {"id": "B"}])
        self.assertEqual(parse_refs('[{"id": "A", "best_period": "morning"}]'), [{"id": "A", "best_period": "morning"}])
        self.assertEqual(parse_refs("A, B，C"), [{"id": "A"}, {"id": "B"}, {"id": "C"}])
        self.assertEqual(parse_refs('{"id": "A"}'), [{"id": "A"}])
        self.assertEqual(parse_refs(""), [])
        self.assertEqual(parse_refs('[{"name": "没有 id"}, ""]'), [])


class TestBuildTripInputs(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAmap()
        self.repo = PoiRepo(fetch=self.fake)

    def test_model_supplied_facts_are_ignored(self):
        # 模型在引用里夹带名称、坐标、评分，一律不采信，事实数据只取自仓库
        out = build_trip_inputs(self.repo, '[{"id": "B0MUSEUM", "name": "假名字", "lng": 0, "lat": 0, '
                                           '"rating": 5, "open_time": "00:00-24:00"}]', "[]")
        s = out.spots[0]
        self.assertEqual((s.name, s.lng, s.lat, s.rating), ("南京博物院", 118.8232, 32.041, 4.9))
        self.assertIn("周一 闭馆", s.open_time)

    def test_judgement_fields_are_used_and_sanitised(self):
        out = build_trip_inputs(self.repo, '[{"id": "B0MUSEUM", "duration_minutes": 9999, "best_period": "noon"},'
                                           ' {"id": "B0LAKE", "best_period": "morning"}]', "[]")
        museum, lake = out.spots
        self.assertEqual((museum.duration_minutes, museum.best_period), (600, ""))
        self.assertEqual((lake.duration_minutes, lake.best_period), (120, "morning"))

    def test_unknown_spot_is_reported(self):
        out = build_trip_inputs(self.repo, '["B0LAKE", "B0FAKE"]', "[]")
        self.assertEqual([s.name for s in out.spots], ["玄武湖景区"])
        self.assertTrue(out.skipped and out.skipped[0].startswith("B0FAKE"), out.skipped)

    def test_unknown_hotel_raises(self):
        with self.assertRaises(ValueError):
            build_trip_inputs(self.repo, '["B0LAKE"]', "[]", hotel_id=geo_id("不存在的酒店"))

    def test_food_meal_type_and_must_eat(self):
        self.repo.put(record_from_poi(FOODS["B0DUCK"], meal_type="meal", search_keyword="鸭血粉丝"))
        out = build_trip_inputs(self.repo, "[]", '[{"id": "B0DUCK", "meal_type": "snack"}, "B0SNACK", "B0SNACK"]',
                                must_eat="鸭血粉丝，梅花糕")
        duck, snack = out.restaurants
        self.assertEqual((duck.meal_type, duck.dish_key), ("snack", "鸭血粉丝"))
        self.assertEqual((snack.meal_type, snack.dish_key), ("meal", "梅花糕"))  # 未经检索的回查结果默认正餐
        self.assertEqual(len(out.restaurants), 2)  # 重复 id 去重


if __name__ == "__main__":
    unittest.main()
