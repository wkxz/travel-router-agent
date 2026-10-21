"""单元测试：验证日历闭馆智能避让、下午茶抑制、返程接驳与城市规则解耦。"""

import sys
import os
import unittest
from datetime import date

# 导入 .edgeone/agent-python 产物目录中的 pages_agents
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".edgeone", "agent-python")))

from pages_agents.engine.models import LocationPoint, Spot, Restaurant
from pages_agents.engine.scheduler import TripScheduler
from pages_agents.chat.index import _load_city_rules_text


class TestSchedulerV2(unittest.TestCase):

    def setUp(self):
        self.hotel = LocationPoint(name="南京新街口金陵饭店", lng=118.7844, lat=32.0416)
        self.station = LocationPoint(name="南京南站", lng=118.7983, lat=31.9696)

        # 构造景点（包含一个周一闭馆的南京博物院）
        self.spots = [
            Spot(name="南京博物院", lng=118.8286, lat=32.0422, duration_minutes=150, closed_days=[0]),
            Spot(name="中山陵音乐台", lng=118.8552, lat=32.0601, duration_minutes=60),
            Spot(name="陵园路梧桐大道", lng=118.8471, lat=32.0534, duration_minutes=45),
            Spot(name="鸡鸣寺", lng=118.7997, lat=32.0620, duration_minutes=60),
            Spot(name="玄武湖公园", lng=118.7981, lat=32.0722, duration_minutes=90),
            Spot(name="老门东", lng=118.7928, lat=32.0151, duration_minutes=90),
        ]

        self.restaurants = [
            Restaurant(name="小郑酥烧饼(老门东店)", lng=118.7929, lat=32.0152, meal_type="snack", cost=15.0),
            Restaurant(name="南京大牌档(德基店)", lng=118.7840, lat=32.0420, meal_type="meal", cost=75.0),
            Restaurant(name="江南灶·中餐厅", lng=118.7830, lat=32.0410, meal_type="meal", cost=180.0),
            Restaurant(name="回味鸭血粉丝汤", lng=118.7850, lat=32.0430, meal_type="meal", cost=28.0),
        ]

    def test_monday_closure_handling(self):
        """测试如果在周一出发（2026-10-26 恰好是周一），南京博物院能够避开周一闭馆"""
        # 2026-10-26 是周一
        scheduler = TripScheduler(
            city="南京",
            total_days=2,
            hotel=self.hotel,
            departure_point=self.station,
            start_date="2026-10-26",
        )
        plan = scheduler.schedule(self.spots, self.restaurants)
        self.assertEqual(plan.total_days, 2)

        # 检查 Day 1（周一）没有安排南京博物院，或者 Day 1 发出了闭馆避让对调
        day1_spot_names = [item.title for item in plan.days[0].items if item.category == "spot"]
        day2_spot_names = [item.title for item in plan.days[1].items if item.category == "spot"]

        # 南京博物院应该被调度在 Day 2（周二）
        self.assertTrue(any("南京博物院" in name for name in day2_spot_names), "南京博物院应在周二开放日进行游览")
        self.assertFalse(any("南京博物院" in name for name in day1_spot_names), "周一闭馆日不应安排南京博物院游览")

    def test_departure_point_transit(self):
        """测试最后一天终点无缝直连返程交通枢纽（南京南站）"""
        scheduler = TripScheduler(
            city="南京",
            total_days=2,
            hotel=self.hotel,
            departure_point=self.station,
            start_date="2026-10-25",
        )
        plan = scheduler.schedule(self.spots, self.restaurants)

        # Day 1 的末尾应该是返回酒店
        day1_last = plan.days[0].items[-1]
        self.assertIn("金陵饭店", day1_last.title)

        # Day 2 的末尾应该直通南京南站
        day2_last = plan.days[1].items[-1]
        self.assertIn("南京南站", day2_last.title)
        self.assertEqual(day2_last.category, "transit")

    def test_snack_time_suppression(self):
        """测试若下午游玩超时，下午茶小吃自动抑制，不发生与晚餐冲突"""
        # 构造耗时极长的下午景点
        heavy_spots = [
            Spot(name="景点A", lng=118.82, lat=32.04, duration_minutes=120),
            Spot(name="景点B(耗时下午)", lng=118.83, lat=32.05, duration_minutes=240),
            Spot(name="景点C(耗时傍晚)", lng=118.84, lat=32.06, duration_minutes=150),
        ]
        scheduler = TripScheduler(city="南京", total_days=1, hotel=self.hotel)
        plan = scheduler.schedule(heavy_spots, self.restaurants)

        # 游玩拖过 17:00，不应该再安排 category == 'snack'
        categories = [item.category for item in plan.days[0].items]
        self.assertNotIn("snack", categories, "下午严重超时时不应再塞入紧凑下午茶小吃")

    def test_city_rules_decoupling(self):
        """测试城市规则库解耦读取无误"""
        rules_text = _load_city_rules_text()
        self.assertIn("南京", rules_text)
        self.assertIn("南京博物院", rules_text)
        self.assertIn("武汉", rules_text)
        self.assertIn("西安", rules_text)


if __name__ == "__main__":
    unittest.main()
