"""第二轮回归测试：针对成都实测行程暴露的问题（餐厅绕远、连吃火锅、时段偏好、步行阈值）。

运行：./.venv/Scripts/python.exe -m unittest tests.test_engine_p1 -v
"""

import re
import unittest

from tests.test_engine_p0 import _items, _minutes  # 同时完成 route_engine 别名挂载
from route_engine.models import LocationPoint, Spot, Restaurant
from route_engine.scheduler import TripScheduler
from route_engine.geo import estimate_transit

# 成都真实坐标（取自 2026-10-21 实测行程）
CHUNXI = LocationPoint(name="春熙路", lng=104.0815, lat=30.6558)
PANDA = dict(name="成都大熊猫繁育研究基地", lng=104.1448, lat=30.7335, open_time="07:30-17:00")
JINLI = dict(name="锦里古街", lng=104.0518, lat=30.6425, open_time="24小时营业")
WUHOU = dict(name="成都武侯祠博物馆", lng=104.0505, lat=30.6433, open_time="09:00-18:00")


class TestMealDistanceCap(unittest.TestCase):
    """熊猫基地周边没有候选时，不能打车 47 分钟去锦里吃午饭。"""

    def test_far_restaurant_not_chosen(self):
        far = Restaurant(name="炊太婆(锦里店)", lng=104.0520, lat=30.6430, meal_type="meal")
        far_snack = Restaurant(name="严太婆锅魁", lng=104.0670, lat=30.6680, meal_type="snack")
        plan = TripScheduler("成都", 1, CHUNXI).schedule([Spot(**PANDA)], [far, far_snack])
        items = _items(plan)
        titles = [it.title for it in items]
        self.assertFalse(any("炊太婆" in t for t in titles), titles)
        lunch = [it for it in _items(plan, "meal") if "午餐" in it.title][0]
        self.assertIn("周边自选", lunch.title)
        # 严太婆锅魁在春熙路：只允许在回到住处休整之后、就近吃，不能从熊猫基地专程打车过去
        snack_idx = next((i for i, t in enumerate(titles) if "严太婆" in t), None)
        if snack_idx is not None:
            self.assertTrue(any("休整" in t for t in titles[:snack_idx]), titles)
            minutes = int(re.search(r"约 (\d+) 分钟", items[snack_idx].transit_info).group(1))
            self.assertLess(minutes, 20, items[snack_idx].transit_info)  # 原问题是打车 47 分钟

    def test_near_restaurant_still_chosen(self):
        near = Restaurant(name="熊猫基地旁餐厅", lng=104.1460, lat=30.7300, meal_type="meal")
        plan = TripScheduler("成都", 1, CHUNXI).schedule([Spot(**PANDA)], [near])
        self.assertTrue(any("熊猫基地旁餐厅" in it.title for it in _items(plan, "meal")))


class TestCuisineDedup(unittest.TestCase):
    """用户点名的必吃美食（dish_key）全程只排一次；不依赖任何写死的菜名词表。"""

    def test_no_hotpot_twice(self):
        rs = [
            Restaurant(name="翠孃孃老火锅", lng=104.0800, lat=30.6550, meal_type="meal", cuisine="火锅店", rating=4.7,
                       dish_key="火锅"),
            Restaurant(name="五里关火锅", lng=104.0810, lat=30.6560, meal_type="meal", cuisine="火锅店", rating=4.7,
                       dish_key="火锅"),
            Restaurant(name="陈麻婆豆腐", lng=104.0820, lat=30.6570, meal_type="meal", cuisine="川菜", rating=4.5),
            Restaurant(name="钟水饺", lng=104.0830, lat=30.6540, meal_type="meal", cuisine="川菜", rating=4.4),
        ]
        spots = [Spot(name="太古里", lng=104.0830, lat=30.6550, duration_minutes=60),
                 Spot(name="大慈寺", lng=104.0820, lat=30.6565, duration_minutes=60)]
        plan = TripScheduler("成都", 2, CHUNXI).schedule(spots, rs)
        meals = [it.title for it in _items(plan, "meal")]
        self.assertLessEqual(sum("火锅" in t for t in meals), 1, meals)

    def test_any_must_eat_dish_once(self):
        # 不在任何词表里的菜（如螺蛳粉）：点名了就只排一次
        rs = [Restaurant(name=f"螺蛳粉{i}号店", lng=109.41 + 0.001 * i, lat=24.32, meal_type="meal",
                         cuisine="中餐厅", dish_key="螺蛳粉", rating=4.8) for i in range(3)]
        rs.append(Restaurant(name="桂菜馆", lng=109.412, lat=24.321, meal_type="meal", cuisine="广西菜", rating=4.2))
        hotel = LocationPoint(name="柳州某酒店", lng=109.41, lat=24.32)
        spots = [Spot(name="景点A", lng=109.411, lat=24.321, duration_minutes=60)]
        plan = TripScheduler("柳州", 2, hotel).schedule(spots, rs)
        self.assertEqual(sum("螺蛳粉" in it.title for it in _items(plan, "meal")), 1)

    def test_must_eat_scheduled_even_if_lower_rated(self):
        rs = [Restaurant(name="高分餐厅", lng=104.0816, lat=30.6559, meal_type="meal", rating=4.9),
              Restaurant(name="高分餐厅2", lng=104.0817, lat=30.6559, meal_type="meal", rating=4.9),
              Restaurant(name="普通甜水面", lng=104.0818, lat=30.6559, meal_type="meal", rating=4.2, dish_key="甜水面")]
        plan = TripScheduler("成都", 1, CHUNXI).schedule([Spot(name="太古里", lng=104.083, lat=30.655)], rs)
        self.assertTrue(any("甜水面" in it.title for it in _items(plan, "meal")))

    def test_generic_cuisine_not_treated_as_duplicate(self):
        rs = [Restaurant(name=f"中餐馆{i}", lng=104.0815 + 0.001 * i, lat=30.6558, meal_type="meal",
                         cuisine="中餐厅", rating=4.6 - 0.1 * i) for i in range(2)]
        plan = TripScheduler("成都", 1, CHUNXI).schedule([Spot(name="太古里", lng=104.083, lat=30.655)], rs)
        self.assertEqual(sum("中餐馆" in it.title for it in _items(plan, "meal")), 2)


