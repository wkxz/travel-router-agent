"""跨天菜系去重：铜锅涮肉的店，高德分类有的是「火锅店」、有的是「清真菜馆」，按分类认菜系会当成两种菜。

2026-10-30 第 4 轮测评（北京 3 天）：第 2 天午餐北门涮肉·铜锅涮肉（火锅店），第 3 天午餐清真·鸦儿李记·涮肉（清真菜馆），
模型点评指出「连续两天午餐重复安排铜锅涮肉」。菜系集合本来就跨天共用（_run_days 里只建一次），
漏掉是因为两家的键不同。店名、分类里带「涮肉」「火锅」的都归为火锅。

运行：./.venv/Scripts/python.exe -m unittest tests.test_cross_day_cuisine -v
"""

import unittest

from app.engine import LocationPoint, Restaurant, Spot, TripScheduler
from app.engine.scheduler import cuisine_key

HOTEL = LocationPoint(name="住处", lng=116.395, lat=39.935)


def _r(name, category, lng, rating):
    return Restaurant(name=name, lng=lng, lat=39.936, category=category, cuisine=category.split(";")[-1],
                      meal_type="meal", rating=rating, cost=90)


BEIMEN = _r("北门涮肉·铜锅涮肉(什刹海店)", "餐饮服务;中餐厅;火锅店", 116.3925, 4.6)
YAERLI = _r("清真·鸦儿李记·涮肉(什刹海店)", "餐饮服务;中餐厅;清真菜馆", 116.3930, 4.7)
JINGCAI = _r("京味家常菜馆", "餐饮服务;中餐厅;北京菜", 116.3935, 4.2)
LUCAI = _r("鲁菜小馆", "餐饮服务;中餐厅;山东菜(鲁菜)", 116.3940, 4.1)
OTHERS = [_r("淮扬小馆", "餐饮服务;中餐厅;江苏菜", 116.3945, 4.0), _r("粤式茶餐厅", "餐饮服务;中餐厅;广东菜(粤菜)", 116.3950, 4.0)]


class TestCrossDayCuisine(unittest.TestCase):
    def test_shuanrou_is_hotpot(self):
        self.assertEqual(cuisine_key(BEIMEN), cuisine_key(YAERLI))
        self.assertEqual(cuisine_key(BEIMEN), "火锅")
        self.assertEqual(cuisine_key(_r("小龙坎老火锅", "餐饮服务;中餐厅;火锅店", 0, 4)), "火锅")
        self.assertEqual(cuisine_key(JINGCAI), "北京菜")
        # 第 7 轮北京：午饭北门涮肉、晚饭「京门九涮(故宫店)」（高德分类只有「餐饮相关」），同一天两顿涮锅
        self.assertEqual(cuisine_key(Restaurant(name="京门九涮(故宫店)", lng=116.4, lat=39.9, category="餐饮相关",
                                                meal_type="meal")), "火锅")

    def test_no_shuanrou_two_days_running(self):
        spots = [Spot(name=f"什刹海片区{i}号", lng=116.392 + 0.001 * i, lat=39.936, duration_minutes=150,
                      open_time="08:00-21:00") for i in range(2)]
        plan = TripScheduler("北京", 2, HOTEL, start_date="2026-11-04", pace="relaxed").schedule(
            spots, [BEIMEN, YAERLI, JINGCAI, LUCAI, *OTHERS])  # 有别的店可选时不重复；只剩涮肉时照样排
        meals = [it.title for d in plan.days for it in d.items if it.category == "meal"]
        self.assertLessEqual(sum("涮肉" in t for t in meals), 1, meals)


if __name__ == "__main__":
    unittest.main()
