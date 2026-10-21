"""测试 _tools.py 中的本地排程工具（无网络依赖部分）。"""
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents._tools import generate_trip_itinerary

def test_generate_itinerary_tool():
    city = "北京"
    total_days = 2
    hotel_name = "王府井希尔顿酒店"
    hotel_location = "116.4172,39.9142"

    spots = [
        {"name": "故宫博物院", "lng": 116.3970, "lat": 39.9180, "cost": 60, "duration_minutes": 180, "open_time": "08:30-17:00"},
        {"name": "天坛公园", "lng": 116.4108, "lat": 39.8822, "cost": 34, "duration_minutes": 120, "open_time": "06:30-21:00"},
        {"name": "颐和园", "lng": 116.2730, "lat": 39.9999, "cost": 30, "duration_minutes": 150, "open_time": "06:30-18:00"},
    ]

    foods = [
        {"name": "四季民福烤鸭店(故宫店)", "lng": 116.4020, "lat": 39.9150, "meal_type": "meal", "rating": 4.8, "cost": 150, "open_time": "10:30-21:30", "cuisine": "北京烤鸭"},
        {"name": "门框胡同百年卤煮(新街口店)", "lng": 116.3720, "lat": 39.9400, "meal_type": "snack", "rating": 4.8, "cost": 45, "open_time": "09:30-02:00", "cuisine": "北京传统小吃"},
        {"name": "南门涮肉(天坛店)", "lng": 116.4150, "lat": 39.8800, "meal_type": "meal", "rating": 4.7, "cost": 110, "open_time": "11:00-22:00", "cuisine": "老北京铜锅涮肉"},
    ]

    res_str = generate_trip_itinerary(
        city=city,
        total_days=total_days,
        hotel_name=hotel_name,
        hotel_location=hotel_location,
        spots_json=json.dumps(spots, ensure_ascii=False),
        foods_json=json.dumps(foods, ensure_ascii=False),
    )

    res = json.loads(res_str)
    assert res.get("success") is True, f"Failed: {res}"
    assert "北京 2 日游定制行程指南" in res.get("markdown")
    print("✅ generate_trip_itinerary 工具函数调用成功！")

if __name__ == "__main__":
    test_generate_itinerary_tool()