class TestBestPeriod(unittest.TestCase):
    """景点时段偏好：熊猫基地早去，锦里傍晚/夜间去。"""

    def test_morning_spot_starts_early(self):
        plan = TripScheduler("成都", 1, CHUNXI).schedule([Spot(**PANDA, best_period="morning")], [])
        spot = _items(plan, "spot")[0]
        self.assertLessEqual(_minutes(spot.time_slot), 8 * 60, spot.time_slot)
        self.assertGreaterEqual(_minutes(spot.time_slot), 7 * 60 + 30, "不能早于开门")
        self.assertLess(_minutes(plan.days[0].items[0].time_slot), 9 * 60, "出发时间应相应提前")

    def test_second_morning_spot_not_labelled_morning(self):
        # 两个宜早去的景点同一天：排到下午的那个不能再提示「上午人少」
        spots = [Spot(**PANDA, best_period="morning", duration_minutes=180),
                 Spot(**WUHOU, best_period="morning", duration_minutes=120)]
        plan = TripScheduler("成都", 1, CHUNXI).schedule(spots, [])
        late = [s for s in _items(plan, "spot") if _minutes(s.time_slot) >= 11 * 60]
        self.assertEqual(len(late), 1, [it.time_slot for it in _items(plan, "spot")])
        self.assertFalse(any(w.startswith("宜早去：") for w in late[0].warnings), late[0].warnings)
        self.assertTrue(any("改为稍晚前往" in w for w in late[0].warnings), late[0].warnings)

    def test_evening_spot_after_daytime_spots(self):
        spots = [Spot(**JINLI, best_period="evening", duration_minutes=90), Spot(**WUHOU, duration_minutes=150)]
        plan = TripScheduler("成都", 1, CHUNXI).schedule(spots, [])
        order = [it.title for it in _items(plan, "spot")]
        self.assertEqual(len(order), 2, order)
        self.assertIn("武侯祠", order[0])
        self.assertIn("锦里", order[1])
        jinli = _items(plan, "spot")[1]
        self.assertGreaterEqual(_minutes(jinli.time_slot), 18 * 60, jinli.time_slot)

    def test_dinner_near_evening_spot(self):
        rs = [Restaurant(name="春熙路餐厅", lng=104.0815, lat=30.6555, meal_type="meal"),
              Restaurant(name="锦里旁餐厅", lng=104.0520, lat=30.6420, meal_type="meal")]
        spots = [Spot(name="太古里", lng=104.0830, lat=30.6550, duration_minutes=60),
                 Spot(**JINLI, best_period="evening", duration_minutes=90)]
        plan = TripScheduler("成都", 1, CHUNXI).schedule(spots, rs)
        dinner = [it for it in _items(plan, "meal") if "晚餐" in it.title][0]
        self.assertIn("锦里旁餐厅", dinner.title)


class TestWalkingThreshold(unittest.TestCase):
    def test_short_distance_is_walking(self):
        _, desc = estimate_transit(1.0)  # 直线 1km ≈ 道路 1.3km
        self.assertIn("步行", desc)
        _, desc = estimate_transit(3.0)
        self.assertIn("打车", desc)


if __name__ == "__main__":
    unittest.main()
