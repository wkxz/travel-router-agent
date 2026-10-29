"""大型景区按门区算通勤，不用几何中心点。

2026-10-29 高德实测：
- 杭州西湖风景名胜区的 location 在西南山里（120.121,30.223），导航入口 entr_location 在约 3km 外的南山路一侧；
- 玄武湖景区的 location 在东岸（118.8127,32.0695），西侧的玄武门（118.7875,32.0705）相距约 2.4km；
  v5 详情的 children 里有「玄武湖景区(玄武门)」「(解放门)」（typecode 9914xx），也有园内停车场。
按中心点算，从湖西的酒店去玄武湖要绕到东岸，湖西的餐厅也会被当成「离景点 2.5km 以外」而不选。
"""

import unittest

from tests.fake_amap import FakeAmap, SPOTS
from app._data.poi_repo import PoiRepo, gate_label, gates_from_children, merge_gates, record_from_poi
from app._data.routing import AmapTravel
from app._data.trip_input import build_trip_inputs
from app.engine import LocationPoint, Restaurant, Spot, TravelTimes, TripScheduler
from app.engine.geo import gate_pair, place_distance

XUANWU_GATE = LocationPoint("玄武门", 118.787506, 32.070519)
JIEFANG_GATE = LocationPoint("解放门", 118.796638, 32.062228)


def lake(with_gates=True):
    return Spot(name="玄武湖景区", lng=118.812688, lat=32.069455, duration_minutes=150,
                open_time="00:00-24:00", gates=[XUANWU_GATE, JIEFANG_GATE] if with_gates else [])


HOTEL_WEST = LocationPoint("湖南路酒店", 118.7830, 32.0690)


class TestGatePair(unittest.TestCase):
    def test_nearest_gate_is_used(self):
        x, y = gate_pair(HOTEL_WEST, lake())
        self.assertIs(x, HOTEL_WEST)
        self.assertIs(y, XUANWU_GATE)
        self.assertLess(place_distance(HOTEL_WEST, lake()), 0.6)
        self.assertGreater(place_distance(HOTEL_WEST, lake(with_gates=False)), 2.5)

    def test_exit_gate_towards_next_stop(self):
        south = Restaurant(name="解放门外餐厅", lng=118.7960, lat=32.0605)
        self.assertIs(gate_pair(lake(), south)[0], JIEFANG_GATE)

    def test_leg_names_the_gates(self):
        leg = TravelTimes().leg(HOTEL_WEST, lake())
        self.assertIn("从「玄武门」进", leg.desc)
        back = TravelTimes().leg(lake(), HOTEL_WEST)
        self.assertIn("从「玄武门」出", back.desc)

    def test_unnamed_entrance_moves_point_without_note(self):
        west_lake = Spot(name="杭州西湖风景名胜区", lng=120.121358, lat=30.222692,
                         gates=[LocationPoint("", 120.153345, 30.224729)])
        hotel = LocationPoint("南山路酒店", 120.1560, 30.2260)
        leg = TravelTimes().leg(hotel, west_lake)
        self.assertNotIn("「", leg.desc)
        self.assertLess(leg.road_km, 1.0)

    def test_amap_requests_gate_coordinates(self):
        fake = FakeAmap()
        spot = lake()
        travel = AmapTravel([HOTEL_WEST, spot], fetch=fake)
        leg = travel.leg(HOTEL_WEST, spot)
        destinations = {p["destination"] for path, p in fake.calls if path == "/distance"}
        self.assertEqual(destinations, {XUANWU_GATE.location_str})
        self.assertIn("从「玄武门」进", leg.desc)
        self.assertFalse(leg.estimated)


