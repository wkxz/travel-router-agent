"""排程引擎独立测试脚本：脱离网络与 LLM，验证算法正确性。

测试场景：
1. 成都 3 日游（5 个景点 + 6 家美食），检验 2-opt 消除折返与双轨餐饮切片。
2. 营业时间冲突检测（夜间排了早关门的餐厅）。
"""

import sys
import os

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import unittest

# 直接测源码（agents/ 以别名 app 挂载），不依赖 .edgeone 构建产物
import tests.fake_amap  # noqa: F401
from app.engine import (
    LocationPoint,
    Spot,
    Restaurant,
    TripScheduler,
    optimize_spots_route,
    render_markdown,
    render_dict,
)
from app.engine.geo import is_time_in_range


def test_optimizer():
    print("=== 测试 1：动线优化与消除折返 ===")
    hotel = LocationPoint(name="春熙路酒店", lng=104.0815, lat=30.6558)

    # 故意打乱顺序构造折返路线：
    # 春熙路 -> 都江堰(西北远端) -> 大慈寺(市中心近) -> 青城山(西北更远) -> 武侯祠(西南近)
    spots = [
        Spot(name="都江堰景区", lng=103.6120, lat=31.0028),
        Spot(name="大慈寺", lng=104.0820, lat=30.6565),
        Spot(name="青城山前山", lng=103.5710, lat=30.9002),
        Spot(name="武侯祠", lng=104.0505, lat=30.6433),
        Spot(name="杜甫草堂", lng=104.0289, lat=30.6601),
    ]

    opt_spots, opt_dist = optimize_spots_route(hotel, spots)
    opt_names = [s.name for s in opt_spots]
    print(f"优化前顺序: {[s.name for s in spots]}")
    print(f"优化后顺序: {opt_names}")
    print(f"优化后总里程: {opt_dist:.2f} km")

    # 验证优化后大慈寺/武侯祠等近处市中心点排在前面，远端都江堰/青城山排在一起
    assert opt_spots[0].name in ["大慈寺", "武侯祠"], "最靠近市中心起点的应优先访问"
    print("✅ 动线优化测试通过！\n")


def test_scheduler_and_render():
    print("=== 测试 2：3 天行程切分与餐饮双轨插入 ===")
    hotel = LocationPoint(name="成都春熙路美居酒店", lng=104.0815, lat=30.6558)

    spots = [
        Spot(name="成都大熊猫繁育研究基地", lng=104.1448, lat=30.7335, duration_minutes=180, cost=55.0, tags=["国宝", "自然"]),
        Spot(name="宽窄巷子", lng=104.0531, lat=30.6644, duration_minutes=90, cost=0.0, tags=["街区", "文化"]),
        Spot(name="武侯祠", lng=104.0505, lat=30.6433, duration_minutes=120, cost=50.0, tags=["三国", "古迹"]),
        Spot(name="杜甫草堂", lng=104.0289, lat=30.6601, duration_minutes=120, cost=50.0, tags=["诗歌", "园林"]),
        Spot(name="锦里古街", lng=104.0518, lat=30.6425, duration_minutes=90, cost=0.0, tags=["夜景", "步行街"]),
    ]

    restaurants = [
        # 正餐
        Restaurant(name="蜀九香火锅(百花店)", lng=104.0435, lat=30.6555, meal_type="meal", rating=4.8, cost=130.0, open_time="11:00-23:00", cuisine="四川火锅", recommend_reason="老牌牛油九宫格火锅"),
        Restaurant(name="陈麻婆豆腐(总店)", lng=104.0620, lat=30.6720, meal_type="meal", rating=4.6, cost=75.0, open_time="11:00-21:00", cuisine="传统川菜", recommend_reason="非遗老字号，麻婆豆腐首选"),
        Restaurant(name="陶德砂锅(春熙店)", lng=104.0810, lat=30.6530, meal_type="meal", rating=4.9, cost=65.0, open_time="10:30-21:30", cuisine="特色砂锅川菜", recommend_reason="口碑极高，砂锅排骨软烂入味"),
        # 特色小吃
        Restaurant(name="严太婆锅魁(人民中路店)", lng=104.0670, lat=30.6680, meal_type="snack", rating=4.7, cost=18.0, open_time="07:30-19:00", cuisine="成都小吃", recommend_reason="现打现烤，牛肉拌三丝锅魁一绝"),
        Restaurant(name="叶婆婆钵钵鸡(太古里店)", lng=104.0850, lat=30.6540, meal_type="snack", rating=4.8, cost=45.0, open_time="11:00-22:00", cuisine="乐山特色小吃", recommend_reason="红油藤椒双拼，本地人常去"),
        # 夜市
        Restaurant(name="玉林夜市/小吃街", lng=104.0665, lat=30.6278, meal_type="night_market", rating=4.7, cost=50.0, open_time="17:30-01:00", cuisine="夜市串串/小吃", recommend_reason="烟火气十足，串串香与烤脑花聚集"),
    ]

    scheduler = TripScheduler(city="成都", total_days=3, hotel=hotel)
    plan = scheduler.schedule(spots, restaurants)

    print(f"生成的总天数: {len(plan.days)}")
    for d in plan.days:
        print(f"第 {d.day_number} 天：{d.theme}（{d.total_spots} 景点，花费 ¥{d.total_cost:.0f}，通勤 {d.estimated_distance_km} km）")
        for item in d.items:
            print(f"   [{item.time_slot}] ({item.category}) {item.title}")

    # 验证每天都有正餐与行程
    assert len(plan.days) == 3
    assert plan.total_days == 3
    assert all(len(d.items) >= 4 for d in plan.days)

    # 渲染 Markdown 与 JSON
    md = render_markdown(plan)
    data = render_dict(plan)

    assert "成都 3 日游定制行程指南" in md
    assert len(data["days"]) == 3
    print("✅ 排程与渲染测试全部通过！\n")


def test_business_hour_check():
    print("=== 测试 3：营业时间与跨零点校验 ===")
    # 正常区间
    ok, msg = is_time_in_range("12:30", "11:00-21:00")
    assert ok, f"Expected True, got {ok}, {msg}"

    # 早于开门
    ok, msg = is_time_in_range("08:30", "11:00-21:00")
    assert not ok, "早于开门应返回 False"

    # 晚于打烊
    ok, msg = is_time_in_range("22:30", "11:00-21:00")
    assert not ok, "晚于打烊应返回 False"

    # 跨零点营业 (如 17:00-02:00)
    ok_night, _ = is_time_in_range("23:30", "17:00-02:00")
    assert ok_night, "跨零点 23:30 应在营业时间内"

    ok_early, _ = is_time_in_range("01:15", "17:00-02:00")
    assert ok_early, "跨零点凌晨 01:15 应在营业时间内"

    ok_closed, _ = is_time_in_range("10:00", "17:00-02:00")
    assert not ok_closed, "白天 10:00 应不在夜市营业时间内"

    print("✅ 营业时间判断逻辑完全准确！\n")


class TestSchedulerScript(unittest.TestCase):
    """上面三个函数原是 pytest 风格，用 unittest 运行时不会被收集；包一层让它们真正参与回归。"""

    def test_optimizer(self):
        test_optimizer()

    def test_scheduler_and_render(self):
        test_scheduler_and_render()

    def test_business_hour_check(self):
        test_business_hour_check()


if __name__ == "__main__":
    test_optimizer()
    test_scheduler_and_render()
    test_business_hour_check()
    print("🎉 纯 Python 排程引擎核心逻辑全部自测通过！")