class TestSchedulerUsesGates(unittest.TestCase):
    def _lunch(self, spot):
        hotel = LocationPoint("新街口酒店", 118.7844, 32.0416)
        # 玄武门外 0.3km；离景区中心点 2.6km（> 2.5km 的就近上限），也不在回新街口的顺路方向上
        rest = Restaurant(name="玄武门外面馆", lng=118.7840, lat=32.0712, meal_type="meal", rating=4.5,
                          open_time="10:00-21:00")
        plan = TripScheduler("南京", 1, hotel, start_date="2026-11-03").schedule([spot], [rest])
        return [i.title for d in plan.days for i in d.items if i.category == "meal"]

    def test_restaurant_near_gate_counts_as_nearby(self):
        self.assertTrue(any("玄武门外面馆" in t for t in self._lunch(lake())))

    def test_control_without_gates_does_not_pick_it(self):
        # 对照：同样的数据不带门区时，这家店被当成太远——说明上面的用例确实依赖门区
        self.assertFalse(any("玄武门外面馆" in t for t in self._lunch(lake(with_gates=False))))


class TestGateData(unittest.TestCase):
    def test_entr_location_becomes_unnamed_gate(self):
        rec = record_from_poi({**SPOTS["B0LAKE"], "entr_location": "118.813101,32.069758"})
        self.assertEqual(rec.gates, [("", 118.813101, 32.069758)])
        self.assertEqual(record_from_poi({**SPOTS["B0LAKE"], "entr_location": []}).gates, [])

    def test_children_filter_and_labels(self):
        parent = record_from_poi(SPOTS["B0LAKE"])
        children = [
            {"name": "玄武湖景区(玄武门)", "typecode": "991401", "location": "118.787506,32.070519"},
            {"name": "玄武湖公园内部停车场", "typecode": "150905", "location": "118.806768,32.075273"},
            {"name": "玄武湖景区东门售票处", "typecode": "070306", "location": "118.80,32.07"},
            {"name": "玄武湖景区售票处", "typecode": "070306", "location": "118.80,32.071"},
            {"name": "远处的门", "typecode": "991400", "location": "118.90,32.20"},
        ]
        self.assertEqual([g[0] for g in gates_from_children(parent, children)], ["玄武门", "东门"])
        self.assertEqual(gate_label("圆明园遗址公园绮春园宫门售票处", "圆明园遗址公园"), "绮春园宫门")

    def test_merge_prefers_named_gate(self):
        merged = merge_gates([("", 118.7876, 32.0706), ("玄武门", 118.787506, 32.070519), ("解放门", 118.7966, 32.0622)])
        self.assertEqual([g[0] for g in merged], ["玄武门", "解放门"])

    def test_build_inputs_fetches_gates_once_for_scenic_only(self):
        fake = FakeAmap()
        repo = PoiRepo(fetch=fake)
        inputs = build_trip_inputs(repo, '["B0LAKE", "B0MUSEUM"]', "[]")
        build_trip_inputs(repo, '["B0LAKE"]', "[]")
        lake_spot = next(s for s in inputs.spots if s.poi_id == "B0LAKE")
        self.assertEqual([g.name for g in lake_spot.gates], ["玄武门", "解放门"])
        museum = next(s for s in inputs.spots if s.poi_id == "B0MUSEUM")
        self.assertEqual(museum.gates, [])
        v5 = [p["id"] for path, p in fake.calls if path == "/v5/place/detail"]
        self.assertEqual(v5, ["B0LAKE"])  # 博物馆不是风景名胜类，不查；同一景点只查一次

    def test_v5_failure_falls_back_to_entrance(self):
        def fetch(path, params):
            if path == "/v5/place/detail":
                return {"error": "高德接口返回错误：USERKEY_PLAT_NOMATCH（infocode=10009）"}
            return FakeAmap()(path, params)
        repo = PoiRepo(fetch=fetch)
        repo.put(record_from_poi({**SPOTS["B0LAKE"], "entr_location": "118.813101,32.069758"}))
        spot = build_trip_inputs(repo, '["B0LAKE"]', "[]").spots[0]
        self.assertEqual([(g.lng, g.lat) for g in spot.gates], [(118.813101, 32.069758)])


if __name__ == "__main__":
    unittest.main()
